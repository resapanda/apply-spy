"""Local web UI (NiceGUI). No business logic here: the pages call the service layer.

Pages live in apply_spy/ui/pages/; importing them registers their routes. The frame
around every page (sidebar and top bar) is in layout.py, the look in theme.py/theme.css.
"""

from nicegui import ui

from apply_spy.config import PORT, session_secret
from apply_spy.ui.pages import (  # noqa: F401  (importing a page registers its route)
    application_detail,
    applications,
    dashboard,
    data,
    follow_ups,
    profiles,
)
from apply_spy.ui.theme import install_theme

install_theme()


def run() -> None:
    # host=127.0.0.1: only this computer can reach the app (your data stays private).
    # storage_secret signs the cookie that remembers the active profile in this browser.
    # It is generated once and kept in the data folder, so it survives restarts.
    ui.run(
        title="apply-spy",
        host="127.0.0.1",
        port=PORT,
        reload=False,
        show=True,
        storage_secret=session_secret(),
    )
