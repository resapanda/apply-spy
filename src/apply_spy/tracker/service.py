"""Application tracker logic. The web UI and the CLI both call these functions.

Profile isolation: every function takes a profile_id and only ever touches
that profile's rows.
"""

from datetime import date

from sqlalchemy import select
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

MAX_SHORT = 200
MAX_URL = 2000
MAX_SALARY = 100

# Fields that can be filled in later with update_application_details.
EDITABLE_FIELDS = ("url", "location", "salary_range", "resume_version", "notes")


class InvalidApplication(ValueError):
    """A field is missing or not acceptable. `field` names it, for the message."""

    def __init__(self, field: str, message: str):
        super().__init__(message)
        self.field = field


class ApplicationNotFound(LookupError):
    """No application with this id in this profile."""


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
    session: Session, profile_id: int, application_id: int, **fields: str | None
) -> Application:
    """Fill in or change optional details later (link, location, salary range,
    resume version, notes). Fields not passed are left as they are."""
    unknown = set(fields) - set(EDITABLE_FIELDS)
    if unknown:
        raise ValueError(f"Cannot edit: {', '.join(sorted(unknown))}")
    application = session.scalar(
        select(Application).where(
            Application.id == application_id, Application.profile_id == profile_id
        )
    )
    if application is None:
        raise ApplicationNotFound(f"No application with id {application_id} in this profile.")

    if "url" in fields:
        application.url = _url(fields["url"])
    if "location" in fields:
        application.location = _optional("Location", "location", fields["location"], MAX_SHORT)
    if "salary_range" in fields:
        application.salary_range = _optional(
            "Salary range", "salary range", fields["salary_range"], MAX_SALARY
        )
    if "resume_version" in fields:
        application.resume_version = _optional(
            "Resume version", "resume version", fields["resume_version"], MAX_SHORT
        )
    if "notes" in fields:
        application.notes = _notes(fields["notes"])
    session.flush()
    return application


def list_applications(session: Session, profile_id: int) -> list[Application]:
    """This profile's applications, newest first."""
    return list(
        session.scalars(
            select(Application)
            .where(Application.profile_id == profile_id)
            .order_by(Application.created_at.desc(), Application.id.desc())
        )
    )
