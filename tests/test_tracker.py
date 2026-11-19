from datetime import date

import pytest
from sqlalchemy import select, text

from apply_spy.db import Application, StatusEvent, session_scope
from apply_spy.profiles import ProfileNotFound, create_profile, delete_profile
from apply_spy.tracker import (
    ApplicationNotFound,
    InvalidApplication,
    create_application,
    list_applications,
    update_application_details,
)


@pytest.fixture
def two_profiles(engine):
    with session_scope(engine) as session:
        one = create_profile(session, "Sample One").id
        two = create_profile(session, "Sample Two").id
    return one, two


def test_add_with_company_title_and_link_is_applied_today(engine, two_profiles):
    one, _ = two_profiles
    with session_scope(engine) as session:
        create_application(session, one, "Acme", "Software Engineer", "https://acme.example/jobs/1")

    with session_scope(engine) as session:
        [app] = list_applications(session, one)
    assert (app.company, app.title) == ("Acme", "Software Engineer")
    assert app.url == "https://acme.example/jobs/1"
    assert app.status == "Applied"
    assert app.date_applied == date.today()
    assert app.profile_id == one


def test_new_application_appears_at_the_top(engine, two_profiles):
    one, _ = two_profiles
    with session_scope(engine) as session:
        create_application(session, one, "First Co", "Engineer")
        create_application(session, one, "Second Co", "Engineer")
        create_application(session, one, "Third Co", "Engineer")

    with session_scope(engine) as session:
        companies = [a.company for a in list_applications(session, one)]
    assert companies == ["Third Co", "Second Co", "First Co"]


def test_first_status_event_is_recorded(engine, two_profiles):
    one, _ = two_profiles
    with session_scope(engine) as session:
        app_id = create_application(session, one, "Acme", "Engineer").id

    with session_scope(engine) as session:
        [event] = session.scalars(select(StatusEvent).where(StatusEvent.application_id == app_id))
    assert (event.old_status, event.new_status) == (None, "Applied")
    assert event.changed_at is not None


@pytest.mark.parametrize(
    ("company", "title", "field"),
    [
        ("", "Engineer", "company"),
        ("Acme", "", "title"),
        ("   ", "Engineer", "company"),
        ("Acme", "\n", "title"),
    ],
)
def test_missing_company_or_title_is_rejected_and_names_the_field(
    engine, two_profiles, company, title, field
):
    one, _ = two_profiles
    with pytest.raises(InvalidApplication) as error:
        with session_scope(engine) as session:
            create_application(session, one, company, title)

    assert error.value.field == field
    assert field.capitalize() in str(error.value)
    with session_scope(engine) as session:
        assert list_applications(session, one) == []
        assert session.scalars(select(StatusEvent)).all() == []


@pytest.mark.parametrize("url", ["acme.example/jobs", "javascript:alert(1)", "ftp://x.example"])
def test_link_must_be_http_or_https(engine, two_profiles, url):
    one, _ = two_profiles
    with pytest.raises(InvalidApplication) as error:
        with session_scope(engine) as session:
            create_application(session, one, "Acme", "Engineer", url)
    assert error.value.field == "link"


def test_optional_fields_can_be_added_now(engine, two_profiles):
    one, _ = two_profiles
    with session_scope(engine) as session:
        create_application(
            session,
            one,
            "Acme",
            "Engineer",
            location="Seattle, WA",
            salary_range="150k-180k",
            resume_version="Backend v2",
            notes="Referred by a friend.",
            date_applied=date(2026, 11, 1),
        )
    with session_scope(engine) as session:
        [app] = list_applications(session, one)
    assert (app.location, app.salary_range, app.resume_version) == (
        "Seattle, WA",
        "150k-180k",
        "Backend v2",
    )
    assert app.notes == "Referred by a friend."
    assert app.date_applied == date(2026, 11, 1)


def test_optional_fields_can_be_added_later(engine, two_profiles):
    one, _ = two_profiles
    with session_scope(engine) as session:
        app_id = create_application(session, one, "Acme", "Engineer").id

    with session_scope(engine) as session:
        update_application_details(session, one, app_id, location="Seattle, WA", notes="Call HR.")
    with session_scope(engine) as session:
        [app] = list_applications(session, one)
    assert (app.location, app.notes) == ("Seattle, WA", "Call HR.")
    assert app.salary_range is None  # untouched


def test_cannot_edit_unknown_fields(engine, two_profiles):
    one, _ = two_profiles
    with session_scope(engine) as session:
        app_id = create_application(session, one, "Acme", "Engineer").id
    with pytest.raises(ValueError):
        with session_scope(engine) as session:
            update_application_details(session, one, app_id, status="Offer")


def test_add_for_a_missing_profile_is_an_error(engine):
    with pytest.raises(ProfileNotFound):
        with session_scope(engine) as session:
            create_application(session, 999, "Acme", "Engineer")


# --- Profile isolation ---------------------------------------------------


def test_each_profile_only_sees_its_own_applications(engine, two_profiles):
    one, two = two_profiles
    with session_scope(engine) as session:
        create_application(session, one, "One Co", "Engineer")
        create_application(session, two, "Two Co", "Engineer")

    with session_scope(engine) as session:
        assert [a.company for a in list_applications(session, one)] == ["One Co"]
        assert [a.company for a in list_applications(session, two)] == ["Two Co"]


def test_100_switches_never_show_the_other_profiles_applications(engine, two_profiles):
    one, two = two_profiles
    with session_scope(engine) as session:
        for i in range(3):
            create_application(session, one, f"One Co {i}", "Engineer")
            create_application(session, two, f"Two Co {i}", "Engineer")

    for i in range(100):
        active = one if i % 2 == 0 else two
        with session_scope(engine) as session:
            shown = list_applications(session, active)
        assert len(shown) == 3
        assert {a.profile_id for a in shown} == {active}


def test_cannot_edit_another_profiles_application(engine, two_profiles):
    one, two = two_profiles
    with session_scope(engine) as session:
        app_id = create_application(session, one, "One Co", "Engineer").id

    with pytest.raises(ApplicationNotFound):
        with session_scope(engine) as session:
            update_application_details(session, two, app_id, notes="sneaky")
    with session_scope(engine) as session:
        assert list_applications(session, one)[0].notes is None


def test_deleting_a_profile_removes_its_applications_and_history_only(engine, two_profiles):
    one, two = two_profiles
    with session_scope(engine) as session:
        create_application(session, one, "One Co", "Engineer")
        create_application(session, one, "One Co 2", "Engineer")
        create_application(session, two, "Two Co", "Engineer")

    with session_scope(engine) as session:
        delete_profile(session, one, confirmed=True)

    with session_scope(engine) as session:
        assert [a.company for a in session.scalars(select(Application))] == ["Two Co"]
        assert len(session.scalars(select(StatusEvent)).all()) == 1
    with engine.connect() as conn:
        assert conn.execute(text("PRAGMA foreign_key_check")).all() == []  # no orphans
