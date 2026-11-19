"""Where apply-spy keeps its local data."""

import os
from pathlib import Path

DATABASE_FILENAME = "apply_spy.db"


def data_dir() -> Path:
    """Folder for the database and resume files (gitignored).

    Override with the APPLY_SPY_DATA_DIR environment variable.
    """
    override = os.environ.get("APPLY_SPY_DATA_DIR")
    path = Path(override) if override else Path.cwd() / "data"
    path.mkdir(parents=True, exist_ok=True)
    return path


def database_url() -> str:
    return f"sqlite:///{data_dir() / DATABASE_FILENAME}"
