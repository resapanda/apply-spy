import csv
import io
import time
from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import select, text

from apply_spy.cli import main
from apply_spy.db import Application, StatusEvent, init_db, make_engine, session_scope
from apply_spy.profiles import ProfileNotFound, create_profile, delete_profile
from apply_spy.tracker import (
    STATUSES,
    ApplicationNotFound,
    CompletedFollowUpNotFound,
    InvalidApplication,
    InvalidStatus,
    calendar_month,
    change_status,
    complete_follow_up,
    count_completed_follow_ups,
    create_application,
    export_applications_csv,
    later_follow_ups,
    list_applications,
    list_completed_follow_ups,
    list_status_events,
    needs_attention,
    set_follow_up,
    undo_complete_follow_up,
    upcoming_follow_ups,
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
        "Follow-up date",
        "Follow-up label",
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
    first = dict(zip(rows[0], rows[1], strict=True))
    assert first["Company"] == "Café" and first["Notes"] == "a, b\nc"

    capsys.readouterr()
    assert main(["export", str(profile_id)]) == 0
    assert _read_csv(capsys.readouterr().out)[1][0] == "Café"


def test_cli_export_unknown_profile_is_an_error(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("APPLY_SPY_DATA_DIR", str(tmp_path))
    assert main(["export", "999"]) == 1
    assert "No profile with id 999" in capsys.readouterr().out


# --- Follow-up dates (story 4.1) --------------------------------------------


def test_set_follow_up_date_and_label(engine, two_profiles):
    one, _ = two_profiles
    with session_scope(engine) as session:
        app_id = create_application(session, one, "Acme", "Engineer").id
    with session_scope(engine) as session:
        set_follow_up(session, one, app_id, date(2026, 11, 25), "OA due")

    with session_scope(engine) as session:
        [app] = list_applications(session, one)
    assert app.follow_up_date == date(2026, 11, 25)
    assert app.follow_up_label == "OA due"


def test_the_label_is_optional(engine, two_profiles):
    one, _ = two_profiles
    with session_scope(engine) as session:
        app_id = create_application(session, one, "Acme", "Engineer").id
        set_follow_up(session, one, app_id, date(2026, 11, 25))
    with session_scope(engine) as session:
        [app] = list_applications(session, one)
    assert (app.follow_up_date, app.follow_up_label) == (date(2026, 11, 25), None)


def test_new_applications_have_no_follow_up(engine, two_profiles):
    one, _ = two_profiles
    with session_scope(engine) as session:
        app = create_application(session, one, "Acme", "Engineer")
    assert (app.follow_up_date, app.follow_up_label) == (None, None)


def test_follow_up_can_be_changed_and_cleared(engine, two_profiles):
    one, _ = two_profiles
    with session_scope(engine) as session:
        app_id = create_application(session, one, "Acme", "Engineer").id
        set_follow_up(session, one, app_id, "2026-11-25", "Check in")
    with session_scope(engine) as session:
        set_follow_up(session, one, app_id, "2026-12-01", "Call recruiter")
    with session_scope(engine) as session:
        [app] = list_applications(session, one)
        assert (app.follow_up_date, app.follow_up_label) == (date(2026, 12, 1), "Call recruiter")
        set_follow_up(session, one, app_id, None)  # clears both
    with session_scope(engine) as session:
        [app] = list_applications(session, one)
    assert (app.follow_up_date, app.follow_up_label) == (None, None)


@pytest.mark.parametrize(
    "bad",
    [
        "11/25/2026",
        "25-11-2026",
        "2026-1-5",
        "20261125",
        "2026-W47-2",
        "tomorrow",
        "2026-13-01",
        "2026-02-30",
    ],
)
def test_a_date_in_an_invalid_format_is_rejected_with_a_message(engine, two_profiles, bad):
    one, _ = two_profiles
    with session_scope(engine) as session:
        app_id = create_application(session, one, "Acme", "Engineer").id
        set_follow_up(session, one, app_id, date(2026, 11, 25), "Keep me")

    with pytest.raises(InvalidApplication) as error:
        with session_scope(engine) as session:
            set_follow_up(session, one, app_id, bad, "New label")

    assert error.value.field == "follow-up date"
    assert "2026-11-30" in str(error.value) or "real date" in str(error.value)
    with session_scope(engine) as session:  # nothing was changed
        [app] = list_applications(session, one)
    assert (app.follow_up_date, app.follow_up_label) == (date(2026, 11, 25), "Keep me")


def test_a_label_without_a_date_is_rejected(engine, two_profiles):
    one, _ = two_profiles
    with session_scope(engine) as session:
        app_id = create_application(session, one, "Acme", "Engineer").id
    with pytest.raises(InvalidApplication) as error:
        with session_scope(engine) as session:
            set_follow_up(session, one, app_id, None, "OA due")
    assert error.value.field == "follow-up date"


def test_a_too_long_label_is_rejected(engine, two_profiles):
    one, _ = two_profiles
    with session_scope(engine) as session:
        app_id = create_application(session, one, "Acme", "Engineer").id
    with pytest.raises(InvalidApplication) as error:
        with session_scope(engine) as session:
            set_follow_up(session, one, app_id, date(2026, 11, 25), "x" * 101)
    assert error.value.field == "follow-up label"


def test_cannot_set_a_follow_up_on_another_profiles_application(engine, two_profiles):
    one, two = two_profiles
    with session_scope(engine) as session:
        app_id = create_application(session, one, "One Co", "Engineer").id
    with pytest.raises(ApplicationNotFound):
        with session_scope(engine) as session:
            set_follow_up(session, two, app_id, date(2026, 11, 25))
    with session_scope(engine) as session:
        assert list_applications(session, one)[0].follow_up_date is None


def test_setting_a_follow_up_does_not_touch_status_or_history(engine, two_profiles):
    one, _ = two_profiles
    with session_scope(engine) as session:
        app_id = create_application(session, one, "Acme", "Engineer").id
        set_follow_up(session, one, app_id, date(2026, 11, 25), "OA due")
    with session_scope(engine) as session:
        assert list_applications(session, one)[0].status == "Applied"
        assert len(list_status_events(session, one, app_id)) == 1


def test_follow_up_appears_in_the_csv_export(engine, two_profiles):
    one, _ = two_profiles
    with session_scope(engine) as session:
        app_id = create_application(session, one, "Acme", "Engineer").id
        set_follow_up(session, one, app_id, date(2026, 11, 25), "OA due")
    with session_scope(engine) as session:
        rows = _read_csv(export_applications_csv(session, one))
    record = dict(zip(rows[0], rows[1], strict=True))
    assert (record["Follow-up date"], record["Follow-up label"]) == ("2026-11-25", "OA due")


# --- An older database gets the new columns (and keeps its data) -------------


def test_init_db_adds_the_follow_up_columns_to_an_older_database(tmp_path):
    import sqlite3

    from sqlalchemy import inspect

    db = tmp_path / "old.db"
    old = sqlite3.connect(db)
    old.executescript(
        """
        CREATE TABLE profiles (id INTEGER PRIMARY KEY, name VARCHAR(100) NOT NULL,
            created_at DATETIME);
        CREATE TABLE applications (
            id INTEGER PRIMARY KEY,
            profile_id INTEGER NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
            company VARCHAR(200) NOT NULL, title VARCHAR(200) NOT NULL,
            url VARCHAR(2000), location VARCHAR(200), salary_range VARCHAR(100),
            resume_version VARCHAR(200), notes TEXT, status VARCHAR(50) NOT NULL,
            date_applied DATE NOT NULL, created_at DATETIME);
        INSERT INTO profiles (id, name) VALUES (1, 'Sample One');
        INSERT INTO applications (profile_id, company, title, status, date_applied)
            VALUES (1, 'Acme', 'Engineer', 'Applied', '2026-11-01');
        """
    )
    old.commit()
    old.close()

    engine = make_engine(f"sqlite:///{db}")
    try:
        init_db(engine)
        init_db(engine)  # running it again must be harmless
        columns = {c["name"] for c in inspect(engine).get_columns("applications")}
        assert {"follow_up_date", "follow_up_label"} <= columns
        with session_scope(engine) as session:
            [app] = list_applications(session, 1)
            assert (app.company, app.status, app.follow_up_date) == ("Acme", "Applied", None)
            set_follow_up(session, 1, app.id, date(2026, 11, 25), "OA due")
        with session_scope(engine) as session:
            assert list_applications(session, 1)[0].follow_up_label == "OA due"
    finally:
        engine.dispose()


# --- Needs attention (story 4.2) --------------------------------------------

TODAY = date(2026, 11, 19)


def _add(session, profile_id, company, days_from_today, status=None, today=TODAY):
    app = create_application(session, profile_id, company, "Engineer")
    set_follow_up(session, profile_id, app.id, today + timedelta(days=days_from_today), "Check in")
    if status:
        change_status(session, profile_id, app.id, status)
    return app


def _names(apps):
    return [a.company for a in apps]


def test_follow_ups_are_grouped_into_overdue_today_and_this_week(engine, two_profiles):
    one, _ = two_profiles
    with session_scope(engine) as session:
        _add(session, one, "Yesterday Co", -1)
        _add(session, one, "Today Co", 0)
        _add(session, one, "Soon Co", 3)
        _add(session, one, "Far Co", 30)  # a month away: not listed
        create_application(session, one, "No Date Co", "Engineer")  # no follow-up: not listed

    with session_scope(engine) as session:
        result = needs_attention(session, one, today=TODAY)
    assert _names(result.overdue) == ["Yesterday Co"]
    assert _names(result.due_today) == ["Today Co"]
    assert _names(result.this_week) == ["Soon Co"]
    assert result.total == 3
    assert result.today == TODAY


def test_week_boundaries(engine, two_profiles):
    one, _ = two_profiles
    with session_scope(engine) as session:
        _add(session, one, "Tomorrow", 1)
        _add(session, one, "Day Seven", 7)
        _add(session, one, "Day Eight", 8)

    with session_scope(engine) as session:
        result = needs_attention(session, one, today=TODAY)
    assert _names(result.this_week) == ["Tomorrow", "Day Seven"]  # day 8 is left out


def test_each_group_is_ordered_soonest_first(engine, two_profiles):
    one, _ = two_profiles
    with session_scope(engine) as session:
        _add(session, one, "Two Days Late", -2)
        _add(session, one, "Ten Days Late", -10)
        _add(session, one, "Five Days Ahead", 5)
        _add(session, one, "Two Days Ahead", 2)
        _add(session, one, "beta", 0)
        _add(session, one, "Alpha", 0)

    with session_scope(engine) as session:
        result = needs_attention(session, one, today=TODAY)
    assert _names(result.overdue) == ["Ten Days Late", "Two Days Late"]  # most overdue first
    assert _names(result.this_week) == ["Two Days Ahead", "Five Days Ahead"]
    assert _names(result.due_today) == ["Alpha", "beta"]  # same day: by company, ignoring case


def test_closed_applications_are_not_listed(engine, two_profiles):
    one, _ = two_profiles
    with session_scope(engine) as session:
        for status in ("Accepted", "Rejected", "Withdrawn"):
            _add(session, one, f"{status} Co", -1, status=status)
        _add(session, one, "Interview Co", -1, status="Interview")
        _add(session, one, "Ghosted Co", -1, status="Ghosted")  # still worth a nudge

    with session_scope(engine) as session:
        result = needs_attention(session, one, today=TODAY)
    assert set(_names(result.overdue)) == {"Interview Co", "Ghosted Co"}


def test_needs_attention_only_shows_that_profiles_applications(engine, two_profiles):
    one, two = two_profiles
    with session_scope(engine) as session:
        _add(session, one, "One Co", -1)
        _add(session, two, "Two Co", -1)

    with session_scope(engine) as session:
        assert _names(needs_attention(session, one, today=TODAY).overdue) == ["One Co"]
        assert _names(needs_attention(session, two, today=TODAY).overdue) == ["Two Co"]


def test_nothing_needs_attention_when_there_are_no_follow_ups(engine, two_profiles):
    one, _ = two_profiles
    with session_scope(engine) as session:
        create_application(session, one, "Acme", "Engineer")
    with session_scope(engine) as session:
        assert needs_attention(session, one, today=TODAY).total == 0


def test_grouping_follows_the_date_around_midnight(engine, two_profiles):
    """The same follow-up moves from 'this week' to 'today' to 'overdue' as the date
    changes. The clock is fixed by passing the date, so this never depends on when
    the test runs."""
    one, _ = two_profiles
    nov_20 = date(2026, 11, 20)
    with session_scope(engine) as session:
        app_id = create_application(session, one, "Acme", "Engineer").id
        set_follow_up(session, one, app_id, nov_20, "OA due")

    def group_on(day):
        with session_scope(engine) as session:
            result = needs_attention(session, one, today=day)
        return (
            "overdue"
            if result.overdue
            else "today"
            if result.due_today
            else "week"
            if result.this_week
            else "none"
        )

    assert group_on(date(2026, 11, 19)) == "week"  # 23:59 the night before
    assert group_on(date(2026, 11, 20)) == "today"  # one minute later
    assert group_on(date(2026, 11, 21)) == "overdue"  # the next midnight
    assert group_on(date(2026, 11, 12)) == "none"  # 8 days ahead: not yet


def test_default_clock_is_the_computers_local_date(engine, two_profiles, monkeypatch):
    from apply_spy.tracker import service

    one, _ = two_profiles
    with session_scope(engine) as session:
        _add(session, one, "Acme", 0)  # due on TODAY

    monkeypatch.setattr(service, "_today", lambda: TODAY)
    with session_scope(engine) as session:
        assert _names(needs_attention(session, one).due_today) == ["Acme"]
    monkeypatch.setattr(service, "_today", lambda: TODAY + timedelta(days=1))
    with session_scope(engine) as session:
        assert _names(needs_attention(session, one).overdue) == ["Acme"]


def test_the_default_clock_really_is_local_today():
    from apply_spy.tracker import service

    assert service._today() == date.today()


def test_needs_attention_with_1000_applications_is_fast(engine, two_profiles):
    one, _ = two_profiles
    with session_scope(engine) as session:
        for i in range(1000):
            app = create_application(session, one, f"Company {i % 50}", f"Engineer {i}")
            set_follow_up(session, one, app.id, TODAY + timedelta(days=(i % 40) - 10), "Check in")

    started = time.perf_counter()
    with session_scope(engine) as session:
        result = needs_attention(session, one, today=TODAY)
    elapsed = time.perf_counter() - started

    assert result.total == sum(1 for i in range(1000) if (i % 40) - 10 <= 7)
    assert elapsed < 1.0


# --- Calendar (dashboard) ------------------------------------------------------


def _follow_up_on(session, profile_id, company, day, status=None):
    app = create_application(session, profile_id, company, "Engineer")
    set_follow_up(session, profile_id, app.id, day, "Check in")
    if status:
        change_status(session, profile_id, app.id, status)
    return app


def _days(month):
    return [d for week in month.weeks for d in week]


def test_calendar_is_made_of_whole_weeks_starting_on_sunday(engine, two_profiles):
    one, _ = two_profiles
    with session_scope(engine) as session:
        month = calendar_month(session, one, 2026, 10, today=TODAY)  # Oct 1, 2026 is a Thursday

    assert all(len(week) == 7 for week in month.weeks)
    assert month.weeks[0][0].day == date(2026, 9, 27)  # the Sunday before Oct 1
    assert month.weeks[-1][-1].day == date(2026, 10, 31)  # Oct 31, 2026 is a Saturday
    assert all(week[0].day.weekday() == 6 for week in month.weeks)  # 6 = Sunday
    days = _days(month)
    assert [d.day for d in days] == sorted(d.day for d in days)  # in date order, no gaps
    assert all(b.day - a.day == timedelta(days=1) for a, b in zip(days, days[1:], strict=False))


def test_calendar_marks_which_days_belong_to_the_month(engine, two_profiles):
    one, _ = two_profiles
    with session_scope(engine) as session:
        month = calendar_month(session, one, 2026, 10, today=TODAY)
    inside = [d.day for d in _days(month) if d.in_month]
    assert inside[0] == date(2026, 10, 1) and inside[-1] == date(2026, 10, 31)
    assert len(inside) == 31
    assert not any(d.in_month for d in month.weeks[0] if d.day.month == 9)


def test_calendar_puts_follow_ups_on_their_day(engine, two_profiles):
    one, _ = two_profiles
    with session_scope(engine) as session:
        _follow_up_on(session, one, "Acme", date(2026, 11, 5))
        _follow_up_on(session, one, "beta", date(2026, 11, 5))
        _follow_up_on(session, one, "Globex", date(2026, 11, 30))

    with session_scope(engine) as session:
        month = calendar_month(session, one, 2026, 11, today=TODAY)
    by_day = {d.day: [a.company for a in d.applications] for d in _days(month) if d.applications}
    assert by_day == {date(2026, 11, 5): ["Acme", "beta"], date(2026, 11, 30): ["Globex"]}


def test_calendar_shows_neighbouring_month_follow_ups_in_the_grey_days(engine, two_profiles):
    one, _ = two_profiles
    with session_scope(engine) as session:
        _follow_up_on(session, one, "Early Dec", date(2026, 12, 2))  # in November's last week
        _follow_up_on(session, one, "Mid Dec", date(2026, 12, 20))  # not in the grid at all

    with session_scope(engine) as session:
        month = calendar_month(session, one, 2026, 11, today=TODAY)
    shown = {d.day: d for d in _days(month) if d.applications}
    assert list(shown) == [date(2026, 12, 2)]
    assert shown[date(2026, 12, 2)].in_month is False


def test_calendar_leaves_out_closed_applications_and_other_profiles(engine, two_profiles):
    one, two = two_profiles
    with session_scope(engine) as session:
        _follow_up_on(session, one, "Open Co", date(2026, 11, 10))
        _follow_up_on(session, one, "Rejected Co", date(2026, 11, 10), status="Rejected")
        _follow_up_on(session, two, "Other Profile Co", date(2026, 11, 10))

    with session_scope(engine) as session:
        month = calendar_month(session, one, 2026, 11, today=TODAY)
    [day] = [d for d in _days(month) if d.applications]
    assert [a.company for a in day.applications] == ["Open Co"]


@pytest.mark.parametrize(
    ("year", "month", "first_sunday", "weeks"),
    [
        (2026, 12, date(2026, 11, 29), 5),  # December spills back into November
        (2027, 1, date(2026, 12, 27), 6),  # starts in the previous year; Jan 31 is a Sunday
        (2028, 2, date(2028, 1, 30), 5),  # a leap February (29 days)
        (2026, 2, date(2026, 2, 1), 4),  # a February that fits in exactly 4 weeks
    ],
)
def test_calendar_handles_year_and_month_boundaries(
    engine, two_profiles, year, month, first_sunday, weeks
):
    one, _ = two_profiles
    with session_scope(engine) as session:
        result = calendar_month(session, one, year, month, today=TODAY)
    assert result.weeks[0][0].day == first_sunday
    assert len(result.weeks) == weeks
    assert (result.year, result.month) == (year, month)


def test_calendar_rejects_a_month_that_does_not_exist(engine, two_profiles):
    one, _ = two_profiles
    for bad in (0, 13, -1):
        with pytest.raises(ValueError):
            with session_scope(engine) as session:
                calendar_month(session, one, 2026, bad)


def test_calendar_today_follows_the_clock(engine, two_profiles, monkeypatch):
    from apply_spy.tracker import service

    one, _ = two_profiles
    monkeypatch.setattr(service, "_today", lambda: date(2026, 11, 19))
    with session_scope(engine) as session:
        assert calendar_month(session, one, 2026, 11).today == date(2026, 11, 19)


# --- Completing a follow-up ------------------------------------------------------


def test_completing_a_follow_up_clears_it_and_it_leaves_the_dashboard_and_calendar(
    engine, two_profiles
):
    one, _ = two_profiles
    with session_scope(engine) as session:
        app = _add(session, one, "Acme", -1)
        app_id = app.id
        _add(session, one, "Globex", 0)

    with session_scope(engine) as session:
        complete_follow_up(session, one, app_id)

    with session_scope(engine) as session:
        assert _names(needs_attention(session, one, today=TODAY).overdue) == []
        assert _names(needs_attention(session, one, today=TODAY).due_today) == ["Globex"]
        month = calendar_month(session, one, 2026, 11, today=TODAY)
        assert [a.company for d in _days(month) for a in d.applications] == ["Globex"]
        acme = next(a for a in list_applications(session, one) if a.company == "Acme")
        assert (acme.follow_up_date, acme.follow_up_label) == (None, None)


def test_completing_keeps_a_record_of_what_was_completed(engine, two_profiles):
    one, _ = two_profiles
    with session_scope(engine) as session:
        app_id = _add(session, one, "Acme", -1).id

    with session_scope(engine) as session:
        record = complete_follow_up(session, one, app_id)
        record_id = record.id
    assert (record.due_date, record.label) == (TODAY - timedelta(days=1), "Check in")

    with session_scope(engine) as session:
        [done] = list_completed_follow_ups(session, one)
    assert done.id == record_id
    assert (done.due_date, done.label) == (TODAY - timedelta(days=1), "Check in")
    assert done.application.company == "Acme"  # readable after the session closed
    assert done.completed_at.tzinfo is not None  # and it says when, in UTC


def test_completing_does_not_touch_status_history_or_other_details(engine, two_profiles):
    one, _ = two_profiles
    with session_scope(engine) as session:
        app_id = _add(session, one, "Acme", -1, status="Interview").id
        update_application_details(session, one, app_id, notes="Keep this")

    with session_scope(engine) as session:
        complete_follow_up(session, one, app_id)

    with session_scope(engine) as session:
        [app] = list_applications(session, one)
        assert (app.status, app.notes) == ("Interview", "Keep this")
        assert len(list_status_events(session, one, app_id)) == 2


def test_completing_with_nothing_to_clear_is_harmless(engine, two_profiles):
    one, _ = two_profiles
    with session_scope(engine) as session:
        app_id = create_application(session, one, "Acme", "Engineer").id
    with session_scope(engine) as session:
        assert complete_follow_up(session, one, app_id) is None
    with session_scope(engine) as session:
        assert list_completed_follow_ups(session, one) == []  # no empty record was made


def test_cannot_complete_another_profiles_follow_up(engine, two_profiles):
    one, two = two_profiles
    with session_scope(engine) as session:
        app_id = _add(session, one, "One Co", -1).id
    with pytest.raises(ApplicationNotFound):
        with session_scope(engine) as session:
            complete_follow_up(session, two, app_id)
    with session_scope(engine) as session:
        assert _names(needs_attention(session, one, today=TODAY).overdue) == ["One Co"]


# --- The applied date can never be blanked (detail-page Save guard) -------------


@pytest.mark.parametrize("bad", [None, "", "2026-11-01"])
def test_the_applied_date_cannot_be_cleared_or_replaced_by_text(engine, two_profiles, bad):
    one, _ = two_profiles
    with session_scope(engine) as session:
        app_id = create_application(
            session, one, "Acme", "Engineer", date_applied=date(2026, 11, 1)
        ).id

    with pytest.raises(InvalidApplication) as error:
        with session_scope(engine) as session:
            update_application_details(session, one, app_id, company="New Name", date_applied=bad)

    assert error.value.field == "date applied"
    assert "Date applied" in str(error.value)
    with session_scope(engine) as session:  # nothing was changed, not even the company
        [app] = list_applications(session, one)
    assert (app.company, app.date_applied) == ("Acme", date(2026, 11, 1))


# --- Timestamps always say they are UTC ------------------------------------------


def test_timestamps_come_back_with_their_time_zone(engine, two_profiles):
    from datetime import UTC

    one, _ = two_profiles
    with session_scope(engine) as session:
        app_id = create_application(session, one, "Acme", "Engineer").id

    with session_scope(engine) as session:
        [app] = list_applications(session, one)
        [event] = list_status_events(session, one, app_id)
    assert app.created_at.tzinfo is not None and app.created_at.utcoffset().total_seconds() == 0
    assert event.changed_at.tzinfo is not None
    assert abs((datetime.now(UTC) - app.created_at).total_seconds()) < 60  # and it is "now"


def test_old_rows_stored_without_a_zone_also_come_back_as_utc(engine, two_profiles):
    from datetime import UTC

    one, _ = two_profiles
    with session_scope(engine) as session:
        create_application(session, one, "Acme", "Engineer")
    with engine.begin() as conn:  # the way older versions stored it: a plain time, no zone
        conn.execute(text("UPDATE applications SET created_at = '2026-11-19 01:19:16.540099'"))

    with session_scope(engine) as session:
        [app] = list_applications(session, one)
    assert app.created_at == datetime(2026, 11, 19, 1, 19, 16, 540099, tzinfo=UTC)


def test_the_csv_export_marks_the_added_column_as_utc_without_microseconds(engine, two_profiles):
    from datetime import UTC

    one, _ = two_profiles
    with session_scope(engine) as session:
        create_application(session, one, "Acme", "Engineer")
    with engine.begin() as conn:
        conn.execute(text("UPDATE applications SET created_at = '2026-11-19 01:19:16.540099'"))

    with session_scope(engine) as session:
        rows = _read_csv(export_applications_csv(session, one))
    added = dict(zip(rows[0], rows[1], strict=True))["Added"]
    assert added == "2026-11-19T01:19:16+00:00"
    assert datetime.fromisoformat(added) == datetime(2026, 11, 19, 1, 19, 16, tzinfo=UTC)


def test_the_date_applied_column_is_still_a_plain_date_in_the_export(engine, two_profiles):
    one, _ = two_profiles
    with session_scope(engine) as session:
        create_application(session, one, "Acme", "Engineer", date_applied=date(2026, 11, 1))
    with session_scope(engine) as session:
        rows = _read_csv(export_applications_csv(session, one))
    assert dict(zip(rows[0], rows[1], strict=True))["Date applied"] == "2026-11-01"


# --- Completed follow-ups: Undo, listing, counts ------------------------------------


def test_undo_restores_the_follow_up_and_removes_the_record(engine, two_profiles):
    one, _ = two_profiles
    with session_scope(engine) as session:
        app_id = _add(session, one, "Acme", -1).id
    with session_scope(engine) as session:
        record_id = complete_follow_up(session, one, app_id).id

    with session_scope(engine) as session:
        undo_complete_follow_up(session, one, record_id)

    with session_scope(engine) as session:
        assert _names(needs_attention(session, one, today=TODAY).overdue) == ["Acme"]
        assert list_completed_follow_ups(session, one) == []
        assert count_completed_follow_ups(session, one) == 0
        [app] = list_applications(session, one)
        assert (app.follow_up_date, app.follow_up_label) == (TODAY - timedelta(days=1), "Check in")


def test_undo_will_not_overwrite_a_newer_follow_up(engine, two_profiles):
    one, _ = two_profiles
    with session_scope(engine) as session:
        app_id = _add(session, one, "Acme", -1).id
    with session_scope(engine) as session:
        record_id = complete_follow_up(session, one, app_id).id
        set_follow_up(session, one, app_id, TODAY + timedelta(days=5), "New plan")

    with pytest.raises(InvalidApplication, match="already has a new follow-up"):
        with session_scope(engine) as session:
            undo_complete_follow_up(session, one, record_id)

    with session_scope(engine) as session:
        [app] = list_applications(session, one)
        assert app.follow_up_label == "New plan"  # untouched
        assert count_completed_follow_ups(session, one) == 1  # the record is kept


def test_completed_list_is_newest_first_and_counted(engine, two_profiles):
    one, _ = two_profiles
    with session_scope(engine) as session:
        ids = [_add(session, one, name, -1).id for name in ("First Co", "Second Co", "Third Co")]
    for app_id in ids:  # completed one after another, in separate transactions
        with session_scope(engine) as session:
            complete_follow_up(session, one, app_id)

    with session_scope(engine) as session:
        assert [c.application.company for c in list_completed_follow_ups(session, one)] == [
            "Third Co",
            "Second Co",
            "First Co",
        ]
        assert [
            c.application.company for c in list_completed_follow_ups(session, one, limit=2)
        ] == [
            "Third Co",
            "Second Co",
        ]
        assert count_completed_follow_ups(session, one) == 3


def test_the_same_application_can_be_completed_many_times(engine, two_profiles):
    one, _ = two_profiles
    with session_scope(engine) as session:
        app_id = create_application(session, one, "Acme", "Engineer").id
    for day in (3, 10):
        with session_scope(engine) as session:
            set_follow_up(session, one, app_id, TODAY + timedelta(days=day), f"Round {day}")
        with session_scope(engine) as session:
            complete_follow_up(session, one, app_id)

    with session_scope(engine) as session:
        assert [c.label for c in list_completed_follow_ups(session, one)] == ["Round 10", "Round 3"]


def test_completed_follow_ups_are_private_to_each_profile(engine, two_profiles):
    one, two = two_profiles
    with session_scope(engine) as session:
        one_app = _add(session, one, "One Co", -1).id
        two_app = _add(session, two, "Two Co", -1).id
    with session_scope(engine) as session:
        one_record = complete_follow_up(session, one, one_app).id
        complete_follow_up(session, two, two_app)

    with session_scope(engine) as session:
        assert [c.application.company for c in list_completed_follow_ups(session, one)] == [
            "One Co"
        ]
        assert [c.application.company for c in list_completed_follow_ups(session, two)] == [
            "Two Co"
        ]
        assert count_completed_follow_ups(session, one) == 1
    with pytest.raises(CompletedFollowUpNotFound):  # profile two cannot undo profile one's
        with session_scope(engine) as session:
            undo_complete_follow_up(session, two, one_record)


def test_deleting_a_profile_removes_its_completed_follow_ups_only(engine, two_profiles):
    from apply_spy.profiles import delete_profile

    one, two = two_profiles
    with session_scope(engine) as session:
        one_app = _add(session, one, "One Co", -1).id
        two_app = _add(session, two, "Two Co", -1).id
    with session_scope(engine) as session:
        complete_follow_up(session, one, one_app)
        complete_follow_up(session, two, two_app)

    with session_scope(engine) as session:
        delete_profile(session, one, confirmed=True)

    with session_scope(engine) as session:
        assert count_completed_follow_ups(session, one) == 0
        assert count_completed_follow_ups(session, two) == 1
    with engine.connect() as conn:
        assert conn.execute(text("PRAGMA foreign_key_check")).all() == []  # no orphans


def test_the_sidebar_badge_counts_overdue_plus_due_today(engine, two_profiles):
    one, _ = two_profiles
    with session_scope(engine) as session:
        _add(session, one, "Late Co", -3)
        _add(session, one, "Today Co", 0)
        _add(session, one, "Soon Co", 4)
    with session_scope(engine) as session:
        found = needs_attention(session, one, today=TODAY)
    assert found.urgent == 2  # this-week items are not urgent
    assert found.total == 3


# --- Upcoming and later follow-ups ---------------------------------------------------


def test_upcoming_starts_today_is_soonest_first_and_respects_the_limit(engine, two_profiles):
    one, _ = two_profiles
    with session_scope(engine) as session:
        _add(session, one, "Yesterday Co", -1)  # in the past: not upcoming
        _add(session, one, "Month Co", 30)
        _add(session, one, "Today Co", 0)
        _add(session, one, "Week Co", 5)
        _add(session, one, "Closed Co", 2, status="Rejected")  # closed: left out

    with session_scope(engine) as session:
        assert _names(upcoming_follow_ups(session, one, today=TODAY)) == [
            "Today Co",
            "Week Co",
            "Month Co",
        ]
        assert _names(upcoming_follow_ups(session, one, today=TODAY, limit=2)) == [
            "Today Co",
            "Week Co",
        ]


def test_later_means_beyond_the_this_week_window(engine, two_profiles):
    one, _ = two_profiles
    with session_scope(engine) as session:
        _add(session, one, "Day Seven", 7)  # still "this week"
        _add(session, one, "Day Eight", 8)
        _add(session, one, "Far Co", 40)
        _add(session, one, "Closed Far", 40, status="Withdrawn")

    with session_scope(engine) as session:
        assert _names(later_follow_ups(session, one, today=TODAY)) == ["Day Eight", "Far Co"]


def test_upcoming_and_later_are_per_profile(engine, two_profiles):
    one, two = two_profiles
    with session_scope(engine) as session:
        _add(session, one, "One Co", 2)
        _add(session, two, "Two Co", 2)
        _add(session, two, "Two Far", 30)
    with session_scope(engine) as session:
        assert _names(upcoming_follow_ups(session, one, today=TODAY)) == ["One Co"]
        assert _names(later_follow_ups(session, one, today=TODAY)) == []
        assert _names(later_follow_ups(session, two, today=TODAY)) == ["Two Far"]
