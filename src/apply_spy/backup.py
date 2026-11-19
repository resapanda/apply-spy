"""Backup, restore and self-check of the local data folder.

A backup is one .zip with a consistent copy of the database, the resumes/ folder
and a manifest that records how many rows each table had. Restoring checks the
archive first and only then touches the data folder.

Stop the web UI before restoring.
"""

import json
import os
import shutil
import sqlite3
import tempfile
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path, PurePosixPath

from apply_spy.config import DATABASE_FILENAME, LOG_FILENAME, NICEGUI_DIRNAME
from apply_spy.db.models import Base

RESUMES_DIR = "resumes"
BACKUPS_DIR = "backups"  # default place for archives; never part of an archive
# Things in the data folder that are not your data: they never count as "existing data"
# and a restore leaves them where they are.
NOT_DATA = (BACKUPS_DIR, NICEGUI_DIRNAME, LOG_FILENAME)
MANIFEST = "manifest.json"
FORMAT_VERSION = 1


class BackupError(Exception):
    """Something stopped the backup or restore. Nothing was changed."""


class DataFolderNotEmpty(BackupError):
    """The data folder already has data and overwriting was not confirmed."""


class InvalidBackup(BackupError):
    """The archive is not a usable apply-spy backup."""


@dataclass
class CheckResult:
    """What the self-check found: row counts per table and database health."""

    counts: dict[str, int] = field(default_factory=dict)
    missing_tables: list[str] = field(default_factory=list)
    integrity_ok: bool = True
    foreign_keys_ok: bool = True

    @property
    def ok(self) -> bool:
        return self.integrity_ok and self.foreign_keys_ok and not self.missing_tables


def check_database(db_path: Path) -> CheckResult:
    """Open the database read-only and report row counts and health."""
    if not db_path.exists():
        raise BackupError(f"No database found at {db_path}.")
    result = CheckResult()
    connection = sqlite3.connect(f"{db_path.resolve().as_uri()}?mode=ro", uri=True)
    try:
        try:
            result.integrity_ok = connection.execute("PRAGMA integrity_check").fetchall() == [
                ("ok",)
            ]
            result.foreign_keys_ok = connection.execute("PRAGMA foreign_key_check").fetchall() == []
            existing = {name for (name,) in connection.execute("SELECT name FROM sqlite_master")}
            for table in Base.metadata.sorted_tables:  # our own table names, not user input
                if table.name not in existing:
                    result.missing_tables.append(table.name)
                else:
                    (count,) = connection.execute(f'SELECT COUNT(*) FROM "{table.name}"').fetchone()
                    result.counts[table.name] = count
        except sqlite3.DatabaseError:
            # A file that is not a database at all (or is badly damaged).
            result.integrity_ok = False
    finally:
        connection.close()
    return result


def has_live_data(data_dir: Path) -> bool:
    """True if the data folder holds anything other than saved backups and the app's
    own housekeeping files (see NOT_DATA)."""
    return data_dir.exists() and any(p.name not in NOT_DATA for p in data_dir.iterdir())


def _unique_path(path: Path) -> Path:
    """path, or path with -2, -3, ... added before the suffix if it is taken."""
    candidate, n = path, 2
    while candidate.exists():
        candidate = path.with_name(f"{path.stem}-{n}{path.suffix}")
        n += 1
    return candidate


def create_backup(
    data_dir: Path, output_dir: Path | None = None, now: datetime | None = None
) -> tuple[Path, CheckResult]:
    """Write a timestamped archive of the database and resume files.

    The database is copied with SQLite's own backup feature, so the copy is
    consistent even if the app is running. Returns the archive path and the
    row counts that were recorded in it.
    """
    db_path = data_dir / DATABASE_FILENAME
    if not db_path.exists():
        raise BackupError(f"No database found in {data_dir}. Nothing to back up.")
    destination = output_dir or data_dir / BACKUPS_DIR
    destination.mkdir(parents=True, exist_ok=True)
    now = now or datetime.now()
    archive = _unique_path(destination / f"apply-spy-backup-{now:%Y%m%d-%H%M%S}.zip")

    with tempfile.TemporaryDirectory() as scratch:
        snapshot = Path(scratch) / DATABASE_FILENAME
        source = sqlite3.connect(db_path)
        target = sqlite3.connect(snapshot)
        try:
            source.backup(target)
        finally:
            target.close()
            source.close()
        result = check_database(snapshot)
        if not result.ok:
            raise BackupError(
                "The database did not pass its integrity check, so no backup was made. "
                "Run 'apply-spy check' for details."
            )

        resume_files = []
        resumes = data_dir / RESUMES_DIR
        if resumes.is_dir():
            resume_files = sorted(
                p for p in resumes.rglob("*") if p.is_file() and not p.is_symlink()
            )
        manifest = {
            "format_version": FORMAT_VERSION,
            "created_at": now.isoformat(timespec="seconds"),
            "counts": result.counts,
            "resume_files": len(resume_files),
        }

        # Write to a temporary name first, so a half-written file never looks like a backup.
        partial = archive.with_name(archive.name + ".partial")
        try:
            with zipfile.ZipFile(partial, "w", zipfile.ZIP_DEFLATED) as zf:
                zf.writestr(MANIFEST, json.dumps(manifest, indent=2))
                zf.write(snapshot, DATABASE_FILENAME)
                for file in resume_files:
                    zf.write(file, PurePosixPath(RESUMES_DIR, *file.relative_to(resumes).parts))
            os.replace(partial, archive)
        finally:
            partial.unlink(missing_ok=True)
    return archive, result


def _safe_member(name: str) -> bool:
    """Only the files we write ourselves, with no way out of the target folder."""
    if "\\" in name or name.startswith("/"):
        return False
    parts = PurePosixPath(name).parts
    if not parts or ".." in parts or "." in parts:
        return False
    if name in (MANIFEST, DATABASE_FILENAME):
        return True
    return parts[0] == RESUMES_DIR and len(parts) > 1


def _extract_checked(archive: Path, scratch: Path) -> tuple[dict, CheckResult]:
    """Unpack into scratch and verify everything. Raises InvalidBackup on any problem."""
    if not zipfile.is_zipfile(archive):
        raise InvalidBackup(f"{archive} is not a backup archive (not a .zip file).")
    with zipfile.ZipFile(archive) as zf:
        if zf.testzip() is not None:
            raise InvalidBackup("The archive is damaged (a file inside failed its checksum).")
        members = [i for i in zf.infolist() if not i.is_dir()]
        for info in members:
            is_symlink = (info.external_attr >> 16) & 0o170000 == 0o120000
            if is_symlink or not _safe_member(info.filename):
                raise InvalidBackup(f"The archive contains an unexpected entry: {info.filename!r}.")
        names = {i.filename for i in members}
        if MANIFEST not in names or DATABASE_FILENAME not in names:
            raise InvalidBackup("The archive is missing its manifest or database.")
        try:
            manifest = json.loads(zf.read(MANIFEST))
        except ValueError as error:
            raise InvalidBackup("The archive's manifest cannot be read.") from error
        if not isinstance(manifest, dict) or manifest.get("format_version") != FORMAT_VERSION:
            raise InvalidBackup("This backup was made by an unsupported version of apply-spy.")
        for info in members:
            target = scratch / info.filename
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as source, open(target, "wb") as out:
                shutil.copyfileobj(source, out)

    result = check_database(scratch / DATABASE_FILENAME)
    if not result.ok:
        raise InvalidBackup("The database inside the backup failed its integrity check.")
    if result.counts != manifest.get("counts"):
        raise InvalidBackup("The row counts in the backup do not match its manifest.")
    return manifest, result


def restore_backup(archive: Path, data_dir: Path, *, overwrite: bool = False) -> CheckResult:
    """Restore an archive into the data folder and return the restored row counts.

    The archive is checked completely before the data folder is touched. If the
    folder already holds data, restoring needs overwrite=True (the caller must get
    the user's explicit confirmation); the old data is then moved to a folder next
    to it called <data>.before-restore-<time>, not deleted.
    """
    if not archive.is_file():
        raise InvalidBackup(f"No such file: {archive}")
    live = has_live_data(data_dir)
    if live and not overwrite:
        raise DataFolderNotEmpty(
            f"{data_dir} already has data. Restoring would replace it; confirm to continue."
        )
    data_dir.mkdir(parents=True, exist_ok=True)

    scratch = Path(tempfile.mkdtemp(prefix=".restore-", dir=data_dir))
    moved_aside: list[tuple[Path, Path]] = []
    try:
        _, result = _extract_checked(archive, scratch)

        if live:
            aside = _unique_path(
                data_dir.parent / f"{data_dir.name}.before-restore-{datetime.now():%Y%m%d-%H%M%S}"
            )
            aside.mkdir()
            for item in list(data_dir.iterdir()):
                if item.name in (*NOT_DATA, scratch.name):
                    continue
                shutil.move(str(item), str(aside / item.name))
                moved_aside.append((aside / item.name, item))

        try:
            shutil.move(str(scratch / DATABASE_FILENAME), str(data_dir / DATABASE_FILENAME))
            if (scratch / RESUMES_DIR).exists():
                shutil.move(str(scratch / RESUMES_DIR), str(data_dir / RESUMES_DIR))
        except Exception:
            # Put the old data back so a failed restore leaves things as they were.
            for kept, original in moved_aside:
                if not original.exists():
                    shutil.move(str(kept), str(original))
            raise
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    return result
