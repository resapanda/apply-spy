"""The Mark Complete / Change date / Undo actions, shared by the Dashboard and the
Follow-ups page. The buttons live inside HTML blocks, so they send page-level events;
this class listens for them."""

from collections.abc import Callable

from nicegui import ui

from apply_spy.db import session_scope
from apply_spy.tracker import (
    ApplicationNotFound,
    CompletedFollowUpNotFound,
    InvalidApplication,
    complete_follow_up,
    get_application,
    undo_complete_follow_up,
)
from apply_spy.ui.dialogs import follow_up_dialog
from apply_spy.ui.state import engine
from apply_spy.ui.theme import follow_up_text


def event_js(name: str, application_id: int) -> str:
    """The onclick that asks the server to handle `name` for this id (a number from our
    own database, never text typed by a user)."""
    return f"emitEvent('{name}', {{id: {int(application_id)}}})"


class FollowUpActions:
    def __init__(
        self, profile_id: int, refresh: Callable[[], None], refresh_bar: Callable[[], None]
    ):
        self.profile_id = profile_id
        self.refresh = refresh  # called after anything changes
        self.refresh_bar = refresh_bar  # called when only the Undo bar changes
        self.last_completed: dict | None = None  # the most recent Mark Complete, for Undo

    def register(self) -> None:
        """Listen for the page-level events the HTML buttons send. Call once per page
        (not inside a refreshable part, which runs again on every refresh)."""
        ui.on("follow_up_done", self._on_complete)
        ui.on("follow_up", self._on_change_date)
        ui.on("undo_completed", self._on_undo_completed)

    @staticmethod
    def _id_from(event) -> int | None:
        found = event.args.get("id") if isinstance(event.args, dict) else None
        return found if isinstance(found, int) else None

    def _on_complete(self, event) -> None:
        application_id = self._id_from(event)
        if application_id is None:
            return
        try:
            with session_scope(engine) as session:
                company = get_application(session, self.profile_id, application_id).company
                record = complete_follow_up(session, self.profile_id, application_id)
                if record is not None:
                    self.last_completed = {
                        "id": record.id,
                        "company": company,
                        "text": follow_up_text(record.due_date, record.label),
                    }
        except ApplicationNotFound as error:
            ui.notify(str(error), type="negative")
            return
        self.refresh()

    def _on_change_date(self, event) -> None:
        application_id = self._id_from(event)
        if application_id is not None:
            follow_up_dialog(self.profile_id, application_id, self.refresh)

    def _on_undo_completed(self, event) -> None:
        completed_id = self._id_from(event)
        if completed_id is not None:
            self.undo(completed_id)

    def undo(self, completed_id: int) -> None:
        try:
            with session_scope(engine) as session:
                undo_complete_follow_up(session, self.profile_id, completed_id)
        except (CompletedFollowUpNotFound, InvalidApplication) as error:
            ui.notify(str(error), type="negative")
        self.last_completed = None
        self.refresh()

    def dismiss(self) -> None:
        self.last_completed = None
        self.refresh_bar()

    def undo_bar(self) -> None:
        """The green 'Completed: ... Undo' bar, shown after Mark Complete."""
        pending = self.last_completed
        if pending is None:
            return
        with ui.element("div").classes("as-undo").props("data-testid=undo-bar"):
            ui.icon("check_circle", color="positive")
            ui.label(f"Completed: {pending['company']} ({pending['text'] or 'no date'})")
            ui.button("Undo", on_click=lambda: self.undo(pending["id"])).props(
                "flat dense no-caps color=positive"
            )
            ui.button(icon="close", on_click=self.dismiss).props("flat dense round")
