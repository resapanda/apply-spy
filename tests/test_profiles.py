import pytest
from sqlalchemy import select, text

from apply_spy.cli import main
from apply_spy.db import Profile, make_engine, session_scope
from apply_spy.profiles import (
    ConfirmationRequired,
    DuplicateProfileName,
    InvalidProfileName,
    ProfileNotFound,
    create_profile,
    delete_profile,
    get_active_profile,
    list_profiles,
)


def test_create_profile_stores_name_and_creation_time(engine):
    with session_scope(engine) as session:
        profile = create_profile(session, "Haruka")

    with session_scope(engine) as session:
        profiles = list_profiles(session)
    assert [p.name for p in profiles] == ["Haruka"]
    assert profiles[0].id == profile.id
    assert profiles[0].created_at is not None


def test_name_is_trimmed_and_spaces_collapsed(engine):
    with session_scope(engine) as session:
        profile = create_profile(session, "  Haruka   Banin ")
    assert profile.name == "Haruka Banin"


@pytest.mark.parametrize("name", ["", "   ", "\n\t"])
def test_empty_name_is_rejected_and_nothing_is_created(engine, name):
    with pytest.raises(InvalidProfileName):
        with session_scope(engine) as session:
            create_profile(session, name)
    with session_scope(engine) as session:
        assert list_profiles(session) == []


def test_too_long_name_is_rejected(engine):
    with pytest.raises(InvalidProfileName):
        with session_scope(engine) as session:
            create_profile(session, "x" * 101)


@pytest.mark.parametrize("second", ["Haruka", "haruka", "  HARUKA "])
def test_duplicate_name_is_rejected_ignoring_case(engine, second):
    with session_scope(engine) as session:
        create_profile(session, "Haruka")
    with pytest.raises(DuplicateProfileName):
        with session_scope(engine) as session:
            create_profile(session, second)
    with session_scope(engine) as session:
        assert len(list_profiles(session)) == 1


def test_database_itself_blocks_case_insensitive_duplicates(engine):
    """Even if the service check were skipped, the unique index holds."""
    from sqlalchemy.exc import IntegrityError

    with pytest.raises(IntegrityError):
        with session_scope(engine) as session:
            session.add_all([Profile(name="Haruka"), Profile(name="haruka")])


def test_profiles_listed_in_creation_order(engine):
    with session_scope(engine) as session:
        create_profile(session, "Haruka")
        create_profile(session, "Husband")
    with session_scope(engine) as session:
        assert [p.name for p in list_profiles(session)] == ["Haruka", "Husband"]


def test_cli_add_and_list(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("APPLY_SPY_DATA_DIR", str(tmp_path))

    assert main(["profile", "add", "Haruka"]) == 0
    assert main(["profile", "add", "haruka"]) == 1  # duplicate
    assert main(["profile", "add", " "]) == 1  # empty
    assert main(["profile", "list"]) == 0

    out = capsys.readouterr().out
    assert "Created profile 'Haruka'" in out
    assert "already exists" in out
    assert "Please enter a name" in out
    assert out.strip().splitlines()[-1].endswith("Haruka")
    assert (tmp_path / "apply_spy.db").exists()
    with session_scope(make_engine()) as session:
        assert len(session.scalars(select(Profile)).all()) == 1


def test_active_profile_is_the_one_picked(engine):
    with session_scope(engine) as session:
        first = create_profile(session, "Sample One")
        second = create_profile(session, "Sample Two")
        first_id, second_id = first.id, second.id

    with session_scope(engine) as session:
        assert get_active_profile(session, second_id).id == second_id
        assert get_active_profile(session, first_id).id == first_id


def test_active_profile_falls_back_to_first_when_unset_or_missing(engine):
    with session_scope(engine) as session:
        first = create_profile(session, "Sample One")
        create_profile(session, "Sample Two")
        first_id = first.id

    with session_scope(engine) as session:
        assert get_active_profile(session, None).id == first_id
        assert get_active_profile(session, 9999).id == first_id


def test_active_profile_is_none_when_there_are_no_profiles(engine):
    with session_scope(engine) as session:
        assert get_active_profile(session, None) is None
        assert get_active_profile(session, 1) is None


def _make_two_profiles_with_owned_rows(engine):
    """Two profiles plus a stand-in table that, like real tables, has a profile_id.

    (Real tables such as applications arrive in later stories.)
    """
    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE sample_owned (id INTEGER PRIMARY KEY, "
                "profile_id INTEGER NOT NULL REFERENCES profiles(id) ON DELETE CASCADE)"
            )
        )
    with session_scope(engine) as session:
        one = create_profile(session, "Sample One").id
        two = create_profile(session, "Sample Two").id
    with engine.begin() as conn:
        for profile_id in (one, one, two):
            conn.execute(
                text("INSERT INTO sample_owned (profile_id) VALUES (:p)"), {"p": profile_id}
            )
    return one, two


def test_delete_profile_removes_it_and_its_data_but_not_the_other_profiles(engine):
    one, two = _make_two_profiles_with_owned_rows(engine)

    with session_scope(engine) as session:
        delete_profile(session, one, confirmed=True)

    with session_scope(engine) as session:
        assert [p.id for p in list_profiles(session)] == [two]
    with engine.connect() as conn:
        owners = conn.execute(text("SELECT profile_id FROM sample_owned")).scalars().all()
        assert owners == [two]
        assert conn.execute(text("PRAGMA foreign_key_check")).all() == []  # no orphans


@pytest.mark.parametrize("confirmed", [False, None, "yes", 1])
def test_delete_without_explicit_confirmation_changes_nothing(engine, confirmed):
    one, _two = _make_two_profiles_with_owned_rows(engine)

    with pytest.raises(ConfirmationRequired):
        with session_scope(engine) as session:
            delete_profile(session, one, confirmed)

    with session_scope(engine) as session:
        assert len(list_profiles(session)) == 2
    with engine.connect() as conn:
        assert len(conn.execute(text("SELECT * FROM sample_owned")).all()) == 3


def test_delete_unknown_profile_is_an_error(engine):
    with pytest.raises(ProfileNotFound):
        with session_scope(engine) as session:
            delete_profile(session, 999, confirmed=True)


def test_deleting_the_active_profile_falls_back_to_the_remaining_one(engine):
    with session_scope(engine) as session:
        one = create_profile(session, "Sample One").id
        two = create_profile(session, "Sample Two").id
    with session_scope(engine) as session:
        delete_profile(session, one, confirmed=True)
    with session_scope(engine) as session:
        assert get_active_profile(session, one).id == two


def test_cli_delete_needs_a_yes(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("APPLY_SPY_DATA_DIR", str(tmp_path))
    assert main(["profile", "add", "Haruka"]) == 0

    monkeypatch.setattr("builtins.input", lambda _prompt: "no")
    assert main(["profile", "delete", "1"]) == 1  # cancelled
    assert main(["profile", "list"]) == 0
    assert capsys.readouterr().out.strip().splitlines()[-1].endswith("Haruka")

    monkeypatch.setattr("builtins.input", lambda _prompt: "yes")
    assert main(["profile", "delete", "1"]) == 0
    assert main(["profile", "delete", "1"]) == 1  # already gone
    with session_scope(make_engine()) as session:
        assert session.scalars(select(Profile)).all() == []
