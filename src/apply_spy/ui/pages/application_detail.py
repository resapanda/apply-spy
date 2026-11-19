"""One application: edit its details, change its status, set a follow-up, see history."""

from datetime import date

from nicegui import ui

from apply_spy.db import session_scope
from apply_spy.tracker import (
    STATUSES,
    ApplicationNotFound,
    InvalidApplication,
    InvalidStatus,
    change_status,
    get_application,
    list_status_events,
    update_application_details,
)
from apply_spy.ui.dialogs import follow_up_dialog
from apply_spy.ui.layout import Shell, app_shell, no_profile_card
from apply_spy.ui.state import engine
from apply_spy.ui.theme import follow_up_text, status_chip


@ui.page("/applications/{application_id}")
def application_page(application_id: int) -> None:
    with app_shell("applications") as shell:
        if shell.profile_id is None:
            no_profile_card()
            return
        _build(shell, application_id)


def _build(shell: Shell, application_id: int) -> None:
    profile_id = shell.profile_id
    ui.html(
        '<a class="as-link" href="/applications">&larr; Back to applications</a>', sanitize=False
    )
    try:
        with session_scope(engine) as session:
            application = get_application(session, profile_id, application_id)
    except ApplicationNotFound:
        # Also what you see for another profile's application: it is not yours to open.
        with ui.element("div").classes("as-card"):
            ui.label("Application not found in this profile.").props("data-testid=not-found")
        return

    heading = ui.label(f"{application.company}: {application.title}").classes("as-h1")

    def subtitle(status: str) -> str:
        applied = application.date_applied
        return (
            f'<div class="as-sub">Applied {applied:%b} {applied.day}, {applied.year}'
            f" &middot; {status_chip(status)}</div>"
        )

    subtitle_label = ui.html(subtitle(application.status), sanitize=False)

    with ui.row().classes("w-full items-start gap-6").style("flex-wrap: wrap"):
        # --- Left: the details form ---------------------------------------------------
        with ui.element("div").classes("as-card").style("flex: 1 1 520px; min-width: 0"):
            ui.label("Details").classes("as-title mb-2")
            with ui.row().classes("w-full gap-3 no-wrap"):
                company = ui.input("Company", value=application.company).classes("grow")
                title = ui.input("Title", value=application.title).classes("grow")
            link = ui.input("Link", value=application.url or "").classes("w-full")
            with ui.row().classes("w-full gap-3 no-wrap"):
                location = ui.input("Location", value=application.location or "").classes("grow")
                salary = ui.input("Salary range", value=application.salary_range or "").classes(
                    "grow"
                )
                resume = ui.input("Resume version", value=application.resume_version or "").classes(
                    "grow"
                )
            # The date is picked from a calendar (read-only text box) so it can never be mistyped.
            with ui.input("Date applied", value=application.date_applied.isoformat()).props(
                "readonly"
            ) as date_input:
                with ui.menu().props("no-parent-event") as menu:
                    with ui.date().bind_value(date_input):
                        with ui.row().classes("justify-end"):
                            ui.button("Close", on_click=menu.close).props("flat")
                with date_input.add_slot("append"):
                    ui.icon("edit_calendar").on("click", menu.open).classes("cursor-pointer")
            notes = ui.textarea("Notes", value=application.notes or "").classes("w-full")

            def save() -> None:
                # The calendar picker could hand back nothing; the service then rejects it
                # with a clear message instead of this raising an unhandled error.
                try:
                    applied = date.fromisoformat(date_input.value) if date_input.value else None
                except ValueError:
                    applied = None
                try:
                    with session_scope(engine) as session:
                        updated = update_application_details(
                            session,
                            profile_id,
                            application_id,
                            company=company.value,
                            title=title.value,
                            url=link.value,
                            location=location.value,
                            salary_range=salary.value,
                            resume_version=resume.value,
                            notes=notes.value,
                            date_applied=applied,
                        )
                        heading_text = f"{updated.company}: {updated.title}"
                except (InvalidApplication, ApplicationNotFound) as error:
                    ui.notify(str(error), type="negative")
                    return
                heading.set_text(heading_text)
                ui.notify("Saved", type="positive")

            ui.button("Save changes", on_click=save).props(
                "unelevated no-caps color=primary data-testid=save-button"
            ).classes("mt-2")

        # --- Right: follow-up, status and history ---------------------------------------
        with ui.column().classes("gap-5").style("flex: 0 0 400px; max-width: 100%"):
            with ui.element("div").classes("as-card w-full"):
                ui.label("Follow-up").classes("as-title mb-1")
                text = follow_up_text(application.follow_up_date, application.follow_up_label)
                ui.label(text if text else "No follow-up set").props("data-testid=follow-up")
                ui.button(
                    "Change follow-up" if text else "Set follow-up",
                    icon="event",
                    on_click=lambda: follow_up_dialog(
                        profile_id, application_id, ui.navigate.reload
                    ),
                ).props("outline no-caps color=primary").classes("mt-2")

            with ui.element("div").classes("as-card w-full"):
                ui.label("Status").classes("as-title mb-1")

                def on_status_change(event) -> None:
                    try:
                        with session_scope(engine) as session:
                            change_status(session, profile_id, application_id, event.value)
                    except (InvalidStatus, ApplicationNotFound) as error:
                        ui.notify(str(error), type="negative")
                        return
                    ui.notify(f"Status: {event.value}", type="positive")
                    subtitle_label.set_content(subtitle(event.value))  # keep the chip in step
                    history.refresh()

                ui.select(
                    list(STATUSES),
                    value=application.status,
                    label="Status",
                    on_change=on_status_change,
                ).props("outlined dense data-testid=status-select").classes("w-full")

                @ui.refreshable
                def history() -> None:
                    with session_scope(engine) as session:
                        events = list_status_events(session, profile_id, application_id)
                    # The first event (no old status) is the "added" line above, not a change.
                    rows = [
                        {
                            "id": e.id,
                            "when": e.changed_at.astimezone().strftime("%Y-%m-%d %H:%M"),
                            "old": e.old_status,
                            "new": e.new_status,
                        }
                        for e in events
                        if e.old_status is not None
                    ]
                    ui.label("Status history").classes("as-title mt-4 mb-1")
                    if not rows:
                        ui.label("No status changes yet.").classes("as-muted").props(
                            "data-testid=no-history"
                        )
                        return
                    # Read-only on purpose: history is a record, so there is no edit control.
                    ui.table(
                        columns=[
                            {"name": "when", "label": "When", "field": "when", "align": "left"},
                            {"name": "old", "label": "From", "field": "old", "align": "left"},
                            {"name": "new", "label": "To", "field": "new", "align": "left"},
                        ],
                        rows=rows,
                        row_key="id",
                    ).props("flat dense data-testid=history").classes("w-full")

                history()
