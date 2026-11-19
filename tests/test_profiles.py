import pytest
from sqlalchemy import select

from apply_spy.cli import main
from apply_spy.db import Profile, make_engine, session_scope
from apply_spy.profiles import (
    DuplicateProfileName,
    InvalidProfileName,
    create_profile,
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
