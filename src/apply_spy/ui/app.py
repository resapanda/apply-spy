"""Local web UI (NiceGUI). No business logic here: it calls the service layer."""

import html
import json
from contextlib import contextmanager
from datetime import UTC, date

from nicegui import app, ui

from apply_spy.db import init_db, make_engine, session_scope
from apply_spy.profiles import (
    ConfirmationRequired,
    DuplicateProfileName,
    InvalidProfileName,
    create_profile,
    delete_profile,
    get_active_profile,
    list_profiles,
)
from apply_spy.tracker import (
    SORT_FIELDS,
    STATUSES,
    ApplicationNotFound,
    InvalidApplication,
    InvalidStatus,
    change_status,
    create_application,
    get_application,
    list_applications,
    list_status_events,
    update_application_details,
)

engine = make_engine()
init_db(engine)


ACTIVE_PROFILE_KEY = "active_profile_id"


@contextmanager
def page_layout():
    """Header shared by every page: app name plus the profile switcher.

    Every page should use this so the active profile name is always visible.
    """
    with session_scope(engine) as session:
        profiles = [(p.id, p.name) for p in list_profiles(session)]
        active = get_active_profile(session, app.storage.user.get(ACTIVE_PROFILE_KEY))
        active_id = active.id if active else None

    def switch(event) -> None:
        app.storage.user[ACTIVE_PROFILE_KEY] = event.value
        ui.navigate.reload()  # reload so everything on the page uses the new profile

    with ui.header().classes("items-center justify-between"):
        with ui.row().classes("items-center gap-4"):
            ui.label("apply-spy").classes("text-xl font-bold")
            ui.link("Applications", "/").classes("text-white")
            ui.link("Profiles", "/profiles").classes("text-white")
        if profiles:
            ui.select(
                dict(profiles), value=active_id, label="Active profile", on_change=switch
            ).props("data-testid=profile-switcher dense")
        else:
            ui.label("No profile yet").props("data-testid=profile-switcher")
    yield active_id


@ui.page("/")
def applications_page() -> None:
    with page_layout() as active_id:
        _applications_content(active_id)


@ui.page("/applications/{application_id}")
def application_page(application_id: int) -> None:
    with page_layout() as active_id:
        _application_detail_content(active_id, application_id)


@ui.page("/profiles")
def profiles_page() -> None:
    with page_layout():
        _profiles_content()


FILTERS_KEY = "application_filters"
DEFAULT_FILTERS = {
    "status": "All",
    "search": "",
    "sort_by": "created",
    "descending": True,
    "view": "Table",
}
SORT_LABELS = {
    "created": "Newest added",
    "date_applied": "Date applied",
    "company": "Company",
    "status": "Status",
}


def _current_filters() -> dict:
    """The filters kept for this browser session, cleaned of anything stale."""
    saved = {**DEFAULT_FILTERS, **(app.storage.user.get(FILTERS_KEY) or {})}
    if saved["status"] != "All" and saved["status"] not in STATUSES:
        saved["status"] = "All"
    if saved["sort_by"] not in SORT_FIELDS:
        saved["sort_by"] = DEFAULT_FILTERS["sort_by"]
    if saved["view"] not in ("Table", "Board"):
        saved["view"] = "Table"
    saved["search"] = str(saved["search"] or "")
    saved["descending"] = bool(saved["descending"])
    return saved


def _board_html(applications) -> str:
    """The board as one HTML block (much lighter than thousands of widgets).

    Everything shown is escaped, and the only link is to our own detail page.
    """
    by_status = {name: [] for name in STATUSES}
    for a in applications:
        by_status[a.status].append(a)
    columns = []
    for name, cards in by_status.items():
        items = "".join(
            f'<div style="border:1px solid rgba(128,128,128,.4);border-radius:6px;'
            f'padding:6px;margin-top:6px"><a href="/applications/{a.id}" '
            f'style="font-weight:600;text-decoration:underline">{html.escape(a.company)}</a>'
            f"<div>{html.escape(a.title)}</div>"
            f'<div style="opacity:.7;font-size:.85em">{a.date_applied.isoformat()}</div></div>'
            for a in cards
        )
        columns.append(
            '<div style="min-width:220px;max-width:220px;background:rgba(128,128,128,.12);'
            f'border-radius:8px;padding:8px"><b>{html.escape(name)}</b> '
            f'<span style="opacity:.7">({len(cards)})</span>{items}</div>'
        )
    return (
        '<div data-testid="board" style="display:flex;gap:12px;overflow-x:auto;'
        f'align-items:flex-start">{"".join(columns)}</div>'
    )


def _applications_content(profile_id: int | None) -> None:
    ui.label("Applications").classes("text-lg mt-4")
    if profile_id is None:
        ui.label("Create a profile first, then add applications.")
        ui.link("Go to Profiles", "/profiles")
        return

    columns = [
        {"name": "company", "label": "Company", "field": "company", "align": "left"},
        {"name": "title", "label": "Title", "field": "title", "align": "left"},
        {"name": "location", "label": "Location", "field": "location", "align": "left"},
        {"name": "status", "label": "Status", "field": "status", "align": "left"},
        {"name": "date", "label": "Applied", "field": "date_applied", "align": "left"},
        {"name": "link", "label": "Link", "field": "url", "align": "left"},
    ]

    def set_filter(name: str, value) -> None:
        app.storage.user[FILTERS_KEY] = {**_current_filters(), name: value}
        results.refresh()

    def clear_filters() -> None:
        app.storage.user[FILTERS_KEY] = {**_current_filters(), "status": "All", "search": ""}
        ui.navigate.reload()  # simplest way to reset the controls to match

    @ui.refreshable
    def results() -> None:
        filters = _current_filters()
        is_filtered = filters["status"] != "All" or bool(filters["search"].strip())
        with session_scope(engine) as session:
            found = list_applications(
                session,
                profile_id,
                status=None if filters["status"] == "All" else filters["status"],
                search=filters["search"],
                sort_by=filters["sort_by"],
                descending=filters["descending"],
            )
            # Plain values only, so nothing touches the database after this block.
            rows = [
                {
                    "id": a.id,
                    "company": a.company,
                    "title": a.title,
                    "location": a.location or "",
                    "status": a.status,
                    "date_applied": a.date_applied.isoformat(),
                    "url": a.url or "",
                }
                for a in found
            ]
            board = _board_html(found) if filters["view"] == "Board" else ""
        if not rows:
            message = (
                "No applications match these filters."
                if is_filtered
                else "No applications yet. Add your first one above."
            )
            ui.label(message).props("data-testid=empty")
            return
        ui.label(f"Showing {len(rows)} application{'s' if len(rows) != 1 else ''}").classes(
            "text-sm opacity-70"
        ).props("data-testid=count")

        if filters["view"] == "Board":
            ui.html(board, sanitize=False)
            return

        grid = ui.table(columns=columns, rows=rows, row_key="id", pagination=50).props(
            "data-testid=applications"
        )
        grid.add_slot(
            "body-cell-company",
            '<q-td :props="props"><a :href="\'/applications/\' + props.row.id" '
            'class="text-primary" style="text-decoration: underline">'
            "{{ props.value }}</a></q-td>",
        )
        # Status is a dropdown in every row: choosing from a list avoids typos.
        # The options are our own fixed list, so embedding them here is safe.
        grid.add_slot(
            "body-cell-status",
            '<q-td :props="props"><q-select dense outlined options-dense '
            f":options='{json.dumps(list(STATUSES))}' :model-value=\"props.value\" "
            "@update:model-value=\"(value) => $parent.$emit('status_change', "
            '{id: props.row.id, status: value})" /></q-td>',
        )
        grid.on("status_change", on_row_status_change)
        # Links are checked to be http(s) by the service before they are saved.
        grid.add_slot(
            "body-cell-link",
            '<q-td :props="props"><a v-if="props.value" :href="props.value" '
            'target="_blank" rel="noopener">Open</a></q-td>',
        )

    def on_row_status_change(event) -> None:
        try:
            with session_scope(engine) as session:
                change_status(session, profile_id, event.args["id"], event.args["status"])
        except (InvalidStatus, ApplicationNotFound) as error:
            ui.notify(str(error), type="negative")
        else:
            ui.notify(f"Status: {event.args['status']}", type="positive")
        results.refresh()  # shows the saved value (or puts the old one back after an error)

    def submit() -> None:
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
        for field in (company, title, url, location, salary, resume, notes):
            field.value = ""
        ui.notify("Application added", type="positive")
        results.refresh()

    with ui.row().classes("items-start gap-2"):
        company = ui.input("Company").props("data-testid=company-input").on("keydown.enter", submit)
        title = ui.input("Title").props("data-testid=title-input").on("keydown.enter", submit)
        url = (
            ui.input("Link (optional)").props("data-testid=link-input").on("keydown.enter", submit)
        )
        ui.button("Add application", on_click=submit).props("data-testid=add-button")
    with ui.expansion("More details (optional)").classes("w-full"):
        with ui.row().classes("gap-2"):
            location = ui.input("Location")
            salary = ui.input("Salary range")
            resume = ui.input("Resume version")
        notes = ui.textarea("Notes").classes("w-full")

    # Filters, search, sort and view. They are remembered for this browser session, so
    # opening an application and coming back shows the same results.
    filters = _current_filters()
    with ui.row().classes("items-center gap-2 mt-4"):
        ui.select(
            ["All", *STATUSES],
            value=filters["status"],
            label="Status",
            on_change=lambda e: set_filter("status", e.value),
        ).props("data-testid=filter-status").classes("w-48")
        ui.input(
            "Search company, title, location",
            value=filters["search"],
            on_change=lambda e: set_filter("search", e.value or ""),
        ).props("clearable debounce=300 data-testid=filter-search").classes("w-72")
        ui.select(
            SORT_LABELS,
            value=filters["sort_by"],
            label="Sort by",
            on_change=lambda e: set_filter("sort_by", e.value),
        ).props("data-testid=sort-by").classes("w-44")
        ui.toggle(
            {True: "Descending", False: "Ascending"},
            value=filters["descending"],
            on_change=lambda e: set_filter("descending", e.value),
        )
        ui.toggle(
            ["Table", "Board"],
            value=filters["view"],
            on_change=lambda e: set_filter("view", e.value),
        ).props("data-testid=view-toggle")
        ui.button("Clear filters", on_click=clear_filters).props("flat")

    results()


def _application_detail_content(profile_id: int | None, application_id: int) -> None:
    ui.link("< Back to applications", "/")
    if profile_id is None:
        ui.label("Create a profile first.")
        return
    try:
        with session_scope(engine) as session:
            application = get_application(session, profile_id, application_id)
    except ApplicationNotFound:
        # Also what you see for another profile's application: it is not yours to open.
        ui.label("Application not found in this profile.").props("data-testid=not-found")
        return

    heading = ui.label(f"{application.company}: {application.title}").classes(
        "text-xl font-bold mt-2"
    )

    # Edit form: every detail can be changed. The date is picked from a calendar
    # (read-only text box) so it can never be mistyped.
    with ui.card().classes("w-full max-w-2xl"):
        with ui.row().classes("gap-2"):
            company = ui.input("Company", value=application.company)
            title = ui.input("Title", value=application.title)
        link = ui.input("Link", value=application.url or "").classes("w-full")
        with ui.row().classes("gap-2"):
            location = ui.input("Location", value=application.location or "")
            salary = ui.input("Salary range", value=application.salary_range or "")
            resume = ui.input("Resume version", value=application.resume_version or "")
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
                        date_applied=date.fromisoformat(date_input.value),
                    )
                    heading_text = f"{updated.company}: {updated.title}"
            except (InvalidApplication, ApplicationNotFound) as error:
                ui.notify(str(error), type="negative")
                return
            heading.set_text(heading_text)
            ui.notify("Saved", type="positive")

        ui.button("Save changes", on_click=save).props("data-testid=save-button")

    @ui.refreshable
    def history() -> None:
        with session_scope(engine) as session:
            events = list_status_events(session, profile_id, application_id)
        # The first event (no old status) is the "added" line above, not a change.
        rows = [
            {
                "id": e.id,
                "when": e.changed_at.replace(tzinfo=UTC).astimezone().strftime("%Y-%m-%d %H:%M"),
                "old": e.old_status,
                "new": e.new_status,
            }
            for e in events
            if e.old_status is not None
        ]
        ui.label("Status history").classes("text-lg mt-4")
        if not rows:
            ui.label("No status changes yet.").props("data-testid=no-history")
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
        ).props("data-testid=history")

    def on_status_change(event) -> None:
        try:
            with session_scope(engine) as session:
                change_status(session, profile_id, application_id, event.value)
        except (InvalidStatus, ApplicationNotFound) as error:
            ui.notify(str(error), type="negative")
            return
        ui.notify(f"Status: {event.value}", type="positive")
        history.refresh()

    ui.select(
        list(STATUSES), value=application.status, label="Status", on_change=on_status_change
    ).props("data-testid=status-select").classes("w-64 mt-4")
    history()


def _profiles_content() -> None:
    ui.label("Profiles").classes("text-lg mt-4")

    @ui.refreshable
    def profile_list() -> None:
        with session_scope(engine) as session:
            profiles = list_profiles(session)
        if not profiles:
            ui.label("No profiles yet. Create one below.").props("data-testid=empty")
        for profile in profiles:
            with ui.row().classes("items-center"):
                ui.label(profile.name).props("data-testid=profile-name")
                ui.button(
                    icon="delete",
                    color="negative",
                    on_click=lambda _, p=profile: confirm_delete(p.id, p.name),
                ).props("flat dense data-testid=delete-button")

    def confirm_delete(profile_id: int, name: str) -> None:
        with ui.dialog() as dialog, ui.card():
            ui.label(f"Delete {name}?").classes("text-lg font-bold")
            ui.label("This permanently removes the profile and all of its data.")
            confirm = ui.checkbox("Yes, delete this profile and all its data").props(
                "data-testid=confirm-checkbox"
            )

            def do_delete() -> None:
                try:
                    with session_scope(engine) as session:
                        delete_profile(session, profile_id, confirm.value)
                except ConfirmationRequired as error:
                    ui.notify(str(error), type="negative")
                    return
                dialog.close()
                ui.notify(f"Deleted {name}", type="positive")
                ui.navigate.reload()  # header switcher must drop the deleted profile

            with ui.row():
                ui.button("Cancel", on_click=dialog.close).props("flat")
                # The button is only a convenience; the service enforces the check.
                ui.button("Delete", color="negative", on_click=do_delete).props(
                    "data-testid=confirm-delete-button"
                ).bind_enabled_from(confirm, "value")
        dialog.open()

    profile_list()

    def submit() -> None:
        try:
            with session_scope(engine) as session:
                create_profile(session, name_input.value)
        except (InvalidProfileName, DuplicateProfileName) as error:
            ui.notify(str(error), type="negative")
            return
        name_input.value = ""
        ui.notify("Profile created", type="positive")
        profile_list.refresh()

    with ui.row().classes("items-center mt-4"):
        name_input = ui.input("Name").props("data-testid=name-input").on("keydown.enter", submit)
        ui.button("Create profile", on_click=submit).props("data-testid=create-button")


def run() -> None:
    # host=127.0.0.1: only this computer can reach the app (your data stays private).
    # storage_secret signs the cookie that remembers the active profile in this browser.
    # It protects nothing sensitive: the app is only reachable from this computer.
    ui.run(
        title="apply-spy",
        host="127.0.0.1",
        reload=False,
        show=True,
        storage_secret="apply-spy-local",
    )
