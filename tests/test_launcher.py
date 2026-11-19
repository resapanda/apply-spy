import os
import plistlib
import struct
import subprocess
import sys

import pytest

from apply_spy.cli import main
from apply_spy.launcher import (
    BUNDLE_ID,
    LauncherError,
    icon_png,
    install_icon,
    launcher_script,
)


@pytest.fixture
def fake_uv(tmp_path):
    uv = tmp_path / "bin" / "uv"
    uv.parent.mkdir()
    uv.write_text("#!/bin/bash\n", encoding="utf-8")
    uv.chmod(0o755)
    return uv


@pytest.fixture
def project(tmp_path):
    folder = tmp_path / "my projects" / "apply-spy"  # a space, on purpose
    folder.mkdir(parents=True)
    return folder


def build(tmp_path, fake_uv, project, **kwargs):
    return install_icon(tmp_path / "Applications", uv_path=fake_uv, project=project, **kwargs)


# --- The app bundle ------------------------------------------------------------


def test_the_app_has_the_structure_macos_expects(tmp_path, fake_uv, project):
    app = build(tmp_path, fake_uv, project, make_icon=False)

    assert app == tmp_path / "Applications" / "apply-spy.app"
    plist = plistlib.loads((app / "Contents" / "Info.plist").read_bytes())
    assert plist["CFBundleIdentifier"] == BUNDLE_ID
    assert plist["CFBundleExecutable"] == "apply-spy"
    assert plist["CFBundlePackageType"] == "APPL"
    assert plist["LSArchitecturePriority"][0] == "arm64"  # prefer native on Apple chips
    script = app / "Contents" / "MacOS" / "apply-spy"
    assert script.is_file() and os.access(script, os.X_OK)
    assert [p.name for p in (tmp_path / "Applications").iterdir()] == [
        "apply-spy.app"
    ]  # no leftovers


def test_the_script_is_valid_bash_and_quotes_paths_with_spaces(tmp_path, fake_uv, project):
    app = build(tmp_path, fake_uv, project, make_icon=False)
    script = app / "Contents" / "MacOS" / "apply-spy"

    subprocess.run(["bash", "-n", str(script)], check=True)  # syntax check only
    text = script.read_text(encoding="utf-8")
    assert f"PROJECT='{project}'" in text  # quoted, so the space is safe
    assert f"UV={fake_uv}" in text
    assert "run --project" in text and "apply-spy serve" in text


def test_installing_again_replaces_our_app(tmp_path, fake_uv, project):
    first = build(tmp_path, fake_uv, project, make_icon=False)
    (first / "stale-file").write_text("old", encoding="utf-8")

    second = build(tmp_path, fake_uv, project, make_icon=False)

    assert second == first and not (second / "stale-file").exists()


def test_it_never_overwrites_an_app_that_is_not_ours(tmp_path, fake_uv, project):
    other = tmp_path / "Applications" / "apply-spy.app"
    (other / "Contents").mkdir(parents=True)
    (other / "Contents" / "Info.plist").write_bytes(
        plistlib.dumps({"CFBundleIdentifier": "com.someone.else"})
    )
    (other / "precious").write_text("keep", encoding="utf-8")

    with pytest.raises(LauncherError):
        build(tmp_path, fake_uv, project, make_icon=False)

    assert (other / "precious").read_text(encoding="utf-8") == "keep"


def test_a_missing_uv_is_a_clear_error(tmp_path, project):
    with pytest.raises(LauncherError, match="uv"):
        install_icon(tmp_path / "Apps", uv_path=tmp_path / "no-such-uv", project=project)
    assert not (tmp_path / "Apps" / "apply-spy.app").exists()


def test_running_outside_a_project_folder_is_a_clear_error(tmp_path, fake_uv, monkeypatch):
    monkeypatch.setattr("apply_spy.launcher._project_root", lambda: None)
    with pytest.raises(LauncherError, match="project folder"):
        install_icon(tmp_path / "Apps", uv_path=fake_uv)


# --- The icon picture --------------------------------------------------------------


def test_the_icon_picture_is_a_valid_png_of_the_right_size():
    data = icon_png(64)
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    width, height = struct.unpack(">II", data[16:24])
    assert (width, height) == (64, 64)


@pytest.mark.skipif(sys.platform != "darwin", reason="uses macOS's sips and iconutil")
def test_the_app_gets_a_real_icon_on_macos(tmp_path, fake_uv, project):
    app = build(tmp_path, fake_uv, project)
    icon = app / "Contents" / "Resources" / "AppIcon.icns"
    assert icon.is_file() and icon.read_bytes()[:4] == b"icns"


# --- What the launcher script does when it runs ------------------------------------
# The script is run for real, but curl, open, uv and osascript are small stand-ins that
# record how they were called, so every branch can be checked without a server or a screen.


def _stub(folder, name, body):
    path = folder / name
    path.write_text("#!/bin/bash\n" + body, encoding="utf-8")
    path.chmod(0o755)


def run_launcher(
    tmp_path, project, *, server_up_at_start, server_ever_starts, tries="3", apple_chip=True
):
    bin_dir = tmp_path / "stubs"
    bin_dir.mkdir()
    log = tmp_path / "calls.log"
    started = tmp_path / "server-started"
    if server_up_at_start:
        started.write_text("yes", encoding="utf-8")
    _stub(
        bin_dir,
        "uv",
        f'echo "uv $*" >> {log}\n' + (f"touch {started}\n" if server_ever_starts else ""),
    )
    _stub(
        bin_dir, "curl", f'[ -f {started} ] && echo "<title>apply-spy</title>" && exit 0\nexit 7\n'
    )
    _stub(bin_dir, "open", f'echo "open $*" >> {log}\n')
    _stub(bin_dir, "osascript", f'echo "osascript alert" >> {log}\n')
    _stub(bin_dir, "sysctl", f"echo {1 if apple_chip else 0}\n")
    _stub(bin_dir, "arch", f'echo "arch $1" >> {log}\nshift\nexec "$@"\n')
    script = tmp_path / "launcher.sh"
    script.write_text(
        launcher_script(bin_dir / "uv", project, tmp_path / "data" / "apply-spy.log"),
        encoding="utf-8",
    )
    result = subprocess.run(
        ["bash", str(script)],
        env={"PATH": "/usr/bin:/bin", "APPLY_SPY_LAUNCH_TRIES": tries},
        capture_output=True,
        text=True,
        timeout=30,
    )
    calls = log.read_text(encoding="utf-8").splitlines() if log.exists() else []
    return result.returncode, calls


def test_when_already_running_it_only_opens_the_browser(tmp_path, project):
    code, calls = run_launcher(tmp_path, project, server_up_at_start=True, server_ever_starts=False)
    assert code == 0
    assert calls == ["open http://127.0.0.1:8080/"]  # no second server was started


def test_when_not_running_it_starts_the_server_in_the_background(tmp_path, project):
    code, calls = run_launcher(tmp_path, project, server_up_at_start=False, server_ever_starts=True)
    assert code == 0
    # The server opens the browser itself. On an Apple chip it is forced to run natively.
    assert calls == ["arch -arm64", f"uv run --project {project} apply-spy serve"]


def test_on_an_intel_mac_it_does_not_force_an_architecture(tmp_path, project):
    code, calls = run_launcher(
        tmp_path, project, server_up_at_start=False, server_ever_starts=True, apple_chip=False
    )
    assert code == 0
    assert calls == [f"uv run --project {project} apply-spy serve"]  # no 'arch' call


def test_when_it_cannot_start_it_shows_an_error_instead_of_failing_silently(tmp_path, project):
    code, calls = run_launcher(
        tmp_path, project, server_up_at_start=False, server_ever_starts=False
    )
    assert code == 1
    assert calls[-1] == "osascript alert"


# --- The command ---------------------------------------------------------------------


@pytest.mark.skipif(sys.platform != "darwin", reason="the command is macOS-only")
def test_cli_install_icon_reports_what_it_made(tmp_path, capsys, monkeypatch):
    uv = tmp_path / "uv"
    uv.write_text("#!/bin/bash\n", encoding="utf-8")
    uv.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tmp_path}:{os.environ['PATH']}")

    assert main(["install-icon", "--dest", str(tmp_path / "Apps")]) == 0

    out = capsys.readouterr().out
    assert "apply-spy.app" in out and "Quit button" in out
    assert (tmp_path / "Apps" / "apply-spy.app" / "Contents" / "MacOS" / "apply-spy").exists()


def test_cli_install_icon_refuses_outside_macos(monkeypatch, capsys):
    monkeypatch.setattr(sys, "platform", "linux")
    assert main(["install-icon"]) == 1
    assert "only available on macOS" in capsys.readouterr().out
