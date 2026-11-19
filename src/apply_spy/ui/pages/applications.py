"""The Applications page: every application as a table or a board, with filters."""

import html
import json
import re
from datetime import date

from nicegui import app, ui

from apply_spy.db import session_scope
from apply_spy.profiles import get_active_profile
from apply_spy.tracker import (
    SORT_FIELDS,
    STATUSES,
    ApplicationNotFound,
    InvalidStatus,
    change_status,
    export_applications_csv,
    list_applications,
)
from apply_spy.ui.dialogs import add_application_dialog, follow_up_dialog
from apply_spy.ui.layout import Shell, app_shell, no_profile_card
from apply_spy.ui.state import engine
from apply_spy.ui.theme import follow_up_text, long_date

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


def current_filters() -> dict:
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


def _follow_up_card_html(a) -> str:
    """The follow-up line of a board card, with a button that asks the server to open
    the follow-up dialog (the id is a number from our own database)."""
    text = follow_up_text(a.follow_up_date, a.follow_up_label)
    action = "Change" if a.follow_up_date else "Set follow-up"
    shown = f"Follow up: {html.escape(text)} " if text else ""
    return (
        f"{shown}"
        f'<a class="as-link" href="#" '
        f"onclick=\"emitEvent('follow_up', {{id: {int(a.id)}}}); return false;\">{action}</a>"
    )


def board_html(applications) -> str:
    """The board as one HTML block (much lighter than thousands of widgets). Everything
    shown is escaped; the only links go to our own detail pages."""
    by_status = {name: [] for name in STATUSES}
    for a in applications:
        by_status[a.status].append(a)
    columns = []
    for name, cards in by_status.items():
        items = "".join(
            '<div class="as-board-card">'
            f'<a class="as-link" href="/applications/{int(a.id)}" style="font-weight:600">'
            f"{html.escape(a.company)}</a>"
            f"<div>{html.escape(a.title)}</div>"
            f'<div class="as-muted" style="font-size:.85em">{a.date_applied.isoformat()}</div>'
            f'<div style="font-size:.85em">{_follow_up_card_html(a)}</div></div>'
            for a in cards
        )
        columns.append(
            f'<div class="as-board-col"><b>{html.escape(name)}</b> '
            f'<span class="as-muted">({len(cards)})</span>{items}</div>'
        )
    return f'<div class="as-board" data-testid="board">{"".join(columns)}</div>'


@ui.page("/applications")
def applications_page() -> None:
    with app_shell("applications") as shell:
        if shell.profile_id is None:
            no_profile_card()
            return
        _build(shell)


def _build(shell: Shell) -> None:
    profile_id = shell.profile_id

    columns = [
        {"name": "company", "label": "Company", "field": "company", "align": "left"},
        {"name": "title", "label": "Title", "field": "title", "align": "left"},
        {"name": "location", "label": "Location", "field": "location", "align": "left"},
        {"name": "status", "label": "Status", "field": "status", "align": "left"},
        {"name": "date", "label": "Applied", "field": "date_applied", "align": "left"},
        {"name": "follow_up", "label": "Follow-up", "field": "follow_up", "align": "left"},
        {"name": "link", "label": "Link", "field": "url", "align": "left"},
    ]

    def export_csv() -> None:
        """Download all of this profile's applications (not just the filtered ones)."""
        with session_scope(engine) as session:
            text = export_applications_csv(session, profile_id)
            name = get_active_profile(session, profile_id).name
        slug = re.sub(r"[^A-Za-z0-9]+", "-", name).strip("-").lower() or "profile"
        filename = f"applications-{slug}-{date.today().isoformat()}.csv"
        ui.download.content(text.encode("utf-8"), filename, "text/csv")

    def set_filter(name: str, value) -> None:
        app.storage.user[FILTERS_KEY] = {**current_filters(), name: value}
        results.refresh()

    def clear_filters() -> None:
        app.storage.user[FILTERS_KEY] = {**current_filters(), "status": "All", "search": ""}
        ui.navigate.reload()  # simplest way to reset the controls to match

    def open_follow_up(event) -> None:
        """Open the follow-up dialog for the application whose button was clicked."""
        application_id = event.args.get("id") if isinstance(event.args, dict) else None
        if isinstance(application_id, int):
            follow_up_dialog(
                profile_id, application_id, lambda: (results.refresh(), shell.refresh_badge())
            )

    # Board cards live inside one HTML block, so their buttons send a page-level event.
    # Registered once here (not inside results(), which re-runs on every refresh).
    ui.on("follow_up", open_follow_up)

    def on_row_status_change(event) -> None:
        try:
            with session_scope(engine) as session:
                change_status(session, profile_id, event.args["id"], event.args["status"])
        except (InvalidStatus, ApplicationNotFound) as error:
            ui.notify(str(error), type="negative")
        else:
            ui.notify(f"Status: {event.args['status']}", type="positive")
        results.refresh()  # shows the saved value (or puts the old one back after an error)

    # --- Header ------------------------------------------------------------------
    with ui.row().classes("w-full items-center justify-between"):
        with ui.column().classes("gap-1"):
            ui.label("Applications").classes("as-h1")
            ui.label("Every application you have sent, in one place.").classes("as-sub")
        with ui.row().classes("gap-2"):
            ui.button("Export CSV", icon="download", on_click=export_csv).props(
                "outline no-caps color=primary data-testid=export-button"
            )
            ui.button(
                "Add Application",
                icon="add",
                on_click=lambda: add_application_dialog(profile_id, results.refresh),
            ).props("unelevated no-caps color=primary data-testid=add-application-button")

    # --- Filters, search, sort and view: remembered for this browser session, so opening
    # an application and coming back shows the same results. ------------------------------
    filters = current_filters()
    with ui.element("div").classes("as-card w-full"):
        with ui.row().classes("items-center gap-3 w-full"):
            ui.select(
                ["All", *STATUSES],
                value=filters["status"],
                label="Status",
                on_change=lambda e: set_filter("status", e.value),
            ).props("outlined dense data-testid=filter-status").classes("w-48")
            ui.input(
                "Search company, title, location",
                value=filters["search"],
                on_change=lambda e: set_filter("search", e.value or ""),
            ).props("outlined dense clearable debounce=300 data-testid=filter-search").classes(
                "w-72"
            )
            ui.select(
                SORT_LABELS,
                value=filters["sort_by"],
                label="Sort by",
                on_change=lambda e: set_filter("sort_by", e.value),
            ).props("outlined dense data-testid=sort-by").classes("w-44")
            ui.toggle(
                {True: "Descending", False: "Ascending"},
                value=filters["descending"],
                on_change=lambda e: set_filter("descending", e.value),
            ).props("no-caps unelevated toggle-color=primary")
            ui.toggle(
                ["Table", "Board"],
                value=filters["view"],
                on_change=lambda e: set_filter("view", e.value),
            ).props("no-caps unelevated toggle-color=primary data-testid=view-toggle")
            ui.button("Clear filters", on_click=clear_filters).props("flat no-caps")

    @ui.refreshable
    def results() -> None:
        filters = current_filters()
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
                    "date_applied": long_date(a.date_applied),
                    "follow_up": follow_up_text(a.follow_up_date, a.follow_up_label),
                    "url": a.url or "",
                }
                for a in found
            ]
            board = board_html(found) if filters["view"] == "Board" else ""
        if not rows:
            message = (
                "No applications match these filters."
                if is_filtered
                else "No applications yet. Click Add Application to add your first one."
            )
            with ui.element("div").classes("as-card as-empty w-full"):
                ui.label(message).props("data-testid=empty")
            return
        ui.label(f"Showing {len(rows)} application{'s' if len(rows) != 1 else ''}").classes(
            "as-muted"
        ).props("data-testid=count")

        if filters["view"] == "Board":
            ui.html(board, sanitize=False).classes("w-full")
            return

        with ui.element("div").classes("as-card as-table-card w-full"):
            grid = (
                ui.table(columns=columns, rows=rows, row_key="id", pagination=50)
                .props("flat data-testid=applications")
                .classes("w-full")
            )
            grid.add_slot(
                "body-cell-company",
                '<q-td :props="props"><a :href="\'/applications/\' + props.row.id" '
                'class="as-link">{{ props.value }}</a></q-td>',
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
            # The calendar button in each row opens the same follow-up dialog as the board.
            grid.add_slot(
                "body-cell-follow_up",
                '<q-td :props="props">{{ props.value }} <q-btn flat dense round size="sm" '
                'icon="event" @click="() => $parent.$emit(\'follow_up\', {id: props.row.id})" />'
                "</q-td>",
            )
            grid.on("follow_up", open_follow_up)
            # Links are checked to be http(s) by the service before they are saved.
            grid.add_slot(
                "body-cell-link",
                '<q-td :props="props"><a v-if="props.value" :href="props.value" '
                'target="_blank" rel="noopener" class="as-link">Open</a></q-td>',
            )

    results()
