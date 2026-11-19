"""Local web UI (NiceGUI). No business logic here: it calls the service layer."""

import calendar
import html
import json
import re
from contextlib import contextmanager
from datetime import UTC, date

from nicegui import app, ui

from apply_spy.backup import BackupError, check_database, create_backup
from apply_spy.config import DATABASE_FILENAME, PORT, data_dir
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
    calendar_month,
    change_status,
    complete_follow_up,
    create_application,
    export_applications_csv,
    get_application,
    list_applications,
    list_status_events,
    needs_attention,
    set_follow_up,
    update_application_details,
)

engine = make_engine()
init_db(engine)


ACTIVE_PROFILE_KEY = "active_profile_id"

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

    def confirm_quit() -> None:
        with ui.dialog() as dialog, ui.card():
            ui.label("Quit apply-spy?").classes("text-lg font-bold")
            ui.label("Your data is saved. Click the apply-spy icon to start it again.")

            async def quit_app() -> None:
                dialog.close()
                # Cover the page with a clear message BEFORE the server goes away. Once it
                # is gone it cannot send anything, and the browser would only show a
                # frozen page with "Connection lost".
                try:
                    await ui.run_javascript(STOPPED_PAGE_JS, timeout=3)
                except TimeoutError:
                    pass  # no browser answered; shut down anyway
                app.shutdown()

            with ui.row():
                ui.button("Cancel", on_click=dialog.close).props("flat")
                ui.button("Quit", color="negative", on_click=quit_app).props(
                    "data-testid=confirm-quit"
                )
        dialog.open()

    with ui.header().classes("items-center justify-between"):
        with ui.row().classes("items-center gap-4"):
            ui.label("apply-spy").classes("text-xl font-bold")
            ui.link("Dashboard", "/").classes("text-white")
            ui.link("Applications", "/applications").classes("text-white")
            ui.link("Profiles", "/profiles").classes("text-white")
            ui.link("Data", "/data").classes("text-white")
        with ui.row().classes("items-center gap-2"):
            if profiles:
                ui.select(
                    dict(profiles), value=active_id, label="Active profile", on_change=switch
                ).props("data-testid=profile-switcher dense")
            else:
                ui.label("No profile yet").props("data-testid=profile-switcher")
            ui.button(icon="power_settings_new", on_click=confirm_quit).props(
                "flat round dense color=white data-testid=quit-button"
            ).tooltip("Quit apply-spy")
    yield active_id


@ui.page("/")
def dashboard_page() -> None:
    with page_layout() as active_id:
        _dashboard_content(active_id)


@ui.page("/applications")
def applications_page() -> None:
    with page_layout() as active_id:
        _applications_content(active_id)


@ui.page("/applications/{application_id}")
def application_page(application_id: int) -> None:
    with page_layout() as active_id:
        _application_detail_content(active_id, application_id)


@ui.page("/data")
def data_page() -> None:
    with page_layout():
        _data_content()


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


def _days_text(days: int) -> str:
    """How far a follow-up is from today, in words."""
    if days < 0:
        return f"{-days} day{'s' if days != -1 else ''} overdue"
    if days == 0:
        return "Today"
    return f"in {days} day{'s' if days != 1 else ''}"


def _calendar_html(month) -> str:
    """The month grid as one HTML block. All text is escaped; the only links go to our
    own detail pages. Chips are coloured like the tiles: overdue red, today yellow,
    later blue."""
    chips_per_day = 3
    cells = [
        f'<div style="font-weight:600;font-size:.85em;text-align:center;opacity:.7">{name}</div>'
        for name in ("Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat")
    ]
    for week in month.weeks:
        for cell in week:
            is_today = cell.day == month.today
            colour = (
                "rgba(193,0,21,.22)"
                if cell.day < month.today
                else "rgba(242,192,55,.4)"
                if is_today
                else "rgba(25,118,210,.22)"
            )
            chips = "".join(
                f'<a href="/applications/{int(a.id)}" title="{html.escape(a.company)}'
                f'{": " + html.escape(a.follow_up_label) if a.follow_up_label else ""}" '
                f'style="display:block;font-size:.72em;padding:1px 3px;margin-top:2px;'
                f"border-radius:4px;background:{colour};white-space:nowrap;overflow:hidden;"
                f'text-overflow:ellipsis;text-decoration:none;color:inherit">'
                f"{html.escape(a.company)}"
                f"{' · ' + html.escape(a.follow_up_label) if a.follow_up_label else ''}</a>"
                for a in cell.applications[:chips_per_day]
            )
            extra = len(cell.applications) - chips_per_day
            more = (
                f'<div style="font-size:.75em;opacity:.7">+{extra} more</div>' if extra > 0 else ""
            )
            border = "2px solid #1976d2" if is_today else "1px solid rgba(128,128,128,.35)"
            dim = "" if cell.in_month else "opacity:.45;"
            cells.append(
                f'<div style="min-height:72px;padding:3px;min-width:0;border:{border};'
                f'border-radius:6px;{dim}">'
                f'<div style="font-size:.85em;{"font-weight:700;" if is_today else ""}">'
                f"{cell.day.day}</div>{chips}{more}</div>"
            )
    return (
        '<div data-testid="calendar" style="overflow-x:auto"><div style="display:grid;'
        f'grid-template-columns:repeat(7,minmax(0,1fr));gap:3px">{"".join(cells)}</div></div>'
    )


def _dashboard_content(profile_id: int | None) -> None:
    """The home page: what needs attention first. Insights (funnel, weekly activity)
    are planned for later and will be added here."""
    ui.label("Dashboard").classes("text-2xl font-bold mt-4")
    if profile_id is None:
        ui.label("Create a profile first, then add applications.")
        ui.link("Go to Profiles", "/profiles")
        return

    columns = [
        {"name": "due", "label": "Follow-up", "field": "due", "align": "left"},
        {"name": "label", "label": "Label", "field": "label", "align": "left"},
        {"name": "company", "label": "Company", "field": "company", "align": "left"},
        {"name": "title", "label": "Title", "field": "title", "align": "left"},
        {"name": "status", "label": "Status", "field": "status", "align": "left"},
        {"name": "action", "label": "", "field": "id", "align": "left"},
    ]

    def open_follow_up(event) -> None:
        application_id = event.args.get("id") if isinstance(event.args, dict) else None
        if isinstance(application_id, int):
            _follow_up_dialog(profile_id, application_id, refresh_all)

    last_done: dict = {"item": None}  # the most recent Done, kept so it can be undone

    def mark_done(event) -> None:
        """Clear the follow-up in one click and remember it for Undo."""
        application_id = event.args.get("id") if isinstance(event.args, dict) else None
        if not isinstance(application_id, int):
            return
        try:
            with session_scope(engine) as session:
                application = get_application(session, profile_id, application_id)
                company = application.company
                previous_date, previous_label = complete_follow_up(
                    session, profile_id, application_id
                )
        except ApplicationNotFound as error:
            ui.notify(str(error), type="negative")
            return
        last_done["item"] = {
            "id": application_id,
            "company": company,
            "date": previous_date,
            "label": previous_label,
        }
        refresh_all()

    def undo_done() -> None:
        item = last_done["item"]
        last_done["item"] = None
        if item is not None:
            try:
                with session_scope(engine) as session:
                    set_follow_up(session, profile_id, item["id"], item["date"], item["label"])
            except (InvalidApplication, ApplicationNotFound) as error:
                ui.notify(str(error), type="negative")
        refresh_all()

    def dismiss_done() -> None:
        last_done["item"] = None
        attention.refresh()

    def section(title: str, color: str, applications, today) -> None:
        ui.label(f"{title} ({len(applications)})").classes(f"text-lg font-bold mt-4 {color}")
        rows = [
            {
                "id": a.id,
                "due": f"{a.follow_up_date.isoformat()} · "
                f"{_days_text((a.follow_up_date - today).days)}",
                "label": a.follow_up_label or "",
                "company": a.company,
                "title": a.title,
                "status": a.status,
            }
            for a in applications
        ]
        grid = (
            ui.table(columns=columns, rows=rows, row_key="id", pagination=25)
            .classes("w-full")
            .props(f"data-testid=section-{title.lower().replace(' ', '-')}")
        )
        grid.add_slot(
            "body-cell-company",
            '<q-td :props="props"><a :href="\'/applications/\' + props.row.id" '
            'class="text-primary" style="text-decoration: underline">'
            "{{ props.value }}</a></q-td>",
        )
        grid.add_slot(
            "body-cell-action",
            '<q-td :props="props">'
            '<q-btn flat dense round size="sm" color="positive" icon="check_circle" '
            'title="Done: clear this follow-up" '
            "@click=\"() => $parent.$emit('follow_up_done', {id: props.row.id})\" />"
            '<q-btn flat dense round size="sm" icon="event" '
            'title="Change or clear the follow-up" '
            "@click=\"() => $parent.$emit('follow_up', {id: props.row.id})\" />"
            "</q-td>",
        )
        grid.on("follow_up", open_follow_up)
        grid.on("follow_up_done", mark_done)

    @ui.refreshable
    def attention() -> None:
        with session_scope(engine) as session:
            found = needs_attention(session, profile_id)
        groups = [
            ("Overdue", "text-negative", found.overdue),
            ("Today", "text-warning", found.due_today),
            ("This week", "text-primary", found.this_week),
        ]
        done = last_done["item"]
        if done is not None:
            with (
                ui.row()
                .classes("items-center gap-2 p-2 rounded")
                .style("background: rgba(46,125,50,.18)")
                .props("data-testid=undo-bar")
            ):
                ui.icon("check_circle", color="positive")
                was = _follow_up_text(done["date"], done["label"]) or "no date"
                ui.label(f"Done: {done['company']} (was {was})")
                ui.button("Undo", on_click=undo_done).props("flat dense")
                ui.button(icon="close", on_click=dismiss_done).props("flat dense round")
        ui.label(f"Today is {found.today.strftime('%A')}, {found.today.isoformat()}").classes(
            "text-sm opacity-70"
        )
        with ui.row().classes("gap-4 mt-2"):
            for title, color, apps in groups:
                with (
                    ui.card()
                    .classes("w-40 items-center")
                    .props(f"data-testid=tile-{title.lower().replace(' ', '-')}")
                ):
                    ui.label(str(len(apps))).classes(f"text-4xl font-bold {color}")
                    ui.label(title).classes("text-sm opacity-70")
        if found.total == 0:
            ui.label("Nothing needs your attention right now.").classes("text-lg mt-6").props(
                "data-testid=all-clear"
            )
            ui.label(
                "Set a follow-up date on an application and it shows up here when it is "
                "due within a week."
            ).classes("opacity-70")
            ui.link("Go to Applications", "/applications")
            return
        for title, color, apps in groups:
            if apps:
                section(title, color, apps, found.today)

    shown = {"year": date.today().year, "month": date.today().month}

    def shift(months: int) -> None:
        index = shown["year"] * 12 + (shown["month"] - 1) + months
        shown["year"], shown["month"] = index // 12, index % 12 + 1
        month_view.refresh()

    def go_to_today() -> None:
        shown["year"], shown["month"] = date.today().year, date.today().month
        month_view.refresh()

    @ui.refreshable
    def month_view() -> None:
        with session_scope(engine) as session:
            month = calendar_month(session, profile_id, shown["year"], shown["month"])
        with ui.row().classes("items-center gap-2"):
            ui.button(icon="chevron_left", on_click=lambda: shift(-1)).props(
                "flat round dense data-testid=calendar-previous"
            )
            ui.label(f"{calendar.month_name[month.month]} {month.year}").classes(
                "text-lg font-bold w-48 text-center"
            ).props("data-testid=calendar-title")
            ui.button(icon="chevron_right", on_click=lambda: shift(1)).props(
                "flat round dense data-testid=calendar-next"
            )
            ui.button("Today", on_click=go_to_today).props("flat dense")
        ui.html(_calendar_html(month), sanitize=False)

    def refresh_all() -> None:
        attention.refresh()
        month_view.refresh()

    # Two columns: what needs attention on the left, the calendar on the right.
    # On a narrow window the calendar wraps underneath.
    with ui.row().classes("w-full items-start gap-8").props("data-testid=dashboard-layout"):
        with ui.column().style("flex: 1 1 520px; min-width: 0"):
            attention()
        with (
            ui.column().style("flex: 0 0 440px; max-width: 100%").props("data-testid=calendar-side")
        ):
            ui.label("Calendar").classes("text-lg font-bold")
            ui.label("Follow-up dates. Click one to open the application.").classes(
                "text-sm opacity-70"
            )
            month_view()


def _follow_up_text(follow_up_date, label) -> str:
    """ "2026-11-25 · OA due", or "" when there is no follow-up."""
    if follow_up_date is None:
        return ""
    return f"{follow_up_date.isoformat()} · {label}" if label else follow_up_date.isoformat()


def _follow_up_dialog(profile_id: int, application_id: int, on_saved) -> None:
    """Set, change or clear an application's follow-up date from a calendar picker.

    Used by the board cards, the table and the detail page, so it works without
    opening the detail page. The date comes from a picker, so it cannot be mistyped.
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
                "Save",
                on_click=lambda: save(picked.value, label.value, "Follow-up saved"),
            ).props("data-testid=follow-up-save")
    dialog.open()


def _follow_up_card_html(a) -> str:
    """The follow-up line of a board card, with a button that asks the server to open
    the follow-up dialog (the id is a number from our own database)."""
    text = _follow_up_text(a.follow_up_date, a.follow_up_label)
    action = "Change" if a.follow_up_date else "Set follow-up"
    shown = f"Follow up: {html.escape(text)} " if text else ""
    return (
        f"{shown}"
        f'<a href="#" style="text-decoration:underline" '
        f"onclick=\"emitEvent('follow_up', {{id: {int(a.id)}}}); return false;\">{action}</a>"
    )


def _board_html(applications) -> str:
    """The board as one HTML block (much lighter than thousands of widgets).

    Everything shown is escaped. The only links go to our own detail page, and the
    follow-up button sends the application's id (a number) back to the server.
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
            f'<div style="opacity:.7;font-size:.85em">{a.date_applied.isoformat()}</div>'
            f'<div style="font-size:.85em">{_follow_up_card_html(a)}</div></div>'
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
    with ui.row().classes("items-center gap-4 mt-4"):
        ui.label("Applications").classes("text-lg")
        if profile_id is not None:
            ui.button("Export CSV", icon="download", on_click=lambda: export_csv()).props(
                "flat data-testid=export-button"
            )
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
                    "follow_up": _follow_up_text(a.follow_up_date, a.follow_up_label),
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
        # The calendar button in each row opens the same follow-up dialog as the board cards.
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
            'target="_blank" rel="noopener">Open</a></q-td>',
        )

    def open_follow_up(event) -> None:
        """Open the follow-up dialog for the application whose button was clicked."""
        application_id = event.args.get("id") if isinstance(event.args, dict) else None
        if isinstance(application_id, int):
            _follow_up_dialog(profile_id, application_id, results.refresh)

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
    ui.link("< Back to applications", "/applications")
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

    with ui.row().classes("items-center gap-2"):
        text = _follow_up_text(application.follow_up_date, application.follow_up_label)
        ui.label(f"Follow-up: {text}" if text else "No follow-up set").props(
            "data-testid=follow-up"
        )
        ui.button(
            "Change follow-up" if text else "Set follow-up",
            icon="event",
            on_click=lambda: _follow_up_dialog(profile_id, application_id, ui.navigate.reload),
        ).props("flat")

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


def _data_content() -> None:
    ui.label("Your data").classes("text-lg mt-4")
    ui.label(
        "Everything lives on this computer. A backup is one zip file with all profiles, "
        "applications and resumes, so keep a copy somewhere safe (a USB stick or a cloud folder)."
    ).classes("max-w-2xl")

    saved = ui.label("").props("data-testid=backup-saved")

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
        "data-testid=backup-button"
    ).classes("mt-2")

    ui.label("Database check").classes("text-lg mt-6")

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
    ui.button("Check again", on_click=health.refresh).props("flat")

    ui.label("Restoring a backup").classes("text-lg mt-6")
    ui.label(
        "Restoring replaces your data, so it is done from a terminal with this app stopped. "
        "If data already exists you are asked to confirm, and the old data is moved aside, "
        "not deleted."
    ).classes("max-w-2xl")
    ui.code("uv run apply-spy restore PATH/TO/backup.zip", language="bash")


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
        port=PORT,
        reload=False,
        show=True,
        storage_secret="apply-spy-local",
    )
