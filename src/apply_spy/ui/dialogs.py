"""Pop-up dialogs that more than one page uses. No business logic: they call the
tracker service and report its errors."""

from datetime import date

from nicegui import app, ui

from apply_spy.db import session_scope
from apply_spy.tracker import (
    CLOSED_STATUSES,
    ApplicationNotFound,
    InvalidApplication,
    create_application,
    get_application,
    list_applications,
    set_follow_up,
)
from apply_spy.ui.state import engine
from apply_spy.ui.theme import follow_up_text

# Shown over the page when the user quits. It is plain text only (no user data), covers
# everything including NiceGUI's own "Connection lost" box, and stops reconnect attempts.
STOPPED_PAGE_JS = """
(() => {
  try { window.socket.io.reconnection(false); } catch (error) {}
  document.title = "apply-spy stopped";
  const box = document.createElement("div");
  box.setAttribute("data-testid", "stopped-overlay");
  box.style.cssText = "position:fixed;inset:0;z-index:2147483647;background:Canvas;" +
    "color:CanvasText;font-family:system-ui,sans-serif;display:flex;flex-direction:column;" +
    "align-items:center;justify-content:center;text-align:center;";
  box.innerHTML = "<div style='font-size:28px;font-weight:600;margin-bottom:12px'>" +
    "apply-spy has stopped</div>" +
    "<div style='font-size:16px;margin:4px 0'>" +
    "Your data is saved. You can close this tab.</div>" +
    "<div style='font-size:16px;margin:4px 0'>" +
    "To use apply-spy again, click the apply-spy icon.</div>";
  document.body.appendChild(box);
})();
"""


def confirm_quit() -> None:
    with ui.dialog() as dialog, ui.card():
        ui.label("Quit apply-spy?").classes("text-lg font-bold")
        ui.label("Your data is saved. Click the apply-spy icon to start it again.")

        async def quit_app() -> None:
            dialog.close()
            # Cover the page with a clear message BEFORE the server goes away. Once it is
            # gone it cannot send anything, and the browser would only show a frozen page
            # with "Connection lost".
            try:
                await ui.run_javascript(STOPPED_PAGE_JS, timeout=3)
            except TimeoutError:
                pass  # no browser answered; shut down anyway
            app.shutdown()

        with ui.row():
            ui.button("Cancel", on_click=dialog.close).props("flat")
            ui.button("Quit", color="negative", on_click=quit_app).props("data-testid=confirm-quit")
    dialog.open()


def follow_up_dialog(profile_id: int, application_id: int, on_saved) -> None:
    """Set, change or clear one application's follow-up, from a calendar picker.

    The date comes from a picker, so it cannot be mistyped.
    """
    try:
        with session_scope(engine) as session:
            application = get_application(session, profile_id, application_id)
            company = application.company
            current_date = application.follow_up_date
            current_label = application.follow_up_label
    except ApplicationNotFound as error:
        ui.notify(str(error), type="negative")
        return

    with ui.dialog() as dialog, ui.card():
        ui.label(f"Follow-up for {company}").classes("text-lg font-bold")
        picked = ui.date(value=(current_date or date.today()).isoformat())
        label = ui.input("Label (optional), for example OA due", value=current_label or "").props(
            "data-testid=follow-up-label"
        )

        def save(new_date, new_label, message) -> None:
            try:
                with session_scope(engine) as session:
                    set_follow_up(session, profile_id, application_id, new_date, new_label)
            except (InvalidApplication, ApplicationNotFound) as error:
                ui.notify(str(error), type="negative")
                return
            dialog.close()
            ui.notify(message, type="positive")
            on_saved()

        with ui.row():
            ui.button("Cancel", on_click=dialog.close).props("flat")
            if current_date is not None:
                ui.button("Clear", on_click=lambda: save(None, None, "Follow-up cleared")).props(
                    "flat color=negative"
                )
            ui.button(
                "Save", on_click=lambda: save(picked.value, label.value, "Follow-up saved")
            ).props("data-testid=follow-up-save")
    dialog.open()


def add_follow_up_dialog(profile_id: int, on_saved) -> None:
    """Pick an application and a date (and an optional label) in one step."""
    with session_scope(engine) as session:
        # Closed applications are left out: their follow-ups never show on the dashboard.
        options = {
            a.id: f"{a.company}: {a.title}"
            for a in list_applications(session, profile_id, sort_by="company", descending=False)
            if a.status not in CLOSED_STATUSES
        }
        existing = {
            a.id: follow_up_text(a.follow_up_date, a.follow_up_label)
            for a in list_applications(session, profile_id)
            if a.follow_up_date is not None
        }

    with ui.dialog() as dialog, ui.card().classes("w-[420px] max-w-full"):
        ui.label("Add follow-up").classes("text-lg font-bold")
        if not options:
            ui.label("Add an application first, then you can set a follow-up for it.")
            ui.button("Close", on_click=dialog.close).props("flat")
            dialog.open()
            return

        chosen = ui.select(options, with_input=True, label="Application").classes("w-full")
        chosen.props("data-testid=add-follow-up-application")
        warning = ui.label("").classes("text-sm text-warning")

        def on_choose() -> None:
            current = existing.get(chosen.value)
            warning.set_text(
                f"This will replace its current follow-up ({current})." if current else ""
            )

        chosen.on_value_change(on_choose)
        picked = ui.date(value=date.today().isoformat())
        label = ui.input("Label (optional), for example OA due").classes("w-full")
        label.props("data-testid=add-follow-up-label")

        def save() -> None:
            if chosen.value is None:
                ui.notify("Choose an application first.", type="negative")
                return
            try:
                with session_scope(engine) as session:
                    set_follow_up(session, profile_id, chosen.value, picked.value, label.value)
            except (InvalidApplication, ApplicationNotFound) as error:
                ui.notify(str(error), type="negative")
                return
            dialog.close()
            ui.notify("Follow-up saved", type="positive")
            on_saved()

        with ui.row():
            ui.button("Cancel", on_click=dialog.close).props("flat")
            ui.button("Save", on_click=save).props("data-testid=add-follow-up-save")
    dialog.open()


def add_application_dialog(profile_id: int, on_saved) -> None:
    """Quick-add: only company and title are required; the rest can come later."""
    with ui.dialog() as dialog, ui.card().classes("w-[460px] max-w-full"):
        ui.label("Add application").classes("text-lg font-bold")
        company = ui.input("Company").classes("w-full").props("data-testid=company-input autofocus")
        title = ui.input("Title").classes("w-full").props("data-testid=title-input")
        url = ui.input("Link (optional)").classes("w-full").props("data-testid=link-input")
        with ui.expansion("More details (optional)").classes("w-full"):
            location = ui.input("Location").classes("w-full")
            salary = ui.input("Salary range").classes("w-full")
            resume = ui.input("Resume version").classes("w-full")
            notes = ui.textarea("Notes").classes("w-full")

        def save() -> None:
            try:
                with session_scope(engine) as session:
                    create_application(
                        session,
                        profile_id,
                        company.value,
                        title.value,
                        url.value,
                        location=location.value,
                        salary_range=salary.value,
                        resume_version=resume.value,
                        notes=notes.value,
                    )
            except InvalidApplication as error:
                ui.notify(str(error), type="negative")
                return
            dialog.close()
            ui.notify("Application added", type="positive")
            on_saved()

        for field in (company, title, url):
            field.on("keydown.enter", save)
        with ui.row():
            ui.button("Cancel", on_click=dialog.close).props("flat")
            ui.button("Add application", on_click=save).props("data-testid=add-button")
    dialog.open()
