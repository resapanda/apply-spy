from pathlib import Path

import pytest

from apply_spy import config


def test_default_data_dir_does_not_depend_on_the_current_folder(tmp_path, monkeypatch):
    monkeypatch.delenv("APPLY_SPY_DATA_DIR", raising=False)
    before = config.default_data_dir()

    elsewhere_a, elsewhere_b = tmp_path / "a", tmp_path / "b"
    elsewhere_a.mkdir()
    elsewhere_b.mkdir()
    monkeypatch.chdir(elsewhere_a)
    in_a = config.default_data_dir()
    monkeypatch.chdir(elsewhere_b)
    in_b = config.default_data_dir()

    assert before == in_a == in_b  # the same folder wherever the app is started
    assert in_a.is_absolute()
    assert in_a.name == "data"
    assert (in_a.parent / "pyproject.toml").is_file()  # it is the project's own data/ folder


def test_the_environment_variable_still_wins(tmp_path, monkeypatch):
    monkeypatch.setenv("APPLY_SPY_DATA_DIR", str(tmp_path / "custom"))
    assert config.default_data_dir() == tmp_path / "custom"


def test_looking_up_the_default_does_not_create_it(tmp_path, monkeypatch):
    monkeypatch.setenv("APPLY_SPY_DATA_DIR", str(tmp_path / "not-yet"))
    config.default_data_dir()
    assert not (tmp_path / "not-yet").exists()
    config.data_dir()  # this one creates it
    assert (tmp_path / "not-yet").is_dir()


def test_outside_a_project_checkout_the_fallback_is_a_fixed_home_folder(monkeypatch):
    monkeypatch.delenv("APPLY_SPY_DATA_DIR", raising=False)
    monkeypatch.setattr(config, "_project_root", lambda: None)
    assert config.default_data_dir() == Path.home() / "apply-spy-data"


def test_the_cli_does_not_import_nicegui_before_serve_sets_its_storage_folder():
    """serve points NiceGUI's state folder into the data folder, which only works if
    NiceGUI has not been imported yet. This fails if someone adds an early import."""
    import subprocess
    import sys

    result = subprocess.run(
        [sys.executable, "-c", "import apply_spy.cli, sys; print('nicegui' in sys.modules)"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.strip() == "False"


# --- The session secret ------------------------------------------------------------


def test_the_session_secret_is_random_private_and_kept_between_starts(tmp_path, monkeypatch):
    import stat

    monkeypatch.setenv("APPLY_SPY_DATA_DIR", str(tmp_path / "data"))
    first = config.session_secret()
    second = config.session_secret()  # a restart: same secret, so the cookie stays valid

    assert first == second
    assert len(first) >= 40 and first != "apply-spy-local"
    path = tmp_path / "data" / ".nicegui" / "session-secret"
    assert path.read_text(encoding="utf-8") == first
    assert stat.S_IMODE(path.stat().st_mode) == 0o600  # readable only by you


def test_each_data_folder_gets_its_own_secret(tmp_path, monkeypatch):
    monkeypatch.setenv("APPLY_SPY_DATA_DIR", str(tmp_path / "one"))
    one = config.session_secret()
    monkeypatch.setenv("APPLY_SPY_DATA_DIR", str(tmp_path / "two"))
    assert config.session_secret() != one


def test_starting_twice_at_the_same_moment_agrees_on_one_secret(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor

    monkeypatch.setenv("APPLY_SPY_DATA_DIR", str(tmp_path / "data"))
    with ThreadPoolExecutor(max_workers=8) as pool:
        secrets_seen = set(pool.map(lambda _: config.session_secret(), range(40)))
    assert len(secrets_seen) == 1


def test_an_empty_secret_file_is_replaced_not_used(tmp_path, monkeypatch):
    monkeypatch.setenv("APPLY_SPY_DATA_DIR", str(tmp_path / "data"))
    path = tmp_path / "data" / ".nicegui" / "session-secret"
    path.parent.mkdir(parents=True)
    path.write_text("", encoding="utf-8")
    assert config.session_secret() != ""


@pytest.mark.parametrize("damaged", [b"", b"   \n", b"\xff\xfe\x00 not text"])
def test_a_damaged_secret_file_is_replaced_and_never_hangs_the_app(tmp_path, monkeypatch, damaged):
    monkeypatch.setenv("APPLY_SPY_DATA_DIR", str(tmp_path / "data"))
    path = tmp_path / "data" / ".nicegui" / "session-secret"
    path.parent.mkdir(parents=True)
    path.write_bytes(damaged)

    secret = config.session_secret()

    assert len(secret) >= 40
    assert path.read_text(encoding="utf-8") == secret  # fixed on disk, so it stays stable
    assert config.session_secret() == secret
    assert [p.name for p in path.parent.iterdir()] == ["session-secret"]  # no temp leftovers
