"""Application tracker logic. The web UI and the CLI both call these functions.

Profile isolation: every function takes a profile_id and only ever touches
that profile's rows.
"""

import csv
import io
import re
from datetime import date

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from apply_spy.db.models import Application, Profile, StatusEvent
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


def list_applications(
    session: Session,
    profile_id: int,
    *,
    status: str | None = None,
    search: str | None = None,
    sort_by: str = "created",
    descending: bool = True,
) -> list[Application]:
    """This profile's applications. The table and the board both use this.

    status: only this status. search: case-insensitive text found in the
    company, title or location (% and _ are treated as plain characters).
    sort_by: one of SORT_FIELDS; "status" sorts in pipeline order (Saved first).
    The default is newest-added first.
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
    text = value.isoformat() if hasattr(value, "isoformat") else str(value)
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
