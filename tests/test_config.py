from pathlib import Path

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
