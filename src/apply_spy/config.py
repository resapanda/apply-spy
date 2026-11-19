"""Where apply-spy keeps its local data."""

import os
import secrets
from pathlib import Path

DATABASE_FILENAME = "apply_spy.db"
PORT = 8080  # the web UI always listens on this port (127.0.0.1 only)
# Files the app keeps next to the data that are not your data: the web UI's session
# state (active profile, filters) and the log written when started from the icon.
NICEGUI_DIRNAME = ".nicegui"
LOG_FILENAME = "apply-spy.log"


def _project_root() -> Path | None:
    """The project folder (the one with pyproject.toml), found from this file's own
    location, so it does not depend on which folder the app was started from.
    None if the code is installed somewhere else (not a project checkout)."""
    root = Path(__file__).resolve().parents[2]
    return root if (root / "pyproject.toml").is_file() else None


def standard_data_dir() -> Path:
    """The normal data folder, ignoring APPLY_SPY_DATA_DIR: <project>/data, wherever the
    app is started from. Only if the code is not in a project checkout (so there is no
    project folder) it is ~/apply-spy-data."""
    root = _project_root()
    return root / "data" if root else Path.home() / "apply-spy-data"


def default_data_dir() -> Path:
    """Where the data lives, without creating anything: APPLY_SPY_DATA_DIR if set
    (handy for experiments and tests), otherwise the standard folder."""
    override = os.environ.get("APPLY_SPY_DATA_DIR")
    return Path(override) if override else standard_data_dir()


def data_dir() -> Path:
    """Folder for the database and resume files (gitignored). Created if missing."""
    path = default_data_dir()
    path.mkdir(parents=True, exist_ok=True)
    return path


def database_url() -> str:
    return f"sqlite:///{data_dir() / DATABASE_FILENAME}"


def _read_secret(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except (FileNotFoundError, UnicodeDecodeError):
        return ""


def session_secret() -> str:
    """The secret that signs the browser cookie remembering your active profile.

    Made randomly the first time and kept in the data folder (readable only by you), so
    the cookie stays valid across restarts. An empty or damaged file is replaced. Losing
    the file only makes the browser forget its remembered choices; no data is affected.
    """
    path = data_dir() / NICEGUI_DIRNAME / "session-secret"
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = _read_secret(path)
    if existing:
        return existing

    # Write a complete private file under a temporary name, then put it in place in one
    # step, so nobody can ever see a half-written secret.
    candidate = secrets.token_urlsafe(32)
    temporary = path.with_name(f"{path.name}.{os.getpid()}.{secrets.token_hex(4)}.tmp")
    try:
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            file.write(candidate)
        try:
            os.link(temporary, path)  # only succeeds if no file is there yet
        except FileExistsError:
            if not _read_secret(path):  # a leftover empty/damaged file: replace it
                os.replace(temporary, path)
            # otherwise another start won the race; use its secret below
    finally:
        temporary.unlink(missing_ok=True)
    return _read_secret(path) or candidate
