import csv
import io
import time
from datetime import date, timedelta

import pytest
from sqlalchemy import select, text

from apply_spy.cli import main
from apply_spy.db import Application, StatusEvent, init_db, make_engine, session_scope
from apply_spy.profiles import ProfileNotFound, create_profile, delete_profile
from apply_spy.tracker import (
    STATUSES,
    ApplicationNotFound,
    InvalidApplication,
    InvalidStatus,
    change_status,
    create_application,
    export_applications_csv,
    list_applications,
    list_status_events,
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


# --- Changing status (story 2.2) ------------------------------------------


def _changes(events):
    """(old, new) pairs, skipping the first 'added' event that has no old status."""
    return [(e.old_status, e.new_status) for e in events if e.old_status is not None]


def test_status_moves_through_the_pipeline_with_ordered_history(engine, two_profiles):
    one, _ = two_profiles
    with session_scope(engine) as session:
        app_id = create_application(session, one, "Acme", "Engineer").id

    with session_scope(engine) as session:
        change_status(session, one, app_id, "Online assessment")
    with session_scope(engine) as session:
        change_status(session, one, app_id, "Interview")

    with session_scope(engine) as session:
        [app] = list_applications(session, one)
        events = list_status_events(session, one, app_id)
    assert app.status == "Interview"
    assert _changes(events) == [
        ("Applied", "Online assessment"),
        ("Online assessment", "Interview"),
    ]
    assert all(e.changed_at is not None for e in events)
    # The first event records the application being added.
    assert (events[0].old_status, events[0].new_status) == (None, "Applied")


def test_unknown_status_is_rejected_and_changes_nothing(engine, two_profiles):
    one, _ = two_profiles
    with session_scope(engine) as session:
        app_id = create_application(session, one, "Acme", "Engineer").id

    with pytest.raises(InvalidStatus):
        with session_scope(engine) as session:
            change_status(session, one, app_id, "Super offer")

    with session_scope(engine) as session:
        assert list_applications(session, one)[0].status == "Applied"
        assert len(list_status_events(session, one, app_id)) == 1


def test_choosing_the_same_status_adds_no_event(engine, two_profiles):
    one, _ = two_profiles
    with session_scope(engine) as session:
        app_id = create_application(session, one, "Acme", "Engineer").id
    with session_scope(engine) as session:
        change_status(session, one, app_id, "Applied")
    with session_scope(engine) as session:
        assert len(list_status_events(session, one, app_id)) == 1


def test_if_saving_the_history_fails_the_status_does_not_change(engine, two_profiles, monkeypatch):
    one, _ = two_profiles
    with session_scope(engine) as session:
        app_id = create_application(session, one, "Acme", "Engineer").id

    def broken_event(**_kwargs):
        raise RuntimeError("disk full")

    monkeypatch.setattr("apply_spy.tracker.service.StatusEvent", broken_event)
    with pytest.raises(RuntimeError):
        with session_scope(engine) as session:
            change_status(session, one, app_id, "Interview")
    monkeypatch.undo()

    with session_scope(engine) as session:
        assert list_applications(session, one)[0].status == "Applied"
        assert len(list_status_events(session, one, app_id)) == 1


def test_cannot_change_or_read_another_profiles_application(engine, two_profiles):
    one, two = two_profiles
    with session_scope(engine) as session:
        app_id = create_application(session, one, "One Co", "Engineer").id

    with pytest.raises(ApplicationNotFound):
        with session_scope(engine) as session:
            change_status(session, two, app_id, "Offer")
    with pytest.raises(ApplicationNotFound):
        with session_scope(engine) as session:
            list_status_events(session, two, app_id)
    with session_scope(engine) as session:
        assert list_applications(session, one)[0].status == "Applied"


# --- Editing every field ---------------------------------------------------


def test_every_detail_can_be_edited(engine, two_profiles):
    one, _ = two_profiles
    with session_scope(engine) as session:
        app_id = create_application(session, one, "Acme", "Engineer").id

    with session_scope(engine) as session:
        update_application_details(
            session,
            one,
            app_id,
            company="Acme Corp",
            title="Senior Engineer",
            url="https://acme.example/jobs/2",
            location="Mountain View, CA",
            salary_range="170k-200k",
            resume_version="Backend v3",
            notes="Second round booked.",
            date_applied=date(2026, 10, 30),
        )
    with session_scope(engine) as session:
        [app] = list_applications(session, one)
    assert (app.company, app.title) == ("Acme Corp", "Senior Engineer")
    assert app.url == "https://acme.example/jobs/2"
    assert (app.location, app.salary_range) == ("Mountain View, CA", "170k-200k")
    assert (app.resume_version, app.notes) == ("Backend v3", "Second round booked.")
    assert app.date_applied == date(2026, 10, 30)


def test_editing_details_does_not_touch_status_or_history(engine, two_profiles):
    one, _ = two_profiles
    with session_scope(engine) as session:
        app_id = create_application(session, one, "Acme", "Engineer").id
    with session_scope(engine) as session:
        update_application_details(session, one, app_id, company="Acme Corp")
    with session_scope(engine) as session:
        assert list_applications(session, one)[0].status == "Applied"
        assert len(list_status_events(session, one, app_id)) == 1


@pytest.mark.parametrize("field", ["company", "title"])
def test_cannot_blank_out_company_or_title(engine, two_profiles, field):
    one, _ = two_profiles
    with session_scope(engine) as session:
        app_id = create_application(session, one, "Acme", "Engineer").id

    with pytest.raises(InvalidApplication) as error:
        with session_scope(engine) as session:
            update_application_details(session, one, app_id, **{field: "  "})
    assert error.value.field == field


def test_one_bad_value_changes_nothing(engine, two_profiles):
    one, _ = two_profiles
    with session_scope(engine) as session:
        app_id = create_application(session, one, "Acme", "Engineer").id

    with pytest.raises(InvalidApplication):
        with session_scope(engine) as session:
            update_application_details(session, one, app_id, company="New Name", url="not-a-link")

    with session_scope(engine) as session:
        assert list_applications(session, one)[0].company == "Acme"


def test_clearing_an_optional_field_works(engine, two_profiles):
    one, _ = two_profiles
    with session_scope(engine) as session:
        app_id = create_application(session, one, "Acme", "Engineer", location="Seattle").id
    with session_scope(engine) as session:
        update_application_details(session, one, app_id, location="")
    with session_scope(engine) as session:
        assert list_applications(session, one)[0].location is None


# --- Filter, search and sort (story 2.3) ----------------------------------


def _fifty_applications(engine, profile_id):
    """50 applications over several companies and statuses (made-up data)."""
    companies = ["Acme", "Globex", "Initech", "Umbrella", "Hooli"]
    statuses = ["Applied", "Interview", "Offer", "Rejected", "Online assessment"]
    with session_scope(engine) as session:
        for i in range(50):
            app_id = create_application(
                session,
                profile_id,
                companies[i % 5],
                f"Engineer {i}",
                location="Seattle, WA" if i % 2 == 0 else "Mountain View, CA",
                date_applied=date(2026, 10, 1) + timedelta(days=i),
            ).id
            if statuses[(i // 5) % 5] != "Applied":
                change_status(session, profile_id, app_id, statuses[(i // 5) % 5])


def test_filter_by_status_and_search_together(engine, two_profiles):
    one, _ = two_profiles
    _fifty_applications(engine, one)

    with session_scope(engine) as session:
        everything = list_applications(session, one)
        interviews = list_applications(session, one, status="Interview")
        both = list_applications(session, one, status="Interview", search="acme")
    assert len(everything) == 50
    assert interviews and all(a.status == "Interview" for a in interviews)
    assert both
    assert all(a.status == "Interview" and a.company == "Acme" for a in both)
    # Exactly the applications that match both conditions, no more and no fewer.
    expected = [a.id for a in everything if a.status == "Interview" and a.company == "Acme"]
    assert sorted(a.id for a in both) == sorted(expected)


def test_search_is_case_insensitive_and_covers_company_title_and_location(engine, two_profiles):
    one, _ = two_profiles
    with session_scope(engine) as session:
        create_application(session, one, "Acme", "Backend Engineer", location="Seattle")
        create_application(session, one, "Globex", "Frontend Developer", location="Mountain View")

    def found(term):
        with session_scope(engine) as session:
            return sorted(a.company for a in list_applications(session, one, search=term))

    assert found("ACME") == ["Acme"]
    assert found("frontend") == ["Globex"]
    assert found("mountain") == ["Globex"]
    assert found("  acme  ") == ["Acme"]  # extra spaces ignored
    assert found("") == ["Acme", "Globex"]  # empty search shows everything
    assert found("nothing like this") == []


def test_search_treats_percent_and_underscore_as_plain_characters(engine, two_profiles):
    one, _ = two_profiles
    with session_scope(engine) as session:
        create_application(session, one, "Acme", "Engineer")
        create_application(session, one, "100% Remote Co", "Engineer")

    with session_scope(engine) as session:
        assert [a.company for a in list_applications(session, one, search="%")] == [
            "100% Remote Co"
        ]
        assert list_applications(session, one, search="_") == []


def test_filters_never_leak_the_other_profiles_applications(engine, two_profiles):
    one, two = two_profiles
    with session_scope(engine) as session:
        create_application(session, one, "Acme", "Engineer")
        create_application(session, two, "Acme", "Engineer")

    with session_scope(engine) as session:
        found = list_applications(session, one, status="Applied", search="acme")
    assert [a.profile_id for a in found] == [one]


def test_sort_by_date_applied_company_and_status(engine, two_profiles):
    one, _ = two_profiles
    with session_scope(engine) as session:
        a = create_application(session, one, "beta", "Eng", date_applied=date(2026, 11, 3)).id
        create_application(session, one, "Alpha", "Eng", date_applied=date(2026, 11, 1))
        c = create_application(session, one, "Gamma", "Eng", date_applied=date(2026, 11, 2)).id
        change_status(session, one, a, "Offer")
        change_status(session, one, c, "Saved")

    def companies(sort_by, descending):
        with session_scope(engine) as session:
            return [
                x.company
                for x in list_applications(session, one, sort_by=sort_by, descending=descending)
            ]

    assert companies("date_applied", False) == ["Alpha", "Gamma", "beta"]
    assert companies("date_applied", True) == ["beta", "Gamma", "Alpha"]
    assert companies("company", False) == ["Alpha", "beta", "Gamma"]  # ignores upper/lower case
    assert companies("company", True) == ["Gamma", "beta", "Alpha"]
    # Pipeline order: Saved, then Applied, then Offer (not alphabetical).
    assert companies("status", False) == ["Gamma", "Alpha", "beta"]
    assert companies("status", True) == ["beta", "Alpha", "Gamma"]


def test_default_order_is_newest_added_first(engine, two_profiles):
    one, _ = two_profiles
    with session_scope(engine) as session:
        create_application(session, one, "First", "Eng")
        create_application(session, one, "Second", "Eng")
    with session_scope(engine) as session:
        assert [a.company for a in list_applications(session, one)] == ["Second", "First"]


def test_bad_filter_or_sort_values_are_rejected(engine, two_profiles):
    one, _ = two_profiles
    with session_scope(engine) as session:
        with pytest.raises(InvalidStatus):
            list_applications(session, one, status="Super offer")
        with pytest.raises(ValueError):
            list_applications(session, one, sort_by="notes; DROP TABLE applications")


def test_1000_applications_load_and_filter_quickly(engine, two_profiles):
    one, two = two_profiles
    with session_scope(engine) as session:
        for i in range(1000):
            owner = one if i % 4 else two  # 750 for one, 250 for two
            app = create_application(session, owner, f"Company {i % 40}", f"Engineer {i}")
            change_status(session, owner, app.id, STATUSES[i % len(STATUSES)])

    started = time.perf_counter()
    with session_scope(engine) as session:
        everything = list_applications(session, one)
        filtered = list_applications(session, one, status="Interview", search="company 7")
        by_company = list_applications(session, one, sort_by="company", descending=False)
    elapsed = time.perf_counter() - started

    assert len(everything) == 750 and len(by_company) == 750
    assert all(a.status == "Interview" for a in filtered)
    assert elapsed < 1.0  # the story allows 1 second for board and table to load


# --- CSV export (story 3.1) ------------------------------------------------


def _read_csv(text):
    """Read the exported text back with Python's standard csv reader."""
    return list(csv.reader(io.StringIO(text, newline="")))


def test_export_has_a_header_naming_every_field_and_one_row_per_application(engine, two_profiles):
    one, _ = two_profiles
    with session_scope(engine) as session:
        create_application(session, one, "Acme", "Engineer")
        create_application(session, one, "Globex", "Developer")

    with session_scope(engine) as session:
        rows = _read_csv(export_applications_csv(session, one))
    assert rows[0] == [
        "Company",
        "Title",
        "Link",
        "Location",
        "Salary range",
        "Resume version",
        "Status",
        "Date applied",
        "Notes",
        "Added",
    ]
    assert len(rows) == 3  # header + 2 applications
    assert [r[0] for r in rows[1:]] == ["Acme", "Globex"]  # oldest first
    assert all(len(r) == len(rows[0]) for r in rows)  # every row has every column


def test_round_trip_keeps_commas_quotes_line_breaks_and_unicode(engine, two_profiles):
    one, _ = two_profiles
    notes = 'Said "call back, soon"\nSecond line\r\nThird, with comma'
    with session_scope(engine) as session:
        create_application(
            session,
            one,
            "Café, Inc.",
            'Engineer "II"',
            "https://example.com/jobs?a=1&b=2",
            location="Zürich, 日本",
            salary_range="150k-180k",
            resume_version="Backend v2",
            notes=notes,
            date_applied=date(2026, 11, 1),
        )

    with session_scope(engine) as session:
        text = export_applications_csv(session, one)
    # Saved as UTF-8 and read back as UTF-8, like a spreadsheet or another program would.
    rows = _read_csv(text.encode("utf-8").decode("utf-8"))
    record = dict(zip(rows[0], rows[1], strict=True))
    assert record["Company"] == "Café, Inc."
    assert record["Title"] == 'Engineer "II"'
    assert record["Link"] == "https://example.com/jobs?a=1&b=2"
    assert record["Location"] == "Zürich, 日本"
    assert record["Notes"] == notes
    assert record["Status"] == "Applied"
    assert record["Date applied"] == "2026-11-01"
    assert len(rows) == 2  # the line breaks in notes did not create extra rows


def test_missing_optional_fields_are_empty_cells(engine, two_profiles):
    one, _ = two_profiles
    with session_scope(engine) as session:
        create_application(session, one, "Acme", "Engineer")
    with session_scope(engine) as session:
        rows = _read_csv(export_applications_csv(session, one))
    record = dict(zip(rows[0], rows[1], strict=True))
    assert (record["Link"], record["Location"], record["Notes"]) == ("", "", "")


def test_export_only_contains_that_profiles_applications(engine, two_profiles):
    one, two = two_profiles
    with session_scope(engine) as session:
        create_application(session, one, "One Co", "Engineer")
        create_application(session, two, "Two Co", "Engineer")
    with session_scope(engine) as session:
        companies = [r[0] for r in _read_csv(export_applications_csv(session, one))[1:]]
    assert companies == ["One Co"]


def test_export_for_a_profile_with_no_applications_is_just_the_header(engine, two_profiles):
    one, _ = two_profiles
    with session_scope(engine) as session:
        assert len(_read_csv(export_applications_csv(session, one))) == 1


def test_export_unknown_profile_is_an_error(engine):
    with pytest.raises(ProfileNotFound):
        with session_scope(engine) as session:
            export_applications_csv(session, 999)


@pytest.mark.parametrize("text", ["=1+1", "+1", "-2", "@SUM(A1)"])
def test_cells_that_look_like_formulas_are_exported_as_text(engine, two_profiles, text):
    one, _ = two_profiles
    with session_scope(engine) as session:
        create_application(session, one, "Acme", "Engineer", notes=text)
    with session_scope(engine) as session:
        rows = _read_csv(export_applications_csv(session, one))
    assert dict(zip(rows[0], rows[1], strict=True))["Notes"] == "'" + text


def test_exporting_1000_applications_is_fast(engine, two_profiles):
    one, _ = two_profiles
    with session_scope(engine) as session:
        for i in range(1000):
            create_application(
                session, one, f"Company {i}", f"Engineer {i}", notes="Line one\nLine, two"
            )

    started = time.perf_counter()
    with session_scope(engine) as session:
        text = export_applications_csv(session, one)
    elapsed = time.perf_counter() - started

    assert len(_read_csv(text)) == 1001
    assert elapsed < 3.0  # the story allows 3 seconds


def test_cli_export_writes_a_utf8_file_and_prints_to_the_screen(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("APPLY_SPY_DATA_DIR", str(tmp_path))
    engine = make_engine()
    init_db(engine)
    with session_scope(engine) as session:
        profile_id = create_profile(session, "Sample One").id
        create_application(session, profile_id, "Café", "Engineer", notes="a, b\nc")
    engine.dispose()

    target = tmp_path / "out.csv"
    assert main(["export", str(profile_id), "--output", str(target)]) == 0
    with open(target, encoding="utf-8", newline="") as file:
        rows = list(csv.reader(file))
    assert rows[1][0] == "Café" and rows[1][8] == "a, b\nc"

    capsys.readouterr()
    assert main(["export", str(profile_id)]) == 0
    assert _read_csv(capsys.readouterr().out)[1][0] == "Café"


def test_cli_export_unknown_profile_is_an_error(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("APPLY_SPY_DATA_DIR", str(tmp_path))
    assert main(["export", "999"]) == 1
    assert "No profile with id 999" in capsys.readouterr().out
