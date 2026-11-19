"""A macOS app icon that starts apply-spy with a double-click.

`apply-spy install-icon` builds ~/Applications/apply-spy.app. The app is only a small
launcher: each click makes sure apply-spy is running and opens it in the browser. It
runs quietly in the background; use the Quit button in the page to stop it.
"""

import math
import os
import plistlib
import shlex
import shutil
import stat
import struct
import subprocess
import tempfile
import zlib
from pathlib import Path

from apply_spy.config import LOG_FILENAME, PORT, _project_root, standard_data_dir

APP_NAME = "apply-spy"
BUNDLE_ID = "local.apply-spy.launcher"


class LauncherError(Exception):
    """The icon could not be made. Nothing was changed."""


def launcher_script(uv_path: Path, project: Path, log_path: Path, port: int = PORT) -> str:
    """The shell script inside the app. Paths are quoted so spaces are safe."""
    return f"""#!/bin/bash
# Made by `apply-spy install-icon`. It only starts apply-spy; you can delete this app any time.
UV={shlex.quote(str(uv_path))}
PROJECT={shlex.quote(str(project))}
LOG={shlex.quote(str(log_path))}
URL="http://127.0.0.1:{port}/"
export PATH="$(dirname "$UV"):/usr/bin:/bin:/usr/sbin:/sbin:$PATH"

# Already running? Just open it in the browser.
if curl -s --max-time 2 "$URL" | grep -q "apply-spy"; then
  open "$URL"
  exit 0
fi

# Not running: start it in the background. The server opens the browser itself.
mkdir -p "$(dirname "$LOG")"
cd "$PROJECT" || exit 1
# On a Mac with an Apple chip, an app started from the Finder can end up running in
# compatibility (Rosetta) mode, and everything it starts inherits that. Python's compiled
# parts are native, so they would fail to load. Force the native architecture.
RUN=("$UV")
if [ "$(sysctl -n hw.optional.arm64 2>/dev/null)" = "1" ]; then
  RUN=(arch -arm64 "$UV")
fi
nohup "${{RUN[@]}}" run --project "$PROJECT" apply-spy serve >"$LOG" 2>&1 &

for _ in $(seq 1 "${{APPLY_SPY_LAUNCH_TRIES:-60}}"); do
  if curl -s --max-time 1 "$URL" | grep -q "apply-spy"; then
    exit 0
  fi
  sleep 0.5
done

osascript -e 'on run argv' \\
  -e 'display alert "apply-spy did not start" message ("Details are in: " & item 1 of argv)' \\
  -e 'end run' "$LOG" >/dev/null 2>&1
exit 1
"""


def _info_plist() -> bytes:
    return plistlib.dumps(
        {
            "CFBundleName": APP_NAME,
            "CFBundleDisplayName": APP_NAME,
            "CFBundleIdentifier": BUNDLE_ID,
            "CFBundleExecutable": APP_NAME,
            "CFBundlePackageType": "APPL",
            "CFBundleIconFile": "AppIcon",
            "CFBundleVersion": "1",
            "CFBundleShortVersionString": "1.0",
            "LSMinimumSystemVersion": "11.0",
            # Prefer the native architecture (see the note in the launcher script).
            "LSArchitecturePriority": ["arm64", "x86_64"],
            "NSHighResolutionCapable": True,
        }
    )


# --- The icon picture, drawn in code so no image file has to be stored ---------


def _png(width: int, height: int, rgba: bytes) -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    rows = b"".join(b"\x00" + rgba[y * width * 4 : (y + 1) * width * 4] for y in range(height))
    header = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)  # 8-bit RGBA
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(rows, 6))
        + chunk(b"IEND", b"")
    )


def icon_png(size: int = 512) -> bytes:
    """A blue rounded square with a white magnifying glass."""
    pixels = bytearray(size * size * 4)
    cx, cy, ring_r, ring_w = 0.44, 0.44, 0.215, 0.075
    sx, sy, ex, ey, handle_r = 0.60, 0.60, 0.80, 0.80, 0.045
    dx, dy = ex - sx, ey - sy
    seg_len2 = dx * dx + dy * dy
    for py in range(size):
        y = (py + 0.5) / size
        for px in range(size):
            x = (px + 0.5) / size
            # Rounded square background (signed distance, negative inside).
            qx, qy = abs(x - 0.5) - 0.5 + 0.22, abs(y - 0.5) - 0.5 + 0.22
            d_box = math.hypot(max(qx, 0), max(qy, 0)) + min(max(qx, qy), 0) - 0.22
            cover = min(max(0.5 - d_box * size, 0.0), 1.0)
            if cover <= 0:
                continue
            # Magnifying glass: a ring and a handle (distance to a line segment).
            d_ring = abs(math.hypot(x - cx, y - cy) - ring_r) - ring_w / 2
            t = min(max(((x - sx) * dx + (y - sy) * dy) / seg_len2, 0.0), 1.0)
            d_handle = math.hypot(x - (sx + t * dx), y - (sy + t * dy)) - handle_r
            white = min(max(0.5 - min(d_ring, d_handle) * size, 0.0), 1.0)
            shade = 0.15 * y  # slightly darker towards the bottom
            red, green, blue = 25 * (1 - shade), 118 * (1 - shade), 210 * (1 - shade)
            i = (py * size + px) * 4
            pixels[i] = round(red + (255 - red) * white)
            pixels[i + 1] = round(green + (255 - green) * white)
            pixels[i + 2] = round(blue + (255 - blue) * white)
            pixels[i + 3] = round(255 * cover)
    return _png(size, size, bytes(pixels))


def _make_icns(target: Path) -> bool:
    """Turn the picture into an .icns file with macOS's own sips and iconutil.
    Returns False (and the app simply gets the plain default icon) if that fails."""
    if not (shutil.which("sips") and shutil.which("iconutil")):
        return False
    with tempfile.TemporaryDirectory() as scratch:
        base = Path(scratch) / "base.png"
        base.write_bytes(icon_png(512))
        iconset = Path(scratch) / "AppIcon.iconset"
        iconset.mkdir()
        sizes = {
            "icon_16x16.png": 16,
            "icon_16x16@2x.png": 32,
            "icon_32x32.png": 32,
            "icon_32x32@2x.png": 64,
            "icon_128x128.png": 128,
            "icon_128x128@2x.png": 256,
            "icon_256x256.png": 256,
            "icon_256x256@2x.png": 512,
            "icon_512x512.png": 512,
        }
        try:
            for name, pixels in sizes.items():
                subprocess.run(
                    [
                        "sips",
                        "-z",
                        str(pixels),
                        str(pixels),
                        str(base),
                        "--out",
                        str(iconset / name),
                    ],
                    check=True,
                    capture_output=True,
                )
            subprocess.run(
                ["iconutil", "-c", "icns", str(iconset), "-o", str(target)],
                check=True,
                capture_output=True,
            )
        except (subprocess.CalledProcessError, OSError):
            return False
    return target.is_file()


# --- Building the app -------------------------------------------------------------


def _is_ours(app: Path) -> bool:
    try:
        with open(app / "Contents" / "Info.plist", "rb") as file:
            return plistlib.load(file).get("CFBundleIdentifier") == BUNDLE_ID
    except (OSError, plistlib.InvalidFileException):
        return False


def install_icon(
    dest_dir: Path | None = None,
    *,
    uv_path: Path | None = None,
    project: Path | None = None,
    make_icon: bool = True,
) -> Path:
    """Build apply-spy.app (default: ~/Applications) and return its path.

    Running it again replaces the app, but only if it is the one we made; it never
    overwrites some other app that happens to have the same name.
    """
    uv = uv_path or (Path(found) if (found := shutil.which("uv")) else None)
    if uv is None or not uv.is_file():
        raise LauncherError("Cannot find the 'uv' program. Install uv, then try again.")
    project = project or _project_root()
    if project is None:
        raise LauncherError("The icon needs apply-spy to be run from its project folder.")
    dest = dest_dir or Path.home() / "Applications"
    app = dest / f"{APP_NAME}.app"
    if app.exists() and not _is_ours(app):
        raise LauncherError(f"{app} exists and was not made by apply-spy; not touching it.")

    dest.mkdir(parents=True, exist_ok=True)
    building = Path(tempfile.mkdtemp(prefix=".apply-spy-building-", dir=dest))
    try:
        contents = building / "Contents"
        (contents / "MacOS").mkdir(parents=True)
        (contents / "Resources").mkdir()
        (contents / "Info.plist").write_bytes(_info_plist())
        script = contents / "MacOS" / APP_NAME
        script.write_text(
            launcher_script(uv, project, standard_data_dir() / LOG_FILENAME), encoding="utf-8"
        )
        script.chmod(script.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        if make_icon:
            _make_icns(contents / "Resources" / "AppIcon.icns")
        if app.exists():
            shutil.rmtree(app)
        os.rename(building, app)
    finally:
        shutil.rmtree(building, ignore_errors=True)
    return app
