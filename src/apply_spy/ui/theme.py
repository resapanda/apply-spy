"""How apply-spy looks: colours, shared CSS, and small pieces of HTML (chips, dates).

Everything here is plain text and CSS, with no web fonts or other downloads, so the
app stays fully offline. Anything that goes into HTML is escaped first.
"""

import html
from datetime import date, datetime
from pathlib import Path

from nicegui import ui

PRIMARY = "#2563eb"
POSITIVE = "#16a34a"
NEGATIVE = "#dc2626"
WARNING = "#d97706"

# Chip colours for each status: (text, background).
_STATUS_COLOURS = {
    "Saved": ("#475569", "#e2e8f0"),
    "Applied": ("#1d4ed8", "#dbeafe"),
    "Recruiter screen": ("#4338ca", "#e0e7ff"),
    "Online assessment": ("#7e22ce", "#f3e8ff"),
    "Interview": ("#0f766e", "#ccfbf1"),
    "Offer": ("#15803d", "#dcfce7"),
    "Accepted": ("#166534", "#bbf7d0"),
    "Rejected": ("#b91c1c", "#fee2e2"),
    "Withdrawn": ("#475569", "#e2e8f0"),
    "Ghosted": ("#b45309", "#fef3c7"),
}

CSS_FILE = Path(__file__).with_name("theme.css")


def install_theme() -> None:
    """Add the shared CSS to every page (call once when the app starts)."""
    ui.add_css(CSS_FILE, shared=True)


def apply_colors() -> None:
    """Quasar's own colours for buttons, switches and so on (set for each page)."""
    ui.colors(primary=PRIMARY, positive=POSITIVE, negative=NEGATIVE, warning=WARNING)


# --- Small pieces of HTML. Everything that comes from the user is escaped. ------------


def icon(name: str) -> str:
    """A Material icon (the font ships with the app, so it works offline)."""
    return f'<span class="material-icons-outlined">{html.escape(name)}</span>'


def chip(text: str) -> str:
    return f'<span class="as-chip">{html.escape(text)}</span>'


def status_chip(status: str) -> str:
    colours = _STATUS_COLOURS.get(status)
    style = f' style="color:{colours[0]};background:{colours[1]}"' if colours else ""
    return f'<span class="as-chip"{style}>{html.escape(status)}</span>'


def long_date(day: date) -> str:
    """Nov 19, 2026"""
    return f"{day:%b} {day.day}, {day.year}"


def short_date(day: date) -> str:
    """Nov 19"""
    return f"{day:%b} {day.day}"


def days_text(days: int) -> str:
    """How far a date is from today, in words."""
    if days < 0:
        return f"{-days} day{'s' if days != -1 else ''} overdue"
    if days == 0:
        return "Today"
    return f"in {days} day{'s' if days != 1 else ''}"


def follow_up_text(follow_up_date: date | None, label: str | None) -> str:
    """ "2026-11-25 · OA due", or "" when there is no follow-up."""
    if follow_up_date is None:
        return ""
    return f"{follow_up_date.isoformat()} · {label}" if label else follow_up_date.isoformat()


def greeting(now: datetime | None = None) -> str:
    """Good morning / afternoon / evening, by the computer's local time."""
    hour = (now or datetime.now()).hour
    return "Good morning" if hour < 12 else "Good afternoon" if hour < 18 else "Good evening"
