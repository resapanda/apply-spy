import json
import sqlite3
import zipfile
from datetime import datetime

import pytest

from apply_spy.backup import (
    BackupError,
    DataFolderNotEmpty,
    InvalidBackup,
    check_database,
    create_backup,
    restore_backup,
)
from apply_spy.cli import main
from apply_spy.db import init_db, make_engine, session_scope
from apply_spy.profiles import create_profile, list_profiles
from apply_spy.tracker import change_status, create_application, list_applications


def make_data_folder(folder, with_resumes=True):
    """A data folder with made-up profiles, applications and resume files."""
    folder.mkdir(parents=True, exist_ok=True)
    engine = make_engine(f"sqlite:///{folder / 'apply_spy.db'}")
    init_db(engine)
    with session_scope(engine) as session:
        one = create_profile(session, "Sample One").id
        two = create_profile(session, "Sample Two").id
        a = create_application(session, one, "Acme", "Engineer", notes="a, b\nc").id
        create_application(session, one, "Globex", "Developer")
        create_application(session, two, "Initech", "Analyst")
        change_status(session, one, a, "Interview")
    engine.dispose()
    if with_resumes:
        (folder / "resumes").mkdir()
        (folder / "resumes" / "sample_resume.pdf").write_bytes(b"%PDF made-up bytes \x00\xff")
        (folder / "resumes" / "sub").mkdir()
        (folder / "resumes" / "sub" / "v2.txt").write_text("made-up resume", encoding="utf-8")
    return folder


def read_everything(folder):
    """Profiles, applications and history as plain data, for comparing before and after."""
    engine = make_engine(f"sqlite:///{folder / 'apply_spy.db'}")
    try:
        with session_scope(engine) as session:
            return {
                p.name: sorted(
                    (a.company, a.title, a.status, a.notes)
                    for a in list_applications(session, p.id)
                )
                for p in list_profiles(session)
            }
    finally:
        engine.dispose()


def zip_names(archive):
    with zipfile.ZipFile(archive) as zf:
        return sorted(zf.namelist())


# --- Backup ---------------------------------------------------------------


def test_backup_is_a_timestamped_zip_with_database_resumes_and_manifest(tmp_path):
    data = make_data_folder(tmp_path / "data")

    archive, _ = create_backup(data, now=datetime(2026, 11, 19, 8, 30, 5))

    assert archive.parent == data / "backups"
    assert archive.name == "apply-spy-backup-20261119-083005.zip"
    assert zip_names(archive) == [
        "apply_spy.db",
        "manifest.json",
        "resumes/sample_resume.pdf",
        "resumes/sub/v2.txt",
    ]


def test_backup_manifest_records_row_counts(tmp_path):
    data = make_data_folder(tmp_path / "data")
    archive, _ = create_backup(data)
    with zipfile.ZipFile(archive) as zf:
        manifest = json.loads(zf.read("manifest.json"))
    assert manifest["counts"] == {"profiles": 2, "applications": 3, "status_events": 4}
    assert manifest["resume_files"] == 2


def test_backups_do_not_contain_earlier_backups(tmp_path):
    data = make_data_folder(tmp_path / "data")
    first, _ = create_backup(data, now=datetime(2026, 11, 19, 8, 0, 0))
    second, _ = create_backup(data, now=datetime(2026, 11, 19, 9, 0, 0))
    assert first != second
    assert not any("backups" in name or name.endswith(".zip") for name in zip_names(second))


def test_two_backups_in_the_same_second_do_not_overwrite_each_other(tmp_path):
    data = make_data_folder(tmp_path / "data")
    same = datetime(2026, 11, 19, 8, 0, 0)
    first, _ = create_backup(data, now=same)
    second, _ = create_backup(data, now=same)
    assert first != second and first.exists() and second.exists()


def test_backup_can_go_to_another_folder_and_leaves_no_partial_file(tmp_path):
    data = make_data_folder(tmp_path / "data")
    elsewhere = tmp_path / "usb-stick"
    archive, _ = create_backup(data, elsewhere)
    assert archive.parent == elsewhere
    assert [p.name for p in elsewhere.iterdir()] == [archive.name]


def test_backup_without_a_database_is_an_error(tmp_path):
    with pytest.raises(BackupError):
        create_backup(tmp_path / "empty")


def test_backup_works_without_a_resumes_folder(tmp_path):
    data = make_data_folder(tmp_path / "data", with_resumes=False)
    archive, _ = create_backup(data)
    assert zip_names(archive) == ["apply_spy.db", "manifest.json"]


def test_backup_is_a_snapshot_even_while_a_connection_is_open(tmp_path):
    data = make_data_folder(tmp_path / "data")
    engine = make_engine(f"sqlite:///{data / 'apply_spy.db'}")
    try:
        with session_scope(engine) as session:
            create_profile(session, "Sample Three")
        archive, result = create_backup(data)  # engine still open, like a running app
    finally:
        engine.dispose()
    assert result.counts["profiles"] == 3
    assert archive.exists()


# --- Restore ----------------------------------------------------------------


def test_restore_into_an_empty_folder_brings_back_everything(tmp_path):
    original = make_data_folder(tmp_path / "data")
    before = read_everything(original)
    archive, _ = create_backup(original)

    fresh = tmp_path / "new-laptop" / "data"
    result = restore_backup(archive, fresh)

    assert read_everything(fresh) == before
    assert set(before) == {"Sample One", "Sample Two"}
    assert (fresh / "resumes" / "sample_resume.pdf").read_bytes() == b"%PDF made-up bytes \x00\xff"
    assert (fresh / "resumes" / "sub" / "v2.txt").read_text(encoding="utf-8") == "made-up resume"
    assert result.counts == {"profiles": 2, "applications": 3, "status_events": 4}


def test_self_check_after_restore_reports_the_same_row_counts_as_before(tmp_path):
    original = make_data_folder(tmp_path / "data")
    counts_before = check_database(original / "apply_spy.db").counts
    archive, _ = create_backup(original)

    fresh = tmp_path / "fresh"
    restore_backup(archive, fresh)

    after = check_database(fresh / "apply_spy.db")
    assert after.ok
    assert after.counts == counts_before


def test_restore_refuses_to_overwrite_existing_data_without_confirmation(tmp_path):
    original = make_data_folder(tmp_path / "data")
    archive, _ = create_backup(original)
    target = make_data_folder(tmp_path / "target")
    (target / "resumes" / "sample_resume.pdf").write_bytes(b"keep me")
    before = read_everything(target)

    with pytest.raises(DataFolderNotEmpty):
        restore_backup(archive, target)

    assert read_everything(target) == before
    assert (target / "resumes" / "sample_resume.pdf").read_bytes() == b"keep me"


def test_confirmed_overwrite_replaces_data_and_keeps_the_old_copy(tmp_path):
    original = make_data_folder(tmp_path / "data")
    archive, _ = create_backup(original)

    target = tmp_path / "target"
    target.mkdir()
    engine = make_engine(f"sqlite:///{target / 'apply_spy.db'}")
    init_db(engine)
    with session_scope(engine) as session:
        create_profile(session, "Someone Else")
    engine.dispose()

    restore_backup(archive, target, overwrite=True)

    assert set(read_everything(target)) == {"Sample One", "Sample Two"}
    [kept] = [p for p in tmp_path.iterdir() if p.name.startswith("target.before-restore-")]
    assert set(read_everything(kept)) == {"Someone Else"}  # the old data was moved, not deleted


def test_saved_backups_do_not_count_as_data_and_survive_a_restore(tmp_path):
    data = make_data_folder(tmp_path / "data")
    archive, _ = create_backup(data)  # now data/ has live data AND data/backups/

    restore_backup(archive, data, overwrite=True)

    assert archive.exists()  # the backups folder stays where it was
    assert set(read_everything(data)) == {"Sample One", "Sample Two"}


def test_a_folder_holding_only_backups_restores_without_confirmation(tmp_path):
    source = make_data_folder(tmp_path / "data")
    archive, _ = create_backup(source)
    target = tmp_path / "target"
    (target / "backups").mkdir(parents=True)

    restore_backup(archive, target)  # no overwrite needed

    assert set(read_everything(target)) == {"Sample One", "Sample Two"}


# --- Bad archives never touch the data ---------------------------------------


def _write_zip(path, entries):
    with zipfile.ZipFile(path, "w") as zf:
        for name, content in entries.items():
            zf.writestr(name, content)
    return path


def _good_entries(tmp_path):
    archive, _ = create_backup(make_data_folder(tmp_path / "src"))
    with zipfile.ZipFile(archive) as zf:
        return {name: zf.read(name) for name in zf.namelist()}


def test_not_a_zip_is_rejected(tmp_path):
    bad = tmp_path / "bad.zip"
    bad.write_text("hello", encoding="utf-8")
    with pytest.raises(InvalidBackup):
        restore_backup(bad, tmp_path / "target")
    assert not (tmp_path / "target" / "apply_spy.db").exists()


def test_missing_file_is_rejected(tmp_path):
    with pytest.raises(InvalidBackup):
        restore_backup(tmp_path / "nope.zip", tmp_path / "target")


def test_archive_without_manifest_or_database_is_rejected(tmp_path):
    good = _good_entries(tmp_path)
    for missing in ("manifest.json", "apply_spy.db"):
        entries = {k: v for k, v in good.items() if k != missing}
        bad = _write_zip(tmp_path / f"no-{missing}.zip", entries)
        with pytest.raises(InvalidBackup):
            restore_backup(bad, tmp_path / "target")


@pytest.mark.parametrize(
    "evil", ["../escape.txt", "/etc/escape.txt", "resumes/../../escape.txt", "other/file.txt"]
)
def test_archive_entries_that_could_escape_the_folder_are_rejected(tmp_path, evil):
    entries = _good_entries(tmp_path)
    entries[evil] = b"gotcha"
    bad = _write_zip(tmp_path / "evil.zip", entries)

    with pytest.raises(InvalidBackup):
        restore_backup(bad, tmp_path / "target")

    assert not (tmp_path / "escape.txt").exists()
    assert not (tmp_path / "target" / "apply_spy.db").exists()


def test_unsupported_format_version_is_rejected(tmp_path):
    entries = _good_entries(tmp_path)
    manifest = json.loads(entries["manifest.json"])
    manifest["format_version"] = 999
    entries["manifest.json"] = json.dumps(manifest).encode()
    with pytest.raises(InvalidBackup):
        restore_backup(_write_zip(tmp_path / "v999.zip", entries), tmp_path / "target")


def test_row_counts_that_do_not_match_the_manifest_are_rejected(tmp_path):
    entries = _good_entries(tmp_path)
    manifest = json.loads(entries["manifest.json"])
    manifest["counts"]["applications"] += 1
    entries["manifest.json"] = json.dumps(manifest).encode()

    target = make_data_folder(tmp_path / "target")
    before = read_everything(target)
    with pytest.raises(InvalidBackup):
        restore_backup(_write_zip(tmp_path / "off.zip", entries), target, overwrite=True)
    assert read_everything(target) == before  # untouched, even with overwrite allowed
    assert not [p for p in tmp_path.iterdir() if "before-restore" in p.name]


def test_a_corrupt_database_inside_the_archive_is_rejected(tmp_path):
    entries = _good_entries(tmp_path)
    entries["apply_spy.db"] = b"this is not a database" * 100
    target = make_data_folder(tmp_path / "target")
    before = read_everything(target)
    with pytest.raises(InvalidBackup):
        restore_backup(_write_zip(tmp_path / "corrupt.zip", entries), target, overwrite=True)
    assert read_everything(target) == before


# --- Self-check ----------------------------------------------------------------


def test_check_reports_counts_and_health(tmp_path):
    data = make_data_folder(tmp_path / "data")
    result = check_database(data / "apply_spy.db")
    assert result.ok
    assert result.counts == {"profiles": 2, "applications": 3, "status_events": 4}


def test_check_flags_a_missing_table(tmp_path):
    db = tmp_path / "old.db"
    sqlite3.connect(db).close()
    result = check_database(db)
    assert not result.ok
    assert "profiles" in result.missing_tables


def test_check_flags_a_file_that_is_not_a_database(tmp_path):
    junk = tmp_path / "junk.db"
    junk.write_bytes(b"not a database at all" * 50)
    assert not check_database(junk).ok


# --- CLI ----------------------------------------------------------------------------


def test_cli_backup_check_and_restore_round_trip(tmp_path, monkeypatch, capsys):
    data = make_data_folder(tmp_path / "data")
    before = read_everything(data)
    monkeypatch.setenv("APPLY_SPY_DATA_DIR", str(data))

    assert main(["backup", "--output", str(tmp_path / "saved")]) == 0
    [archive] = (tmp_path / "saved").glob("*.zip")
    assert main(["check"]) == 0
    assert "applications: 3" in capsys.readouterr().out

    fresh = tmp_path / "fresh"
    monkeypatch.setenv("APPLY_SPY_DATA_DIR", str(fresh))
    assert main(["restore", str(archive)]) == 0  # empty folder: no question asked
    assert read_everything(fresh) == before
    assert "applications: 3" in capsys.readouterr().out


def test_cli_restore_asks_before_overwriting_and_cancel_changes_nothing(
    tmp_path, monkeypatch, capsys
):
    source = make_data_folder(tmp_path / "source")
    archive, _ = create_backup(source)
    target = make_data_folder(tmp_path / "target")
    before = read_everything(target)
    monkeypatch.setenv("APPLY_SPY_DATA_DIR", str(target))

    monkeypatch.setattr("builtins.input", lambda _prompt: "no")
    assert main(["restore", str(archive)]) == 1
    assert read_everything(target) == before
    assert "Cancelled" in capsys.readouterr().out

    monkeypatch.setattr("builtins.input", lambda _prompt: "yes")
    assert main(["restore", str(archive)]) == 0
    assert set(read_everything(target)) == {"Sample One", "Sample Two"}


def test_cli_restore_does_not_create_an_empty_database_first(tmp_path, monkeypatch):
    """A restore into a brand-new folder must not ask 'overwrite?' about an empty database."""
    source = make_data_folder(tmp_path / "source")
    archive, _ = create_backup(source)
    fresh = tmp_path / "fresh"
    monkeypatch.setenv("APPLY_SPY_DATA_DIR", str(fresh))
    monkeypatch.setattr("builtins.input", lambda _prompt: pytest.fail("should not ask"))
    assert main(["restore", str(archive)]) == 0


def test_cli_errors_are_friendly(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("APPLY_SPY_DATA_DIR", str(tmp_path / "empty"))
    assert main(["backup"]) == 1
    assert main(["restore", str(tmp_path / "missing.zip")]) == 1
    assert main(["check"]) == 1
    out = capsys.readouterr().out
    assert out.count("Error:") == 3
