"""HTML for follow-up lists, shared by the Dashboard and the Follow-ups page.

Everything that comes from the user is escaped. The buttons send page-level events
(see follow_up_actions.py) with the application's id, which is a number from our own
database.
"""

import html
from datetime import date

from apply_spy.ui.follow_up_actions import event_js
from apply_spy.ui.theme import chip, days_text, icon, long_date, short_date, status_chip

ROWS_PER_GROUP = 10  # the Follow-ups page lists them all


def company_link(application) -> str:
    return (
        f'<div class="as-fco">{icon("apartment")}'
        f'<a href="/applications/{int(application.id)}">'
        f"{html.escape(application.company)}</a></div>"
    )


def follow_up_table_html(applications, today: date) -> str:
    """Rows of follow-ups, each with Mark Complete and Change date buttons."""
    rows = [
        '<div class="as-frow head"><div>Follow-up</div><div>Label</div><div>Company</div>'
        "<div>Title</div><div>Status</div><div></div></div>"
    ]
    for a in applications:
        days = (a.follow_up_date - today).days
        when = long_date(a.follow_up_date)
        if days < 0:
            due = f'<div class="as-fdate overdue">{when} <small>({days_text(days)})</small></div>'
        elif days == 0:
            due = f'<div class="as-fdate">{when} (Today)</div>'
        else:
            due = f'<div class="as-fdate">{when}</div>'
        rows.append(
            '<div class="as-frow">'
            f"{due}"
            f"<div>{chip(a.follow_up_label) if a.follow_up_label else ''}</div>"
            f"{company_link(a)}"
            f"<div>{html.escape(a.title)}</div>"
            f"<div>{status_chip(a.status)}</div>"
            '<div class="as-actions">'
            f'<button class="as-complete" data-testid="complete-{int(a.id)}" '
            f'onclick="{event_js("follow_up_done", a.id)}">{icon("check")} Mark Complete</button>'
            f'<button class="as-iconbtn" title="Change date" data-testid="change-{int(a.id)}" '
            f'onclick="{event_js("follow_up", a.id)}">{icon("event")}</button>'
            "</div></div>"
        )
    return f'<div class="as-ftable">{"".join(rows)}</div>'


def group_html(
    title: str,
    tone: str,
    icon_name: str,
    applications,
    today: date,
    key: str,
    limit: int | None = ROWS_PER_GROUP,
) -> str:
    """A card for one group (Overdue, Today, ...). `limit` caps the rows shown; None shows all."""
    shown = applications if limit is None else applications[:limit]
    extra = len(applications) - len(shown)
    more_link = (
        f'<div class="as-more"><a class="as-link" href="/follow-ups">'
        f"View all {len(applications)} in Follow-ups</a></div>"
        if extra > 0
        else ""
    )
    return (
        f'<div class="as-card as-group" data-testid="group-{key}">'
        f'<div class="as-group-title {tone}">{icon(icon_name)}{html.escape(title)} '
        f"({len(applications)})</div>"
        f"{follow_up_table_html(shown, today)}{more_link}</div>"
    )


def completed_table_html(records) -> str:
    rows = [
        '<div class="as-frow head"><div>Completed</div><div>Label</div><div>Company</div>'
        "<div>Title</div><div>Was due</div><div></div></div>"
    ]
    for r in records:
        done = r.completed_at.astimezone()
        rows.append(
            '<div class="as-frow">'
            f"<div>{long_date(done.date())}</div>"
            f"<div>{chip(r.label) if r.label else ''}</div>"
            f"{company_link(r.application)}"
            f"<div>{html.escape(r.application.title)}</div>"
            f"<div>{short_date(r.due_date)}</div>"
            '<div class="as-actions">'
            f'<button class="as-iconbtn" title="Undo: put this follow-up back" '
            f'data-testid="undo-{int(r.id)}" onclick="{event_js("undo_completed", r.id)}">'
            f"{icon('undo')}</button></div></div>"
        )
    return f'<div class="as-ftable">{"".join(rows)}</div>'
