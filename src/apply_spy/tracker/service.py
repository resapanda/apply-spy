"""Application tracker logic. The web UI and the CLI both call these functions.

Profile isolation: every function takes a profile_id and only ever touches
that profile's rows.
"""

import calendar
import csv
import io
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from apply_spy.db.models import Application, CompletedFollowUp, Profile, StatusEvent
from apply_spy.profiles import ProfileNotFound

STATUSES = (
    "Saved",
    "Applied",
    "Recruiter screen",
    "Online assessment",
    "Interview",
    "Offer",
    "Accepted",
    "Rejected",
    "Withdrawn",
    "Ghosted",
)
DEFAULT_STATUS = "Applied"

# Nothing more to do on these, so their follow-ups are not shown as needing attention.
CLOSED_STATUSES = ("Accepted", "Rejected", "Withdrawn")
# "This week" means tomorrow up to this many days ahead.
ATTENTION_DAYS_AHEAD = 7

# "created" is when the application was added; the others are what you see in the table.
SORT_FIELDS = ("created", "date_applied", "company", "status")

MAX_SHORT = 200
MAX_URL = 2000
MAX_SALARY = 100
MAX_FOLLOW_UP_LABEL = 100

# Fields that can be changed later with update_application_details.
# Everything except status, which must go through change_status so history is written.
EDITABLE_FIELDS = (
    "company",
    "title",
    "url",
    "location",
    "salary_range",
    "resume_version",
    "notes",
    "date_applied",
)


class InvalidApplication(ValueError):
    """A field is missing or not acceptable. `field` names it, for the message."""

    def __init__(self, field: str, message: str):
        super().__init__(message)
        self.field = field


class ApplicationNotFound(LookupError):
    """No application with this id in this profile."""


class InvalidStatus(ValueError):
    """The status is not one of STATUSES."""


class CompletedFollowUpNotFound(LookupError):
    """No completed follow-up with this id in this profile."""


def _short_text(value: str | None) -> str | None:
    """Trim and collapse spaces; empty becomes None."""
    cleaned = " ".join((value or "").split())
    return cleaned or None


def _required(label: str, value: str | None) -> str:
    cleaned = _short_text(value)
    if cleaned is None:
        raise InvalidApplication(label.lower(), f"{label} is required.")
    if len(cleaned) > MAX_SHORT:
        raise InvalidApplication(label.lower(), f"{label} can be at most {MAX_SHORT} characters.")
    return cleaned


def _optional(label: str, field: str, value: str | None, max_length: int) -> str | None:
    cleaned = _short_text(value)
    if cleaned is not None and len(cleaned) > max_length:
        raise InvalidApplication(field, f"{label} can be at most {max_length} characters.")
    return cleaned


def _url(value: str | None) -> str | None:
    cleaned = _short_text(value)
    if cleaned is None:
        return None
    if not cleaned.lower().startswith(("http://", "https://")):
        raise InvalidApplication("link", "Link must start with http:// or https://")
    if len(cleaned) > MAX_URL:
        raise InvalidApplication("link", f"Link can be at most {MAX_URL} characters.")
    return cleaned


def _notes(value: str | None) -> str | None:
    cleaned = (value or "").strip()
    return cleaned or None


def _follow_up_date(value: date | str | None) -> date | None:
    """A date, or text in the form 2026-11-30. Empty means no date. Anything else is rejected."""
    if value is None or isinstance(value, date):
        return value
    text = value.strip()
    if not text:
        return None
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        raise InvalidApplication(
            "follow-up date", "Follow-up date must look like 2026-11-30 (year-month-day)."
        )
    try:
        return date.fromisoformat(text)
    except ValueError as error:
        raise InvalidApplication(
            "follow-up date", f"{text} is not a real date. Use the form 2026-11-30."
        ) from error


def create_application(
    session: Session,
    profile_id: int,
    company: str,
    title: str,
    url: str | None = None,
    *,
    location: str | None = None,
    salary_range: str | None = None,
    resume_version: str | None = None,
    notes: str | None = None,
    date_applied: date | None = None,
) -> Application:
    """Add an application with status "Applied" and today's date (unless given).

    Only company and title are required. The first status event is recorded too,
    so the history is complete from the start.
    """
    if session.get(Profile, profile_id) is None:
        raise ProfileNotFound(f"No profile with id {profile_id}.")

    application = Application(
        profile_id=profile_id,
        company=_required("Company", company),
        title=_required("Title", title),
        url=_url(url),
        location=_optional("Location", "location", location, MAX_SHORT),
        salary_range=_optional("Salary range", "salary range", salary_range, MAX_SALARY),
        resume_version=_optional("Resume version", "resume version", resume_version, MAX_SHORT),
        notes=_notes(notes),
        status=DEFAULT_STATUS,
        date_applied=date_applied or date.today(),
    )
    session.add(application)
    session.flush()
    session.add(
        StatusEvent(application_id=application.id, old_status=None, new_status=application.status)
    )
    session.flush()
    return application


def update_application_details(
    session: Session, profile_id: int, application_id: int, **fields: str | date | None
) -> Application:
    """Change any detail of an application (not its status).

    Fields not passed are left as they are. Every value is checked before
    anything is changed, so a bad value changes nothing.
    """
    unknown = set(fields) - set(EDITABLE_FIELDS)
    if unknown:
        raise ValueError(f"Cannot edit: {', '.join(sorted(unknown))}")
    application = get_application(session, profile_id, application_id)

    cleaners = {
        "company": lambda v: _required("Company", v),
        "title": lambda v: _required("Title", v),
        "url": _url,
        "location": lambda v: _optional("Location", "location", v, MAX_SHORT),
        "salary_range": lambda v: _optional("Salary range", "salary range", v, MAX_SALARY),
        "resume_version": lambda v: _optional("Resume version", "resume version", v, MAX_SHORT),
        "notes": _notes,
    }
    cleaned = {}
    for name, value in fields.items():
        if name == "date_applied":
            if not isinstance(value, date):
                raise InvalidApplication("date applied", "Date applied is required.")
            cleaned[name] = value
        else:
            cleaned[name] = cleaners[name](value)

    for name, value in cleaned.items():
        setattr(application, name, value)
    session.flush()
    return application


def set_follow_up(
    session: Session,
    profile_id: int,
    application_id: int,
    follow_up_date: date | str | None,
    label: str | None = None,
) -> Application:
    """Set (or clear) the follow-up date and its optional label, e.g. "OA due".

    Passing no date clears the follow-up and its label. A label needs a date.
    The date can be a date or text like 2026-11-30; anything else is rejected
    with a message, and nothing is changed.
    """
    parsed = _follow_up_date(follow_up_date)
    cleaned_label = _optional("Label", "follow-up label", label, MAX_FOLLOW_UP_LABEL)
    if parsed is None and cleaned_label is not None:
        raise InvalidApplication("follow-up date", "Pick a follow-up date for this label.")
    application = get_application(session, profile_id, application_id)
    application.follow_up_date = parsed
    application.follow_up_label = cleaned_label
    session.flush()
    return application


def complete_follow_up(
    session: Session, profile_id: int, application_id: int
) -> CompletedFollowUp | None:
    """Mark an application's follow-up as done.

    The follow-up is cleared (so it leaves the dashboard and calendar) and a record of
    it is kept in the completed list. Returns that record, so the caller can offer
    Undo (see undo_complete_follow_up). Nothing to complete is not an error: it
    returns None and changes nothing.
    """
    application = get_application(session, profile_id, application_id)
    if application.follow_up_date is None:
        return None
    record = CompletedFollowUp(
        application_id=application.id,
        due_date=application.follow_up_date,
        label=application.follow_up_label,
    )
    session.add(record)
    application.follow_up_date = None
    application.follow_up_label = None
    session.flush()
    return record


def undo_complete_follow_up(session: Session, profile_id: int, completed_id: int) -> Application:
    """Put a completed follow-up back: it returns to the application and leaves the
    completed list. If the application has been given a new follow-up since, nothing
    is changed and an error explains why."""
    record = session.scalar(
        select(CompletedFollowUp)
        .join(Application, CompletedFollowUp.application_id == Application.id)
        .where(CompletedFollowUp.id == completed_id, Application.profile_id == profile_id)
    )
    if record is None:
        raise CompletedFollowUpNotFound(f"No completed follow-up with id {completed_id}.")
    application = record.application
    if application.follow_up_date is not None:
        raise InvalidApplication(
            "follow-up date",
            "This application already has a new follow-up, so the old one was not restored.",
        )
    application.follow_up_date = record.due_date
    application.follow_up_label = record.label
    session.delete(record)
    session.flush()
    return application


def list_completed_follow_ups(
    session: Session, profile_id: int, limit: int | None = None
) -> list[CompletedFollowUp]:
    """This profile's completed follow-ups, most recently completed first."""
    query = (
        select(CompletedFollowUp)
        .join(Application, CompletedFollowUp.application_id == Application.id)
        .where(Application.profile_id == profile_id)
        .order_by(CompletedFollowUp.completed_at.desc(), CompletedFollowUp.id.desc())
    )
    if limit is not None:
        query = query.limit(limit)
    return list(session.scalars(query).unique())


def count_completed_follow_ups(session: Session, profile_id: int) -> int:
    return (
        session.scalar(
            select(func.count())
            .select_from(CompletedFollowUp)
            .join(Application, CompletedFollowUp.application_id == Application.id)
            .where(Application.profile_id == profile_id)
        )
        or 0
    )


def _today() -> date:
    """The computer's local date. A separate function so tests can fix the clock."""
    return date.today()


@dataclass
class NeedsAttention:
    """Follow-ups grouped by urgency, each group ordered soonest first."""

    today: date
    overdue: list[Application] = field(default_factory=list)  # before today
    due_today: list[Application] = field(default_factory=list)
    this_week: list[Application] = field(default_factory=list)  # tomorrow .. 7 days ahead

    @property
    def total(self) -> int:
        return len(self.overdue) + len(self.due_today) + len(self.this_week)


def needs_attention(session: Session, profile_id: int, today: date | None = None) -> NeedsAttention:
    """This profile's follow-ups that are overdue, due today, or due within 7 days.

    Uses the computer's local date unless `today` is given (tests pass a fixed
    date). Follow-ups further away are left out, and so are applications that are
    closed (Accepted, Rejected, Withdrawn).
    """
    today = today or _today()
    last_day = today + timedelta(days=ATTENTION_DAYS_AHEAD)
    found = session.scalars(
        select(Application)
        .where(
            Application.profile_id == profile_id,
            Application.follow_up_date.is_not(None),
            Application.follow_up_date <= last_day,
            Application.status.not_in(CLOSED_STATUSES),
        )
        .order_by(Application.follow_up_date, func.lower(Application.company), Application.id)
    )
    groups = NeedsAttention(today=today)
    for application in found:
        if application.follow_up_date < today:
            groups.overdue.append(application)
        elif application.follow_up_date == today:
            groups.due_today.append(application)
        else:
            groups.this_week.append(application)
    return groups


@dataclass
class CalendarDay:
    day: date
    in_month: bool  # False for the grey days that fill the first and last week
    applications: list[Application] = field(default_factory=list)  # follow-ups due this day


@dataclass
class CalendarMonth:
    year: int
    month: int
    today: date
    weeks: list[list[CalendarDay]]  # whole weeks, Sunday first


def calendar_month(
    session: Session, profile_id: int, year: int, month: int, today: date | None = None
) -> CalendarMonth:
    """A month grid with each day's follow-ups, for the dashboard calendar.

    Weeks run Sunday to Saturday and are whole, so the first and last rows include
    days of the neighbouring months (marked in_month=False) and their follow-ups too.
    Closed applications (Accepted, Rejected, Withdrawn) are left out, as on the
    Needs attention lists. Each day's applications are ordered by company.
    """
    if not 1 <= month <= 12:
        raise ValueError(f"Month must be 1 to 12, not {month}.")
    weeks = calendar.Calendar(firstweekday=calendar.SUNDAY).monthdatescalendar(year, month)
    first, last = weeks[0][0], weeks[-1][-1]
    found = session.scalars(
        select(Application)
        .where(
            Application.profile_id == profile_id,
            Application.follow_up_date.is_not(None),
            Application.follow_up_date >= first,
            Application.follow_up_date <= last,
            Application.status.not_in(CLOSED_STATUSES),
        )
        .order_by(func.lower(Application.company), Application.id)
    )
    by_day: dict[date, list[Application]] = {}
    for application in found:
        by_day.setdefault(application.follow_up_date, []).append(application)
    return CalendarMonth(
        year=year,
        month=month,
        today=today or _today(),
        weeks=[
            [CalendarDay(d, d.month == month, by_day.get(d, [])) for d in week] for week in weeks
        ],
    )


def count_follow_ups(session: Session, profile_id: int) -> int:
    """How many open follow-ups this profile has, however far away: exactly the ones the
    Follow-ups page lists (overdue, today, this week and later). Closed applications
    are left out, as everywhere. This is the number on the sidebar badge."""
    return (
        session.scalar(
            select(func.count())
            .select_from(Application)
            .where(
                Application.profile_id == profile_id,
                Application.follow_up_date.is_not(None),
                Application.status.not_in(CLOSED_STATUSES),
            )
        )
        or 0
    )


def upcoming_follow_ups(
    session: Session, profile_id: int, today: date | None = None, limit: int = 5
) -> list[Application]:
    """The next follow-ups from today onwards, soonest first (the dashboard's
    'Upcoming Follow-ups' list). Closed applications are left out."""
    today = today or _today()
    return list(
        session.scalars(
            select(Application)
            .where(
                Application.profile_id == profile_id,
                Application.follow_up_date.is_not(None),
                Application.follow_up_date >= today,
                Application.status.not_in(CLOSED_STATUSES),
            )
            .order_by(Application.follow_up_date, func.lower(Application.company), Application.id)
            .limit(limit)
        )
    )


def later_follow_ups(
    session: Session, profile_id: int, today: date | None = None
) -> list[Application]:
    """Follow-ups further away than the 'this week' window, soonest first."""
    today = today or _today()
    return list(
        session.scalars(
            select(Application)
            .where(
                Application.profile_id == profile_id,
                Application.follow_up_date.is_not(None),
                Application.follow_up_date > today + timedelta(days=ATTENTION_DAYS_AHEAD),
                Application.status.not_in(CLOSED_STATUSES),
            )
            .order_by(Application.follow_up_date, func.lower(Application.company), Application.id)
        )
    )


def list_applications(
    session: Session,
    profile_id: int,
    *,
    status: str | None = None,
    search: str | None = None,
    sort_by: str = "created",
    descending: bool = True,
    limit: int | None = None,
) -> list[Application]:
    """This profile's applications. The table and the board both use this.

    status: only this status. search: case-insensitive text found in the
    company, title or location (% and _ are treated as plain characters).
    sort_by: one of SORT_FIELDS; "status" sorts in pipeline order (Saved first).
    The default is newest-added first. limit: at most this many (after sorting).
    """
    if status is not None and status not in STATUSES:
        raise InvalidStatus(f"Unknown status: {status!r}.")
    if sort_by not in SORT_FIELDS:
        raise ValueError(f"Cannot sort by {sort_by!r}.")

    query = select(Application).where(Application.profile_id == profile_id)
    if status is not None:
        query = query.where(Application.status == status)
    term = _short_text(search)
    if term is not None:
        query = query.where(
            Application.company.icontains(term, autoescape=True)
            | Application.title.icontains(term, autoescape=True)
            | Application.location.icontains(term, autoescape=True)
        )

    sort_columns = {
        "created": Application.created_at,
        "date_applied": Application.date_applied,
        "company": func.lower(Application.company),
        "status": case({name: i for i, name in enumerate(STATUSES)}, value=Application.status),
    }
    column = sort_columns[sort_by]
    primary = column.desc() if descending else column.asc()
    # Ties: the most recently added first, so the order is always predictable.
    query = query.order_by(primary, Application.created_at.desc(), Application.id.desc())
    if limit is not None:
        query = query.limit(limit)
    return list(session.scalars(query))


def get_application(session: Session, profile_id: int, application_id: int) -> Application:
    """One application, but only if it belongs to this profile."""
    application = session.scalar(
        select(Application).where(
            Application.id == application_id, Application.profile_id == profile_id
        )
    )
    if application is None:
        raise ApplicationNotFound(f"No application with id {application_id} in this profile.")
    return application


def change_status(
    session: Session, profile_id: int, application_id: int, new_status: str
) -> Application:
    """Move an application to a new status. The ONLY place a status changes.

    The new status and its history event are written in the caller's single
    transaction (session_scope), so they succeed or fail together. Choosing the
    status it already has changes nothing and adds no event.
    """
    if new_status not in STATUSES:
        raise InvalidStatus(f"Unknown status: {new_status!r}.")
    application = get_application(session, profile_id, application_id)
    if application.status == new_status:
        return application

    old_status = application.status
    application.status = new_status
    session.add(
        StatusEvent(application_id=application.id, old_status=old_status, new_status=new_status)
    )
    session.flush()
    return application


def list_status_events(session: Session, profile_id: int, application_id: int) -> list[StatusEvent]:
    """The application's history, oldest first. Includes the first event
    (no old status) that records the application being added."""
    application = get_application(session, profile_id, application_id)
    return list(
        session.scalars(
            select(StatusEvent)
            .where(StatusEvent.application_id == application.id)
            .order_by(StatusEvent.id)
        )
    )


# --- CSV export ---------------------------------------------------------

# Header text and where each value comes from. This one list is the file's layout.
EXPORT_COLUMNS = (
    ("Company", "company"),
    ("Title", "title"),
    ("Link", "url"),
    ("Location", "location"),
    ("Salary range", "salary_range"),
    ("Resume version", "resume_version"),
    ("Status", "status"),
    ("Date applied", "date_applied"),
    ("Follow-up date", "follow_up_date"),
    ("Follow-up label", "follow_up_label"),
    ("Notes", "notes"),
    ("Added", "created_at"),
)

# A spreadsheet treats a cell that starts with one of these as a formula.
_FORMULA_STARTS = ("=", "+", "-", "@", "\t", "\r")


def _csv_cell(value) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        text = value.isoformat(timespec="seconds")  # e.g. 2026-11-19T01:19:16+00:00 (UTC)
    elif isinstance(value, date):
        text = value.isoformat()
    else:
        text = str(value)
    if text.startswith(_FORMULA_STARTS):
        return "'" + text  # shown as plain text instead of being run as a formula
    return text


def export_applications_csv(session: Session, profile_id: int) -> str:
    """All of this profile's applications as CSV text: a header row, then one
    row per application, oldest first. Quotes and line breaks inside a value
    (such as notes) are handled by Python's csv module.

    Encode as UTF-8 when saving. Cells that start with = + - or @ get a leading
    ' so a spreadsheet shows them as text rather than running them as formulas.
    """
    if session.get(Profile, profile_id) is None:
        raise ProfileNotFound(f"No profile with id {profile_id}.")
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow([header for header, _ in EXPORT_COLUMNS])
    for application in list_applications(session, profile_id, sort_by="created", descending=False):
        writer.writerow([_csv_cell(getattr(application, field)) for _, field in EXPORT_COLUMNS])
    return out.getvalue()
