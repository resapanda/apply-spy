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


class ProfileNotFound(LookupError):
    """No profile with this id."""


class ConfirmationRequired(ValueError):
    """Deletion was not explicitly confirmed, so nothing is deleted."""


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


def get_active_profile(session: Session, profile_id: int | None) -> Profile | None:
    """Return the profile the user picked, or fall back to the first one.

    The fallback covers a first visit (nothing picked yet) and a stored id
    that no longer exists (for example a deleted profile). Returns None only
    when there are no profiles at all.
    """
    if profile_id is not None:
        profile = session.get(Profile, profile_id)
        if profile is not None:
            return profile
    profiles = list_profiles(session)
    return profiles[0] if profiles else None


def delete_profile(session: Session, profile_id: int, confirmed: bool) -> None:
    """Delete a profile and everything it owns.

    Safety: nothing is deleted unless confirmed is True (the UI's checkbox or
    the CLI's "yes"). Owned rows in other tables are removed by the database
    itself (ON DELETE CASCADE on profile_id), in the same transaction, so no
    orphans are left behind.
    """
    profile = session.get(Profile, profile_id)
    if profile is None:
        raise ProfileNotFound(f"No profile with id {profile_id}.")
    if confirmed is not True:
        raise ConfirmationRequired("Deletion was not confirmed, so nothing was deleted.")
    session.delete(profile)
    session.flush()
