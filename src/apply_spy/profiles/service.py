"""Profile logic. The web UI and the CLI both call these functions."""

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from apply_spy.db.models import Profile

MAX_NAME_LENGTH = 100


class InvalidProfileName(ValueError):
    """The name is empty or too long."""


class DuplicateProfileName(ValueError):
    """A profile with this name already exists (ignoring case)."""


def create_profile(session: Session, name: str) -> Profile:
    name = " ".join(name.split())  # trim and collapse extra spaces
    if not name:
        raise InvalidProfileName("Please enter a name.")
    if len(name) > MAX_NAME_LENGTH:
        raise InvalidProfileName(f"Names can be at most {MAX_NAME_LENGTH} characters.")

    exists = session.scalar(select(Profile.id).where(func.lower(Profile.name) == name.lower()))
    if exists is not None:
        raise DuplicateProfileName(f'A profile named "{name}" already exists.')

    profile = Profile(name=name)
    session.add(profile)
    try:
        session.flush()  # the unique index is the final safety net
    except IntegrityError as error:
        session.rollback()
        raise DuplicateProfileName(f'A profile named "{name}" already exists.') from error
    return profile


def list_profiles(session: Session) -> list[Profile]:
    return list(session.scalars(select(Profile).order_by(Profile.created_at, Profile.id)))
