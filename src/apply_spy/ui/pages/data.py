"""The Data page: back up your data and check that the database is healthy."""

from nicegui import ui

from apply_spy.backup import BackupError, check_database, create_backup
from apply_spy.config import DATABASE_FILENAME, data_dir
from apply_spy.ui.layout import app_shell


@ui.page("/data")
def data_page() -> None:
    with app_shell("data"):
        _build()


def _build() -> None:
    with ui.column().classes("gap-1"):
        ui.label("Your data").classes("as-h1")
        ui.label("Everything lives on this computer.").classes("as-sub")

    # --- Back up ---------------------------------------------------------------------
    with ui.element("div").classes("as-card w-full"):
        ui.label("Back up").classes("as-title mb-1")
        ui.label(
            "A backup is one zip file with all profiles, applications and resumes. Keep a "
            "copy somewhere safe, such as a USB stick or a cloud folder."
        ).classes("as-muted max-w-2xl")
        saved = ui.label("").classes("as-muted").props("data-testid=backup-saved")

        def back_up_now() -> None:
            try:
                archive, _ = create_backup(data_dir())
            except BackupError as error:
                ui.notify(str(error), type="negative")
                return
            saved.set_text(f"A copy was also saved on this computer: {archive}")
            ui.download.file(archive, archive.name, "application/zip")
            ui.notify("Backup created", type="positive")
            health.refresh()

        ui.button("Back up now", icon="backup", on_click=back_up_now).props(
            "unelevated no-caps color=primary data-testid=backup-button"
        ).classes("mt-3")

    # --- Database check ----------------------------------------------------------------
    with ui.element("div").classes("as-card w-full"):
        ui.label("Database check").classes("as-title mb-1")

        @ui.refreshable
        def health() -> None:
            try:
                result = check_database(data_dir() / DATABASE_FILENAME)
            except BackupError as error:
                ui.label(str(error))
                return
            for table, count in result.counts.items():
                ui.label(f"{table}: {count}")
            for table in result.missing_tables:
                ui.label(f"Missing table: {table}").classes("text-negative")
            ok = result.ok
            ui.label(
                "Integrity and foreign keys: ok" if ok else "Problems found: see the terminal"
            ).classes("text-positive" if ok else "text-negative").props("data-testid=health")

        health()
        ui.button("Check again", on_click=health.refresh).props("flat no-caps")

    # --- Restore -----------------------------------------------------------------------
    with ui.element("div").classes("as-card w-full"):
        ui.label("Restoring a backup").classes("as-title mb-1")
        ui.label(
            "Restoring replaces your data, so it is done from a terminal with this app "
            "stopped. If data already exists you are asked to confirm, and the old data is "
            "moved aside, not deleted."
        ).classes("as-muted max-w-2xl")
        ui.code("uv run apply-spy restore PATH/TO/backup.zip", language="bash")
