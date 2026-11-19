"""State shared by every page: the database engine and the active profile."""

from dataclasses import dataclass, field

from nicegui import app

from apply_spy.db import init_db, make_engine, session_scope
from apply_spy.profiles import get_active_profile, list_profiles

engine = make_engine()
init_db(engine)

ACTIVE_PROFILE_KEY = "active_profile_id"


@dataclass
class ActiveProfile:
    """Who is using the app right now (the person whose data every page shows)."""

    id: int | None = None
    name: str | None = None
    profiles: list[tuple[int, str]] = field(default_factory=list)  # everyone, for the menu


def load_active_profile() -> ActiveProfile:
    """The profile this browser picked, or the first one if none was picked (or the
    picked one was deleted). Empty when there are no profiles at all."""
    with session_scope(engine) as session:
        everyone = [(p.id, p.name) for p in list_profiles(session)]
        active = get_active_profile(session, app.storage.user.get(ACTIVE_PROFILE_KEY))
        if active is None:
            return ActiveProfile(profiles=everyone)
        return ActiveProfile(id=active.id, name=active.name, profiles=everyone)


def set_active_profile(profile_id: int) -> None:
    app.storage.user[ACTIVE_PROFILE_KEY] = profile_id
