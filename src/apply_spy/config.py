"""Where apply-spy keeps its local data."""

import os
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
