"""The home page: what needs attention (follow-ups), at a glance."""

import calendar
import html
from datetime import date

from nicegui import ui

from apply_spy.db import session_scope
from apply_spy.tracker import (
    calendar_month,
    count_completed_follow_ups,
    list_applications,
    list_completed_follow_ups,
    needs_attention,
    upcoming_follow_ups,
)
from apply_spy.ui.follow_up_actions import FollowUpActions
from apply_spy.ui.follow_up_html import company_link, completed_table_html, group_html
from apply_spy.ui.layout import Shell, app_shell, no_profile_card
from apply_spy.ui.state import engine
from apply_spy.ui.theme import (
    follow_up_text,
    greeting,
    icon,
    long_date,
    short_date,
    status_chip,
)


def applications_table_html(applications) -> str:
    rows = [
        '<div class="as-arow head"><div>Company</div><div>Title</div><div>Status</div>'
        "<div>Applied</div><div>Follow-up</div></div>"
    ]
    for a in applications:
        rows.append(
            '<div class="as-arow">'
            f"{company_link(a)}<div>{html.escape(a.title)}</div><div>{status_chip(a.status)}</div>"
            f"<div>{long_date(a.date_applied)}</div>"
            f"<div>{html.escape(follow_up_text(a.follow_up_date, a.follow_up_label))}</div></div>"
        )
    return f'<div class="as-ftable">{"".join(rows)}</div>'


def stats_html(found, completed: int) -> str:
    def card(tone: str, icon_name: str, number: int, label: str, test_id: str) -> str:
        return (
            f'<div class="as-stat {tone}" data-testid="tile-{test_id}">'
            f'<div class="as-stat-icon">{icon(icon_name)}</div><div>'
            f'<div class="as-stat-num">{number}</div><div class="as-stat-label">{label}</div>'
            "</div></div>"
        )

    return (
        '<div class="as-stats">'
        + card("red", "schedule", len(found.overdue), "Overdue", "overdue")
        + card("amber", "event", len(found.due_today), "Due Today", "today")
        + card("blue", "date_range", len(found.this_week), "This Week", "this-week")
        + card("green", "task_alt", completed, "Completed", "completed")
        + "</div>"
    )


def calendar_html(month) -> str:
    """The compact month grid: each day with a follow-up gets a coloured square
    (overdue red, today yellow, later blue). Hover shows which applications."""
    cells = [
        f'<div class="as-cal-head">{name}</div>'
        for name in ("Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat")
    ]
    for week in month.weeks:
        for cell in week:
            if not cell.in_month:
                cells.append("<div></div>")
                continue
            kind = ""
            if cell.applications:
                kind = (
                    "overdue"
                    if cell.day < month.today
                    else "today"
                    if cell.day == month.today
                    else "upcoming"
                )
            elif cell.day == month.today:
                kind = "today"
            names = ", ".join(a.company for a in cell.applications)
            tip = f' title="{html.escape(names)}"' if names else ""
            cells.append(f'<div class="as-day {kind}"{tip}>{cell.day.day}</div>')
    return f'<div class="as-cal-grid" data-testid="calendar">{"".join(cells)}</div>'


def upcoming_html(applications, today: date) -> str:
    if not applications:
        return '<div class="as-empty">No upcoming follow-ups.</div>'
    rows = []
    for a in applications:
        is_today = a.follow_up_date == today
        weekday = "Today" if is_today else f"{a.follow_up_date:%a}"
        what = a.follow_up_label or a.title
        rows.append(
            '<div class="as-upcoming-row">'
            f'<div class="as-dot {"today" if is_today else "upcoming"}"></div><div>'
            f'<div><b>{weekday}</b> <span class="as-muted">'
            f"{short_date(a.follow_up_date)}</span></div>"
            f'<div><a href="/applications/{int(a.id)}">{html.escape(a.company)}</a>'
            f" – {html.escape(what)}</div></div></div>"
        )
    return "".join(rows)


@ui.page("/")
def dashboard_page() -> None:
    with app_shell("dashboard") as shell:
        if shell.profile_id is None:
            no_profile_card()
            return
        _build(shell)


def _build(shell: Shell) -> None:
    profile_id = shell.profile_id
    today = date.today()
    state = {"tab": "followups", "year": today.year, "month": today.month}

    def refresh_all() -> None:
        overview.refresh()
        month_view.refresh()
        upcoming.refresh()
        shell.refresh_badge()

    actions = FollowUpActions(profile_id, refresh_all, lambda: overview.refresh())
    actions.register()

    @ui.refreshable
    def overview() -> None:
        with session_scope(engine) as session:
            found = needs_attention(session, profile_id)
            completed_count = count_completed_follow_ups(session, profile_id)
            completed = list_completed_follow_ups(session, profile_id, limit=20)
            recent = list_applications(session, profile_id, limit=8)

        actions.undo_bar()

        ui.html(stats_html(found, completed_count), sanitize=False).classes("w-full")

        with (
            ui.tabs(value=state["tab"], on_change=lambda e: state.update(tab=e.value))
            .classes("as-tabs w-full")
            .props("align=left inline-label active-color=primary indicator-color=primary") as tabs
        ):
            with ui.tab("followups", label="Follow-ups", icon="event_available").props(
                "data-testid=tab-followups"
            ):
                if found.urgent:
                    ui.badge(str(found.urgent), color="primary").classes("ml-2")
            ui.tab("applications", label="Applications", icon="description").props(
                "data-testid=tab-applications"
            )
            ui.tab("completed", label="Completed", icon="check").props("data-testid=tab-completed")
        ui.separator().classes("-mt-3")

        with ui.tab_panels(tabs, value=state["tab"]).classes("w-full bg-transparent"):
            with ui.tab_panel("followups").classes("p-0 gap-4"):
                if found.total == 0:
                    with (
                        ui.element("div").classes("as-card as-empty").props("data-testid=all-clear")
                    ):
                        ui.label("Nothing needs your attention right now.").classes("as-title")
                        ui.label(
                            "Set a follow-up date on an application and it shows up here "
                            "when it is due within a week."
                        )
                for title, tone, icon_name, key, apps in (
                    ("Overdue", "red", "schedule", "overdue", found.overdue),
                    ("Today", "amber", "event", "today", found.due_today),
                    ("This Week", "blue", "date_range", "this-week", found.this_week),
                ):
                    if apps:
                        ui.html(
                            group_html(title, tone, icon_name, apps, found.today, key),
                            sanitize=False,
                        ).classes("w-full")
            with ui.tab_panel("applications").classes("p-0"):
                with ui.element("div").classes("as-card"):
                    if recent:
                        ui.html(applications_table_html(recent), sanitize=False)
                        ui.html(
                            '<div class="as-more"><a class="as-link" href="/applications">'
                            "View all applications</a></div>",
                            sanitize=False,
                        )
                    else:
                        ui.html('<div class="as-empty">No applications yet.</div>', sanitize=False)
            with ui.tab_panel("completed").classes("p-0"):
                with ui.element("div").classes("as-card").props("data-testid=completed-list"):
                    if completed:
                        ui.html(completed_table_html(completed), sanitize=False)
                    else:
                        ui.html(
                            '<div class="as-empty">Nothing completed yet. Mark a follow-up '
                            "complete and it is kept here.</div>",
                            sanitize=False,
                        )

    def shift(months: int) -> None:
        index = state["year"] * 12 + (state["month"] - 1) + months
        state["year"], state["month"] = index // 12, index % 12 + 1
        month_view.refresh()

    def go_to_today() -> None:
        state["year"], state["month"] = date.today().year, date.today().month
        month_view.refresh()

    @ui.refreshable
    def month_view() -> None:
        with session_scope(engine) as session:
            month = calendar_month(session, profile_id, state["year"], state["month"])
        with ui.element("div").classes("as-card w-full"):
            with ui.row().classes("w-full items-center justify-between no-wrap"):
                ui.button(icon="chevron_left", on_click=lambda: shift(-1)).props(
                    "flat round dense data-testid=calendar-previous"
                )
                ui.label(f"{calendar.month_name[month.month]} {month.year}").classes(
                    "text-xl font-bold"
                ).props("data-testid=calendar-title")
                ui.button(icon="chevron_right", on_click=lambda: shift(1)).props(
                    "flat round dense data-testid=calendar-next"
                )
            ui.html(calendar_html(month), sanitize=False).classes("w-full mt-2")
            if (month.year, month.month) != (today.year, today.month):
                ui.button("Back to today", on_click=go_to_today).props("flat dense no-caps")

    @ui.refreshable
    def upcoming() -> None:
        with session_scope(engine) as session:
            coming = upcoming_follow_ups(session, profile_id, limit=5)
        with ui.element("div").classes("as-card w-full").props("data-testid=upcoming"):
            ui.html(
                f'<div class="as-title" style="display:flex;align-items:center;gap:10px">'
                f"{icon('event')}Upcoming Follow-ups</div>",
                sanitize=False,
            ).classes("mb-2")
            ui.html(upcoming_html(coming, today), sanitize=False)
            ui.html(
                '<div class="as-more"><a class="as-link" href="/follow-ups">'
                "View all follow-ups</a></div>",
                sanitize=False,
            )

    with ui.row().classes("w-full items-start gap-6").style("flex-wrap: wrap"):
        with ui.column().classes("gap-5").style("flex: 1 1 640px; min-width: 0"):
            ui.html(
                f'<div class="as-h1" data-testid="greeting">{greeting()}, '
                f"{html.escape(shell.profile.name or '')} 👋</div>"
                '<div class="as-sub">Here\'s your job application overview.</div>',
                sanitize=False,
            )
            overview()
        with ui.column().classes("gap-5").style("flex: 0 0 380px; max-width: 100%"):
            month_view()
            upcoming()
