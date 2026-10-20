"""Game process control and editor calls for the MCP server (64-bit Python).

Never loads the game DLL: everything that needs it runs through the editor's
own command line on the 32-bit Python (`python32 -m app ...`).
"""

import base64
import os
import re
import subprocess
import time
from collections.abc import Callable
from pathlib import Path

from app.config import Paths, read_settings
from app.errors import AppError
from app.workspace import GAME_EXE, is_game_running

ROOT = Path(__file__).resolve().parent.parent
SYSTEM32 = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32"
LAUNCH_TIMEOUT = 90
CLOSE_TIMEOUT = 20
EDITOR_TIMEOUT = 300
SCREENSHOT_VARIABLE = "IHFHC_SCREENSHOT_PATH"
# The editor's 32-bit Python: only the MCP side needs it.
PYTHON32_VARIABLE = "IHFHC_PYTHON32"
DEFAULT_PYTHON32 = Path(".venv") / "Scripts" / "python.exe"

# Captures the game's main window. The output path comes from an environment
# variable, so nothing is interpolated into the script.
SCREENSHOT_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Drawing
Add-Type @'
using System;
using System.Runtime.InteropServices;
public static class GameWindow {
    [StructLayout(LayoutKind.Sequential)]
    public struct Rect { public int Left, Top, Right, Bottom; }
    [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr window, out Rect rect);
    [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr window);
    [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr window, int command);
    [DllImport("user32.dll")] public static extern bool SetProcessDPIAware();
}
'@
[GameWindow]::SetProcessDPIAware() | Out-Null
$process = Get-Process -Name 'HC14' | Where-Object { $_.MainWindowHandle -ne 0 } | Select-Object -First 1
if (-not $process) { throw 'The game window was not found.' }
$window = $process.MainWindowHandle
[GameWindow]::ShowWindow($window, 9) | Out-Null
[GameWindow]::SetForegroundWindow($window) | Out-Null
Start-Sleep -Milliseconds 800
$rect = New-Object GameWindow+Rect
[GameWindow]::GetWindowRect($window, [ref]$rect) | Out-Null
$width = $rect.Right - $rect.Left
$height = $rect.Bottom - $rect.Top
if ($width -le 0 -or $height -le 0) { throw 'The game window has no size.' }
$bitmap = New-Object System.Drawing.Bitmap $width, $height
$graphics = [System.Drawing.Graphics]::FromImage($bitmap)
$graphics.CopyFromScreen($rect.Left, $rect.Top, 0, 0, $bitmap.Size)
$bitmap.Save($env:IHFHC_SCREENSHOT_PATH, [System.Drawing.Imaging.ImageFormat]::Png)
"""  # noqa: E501 - PowerShell and C# source


class GameControlError(AppError):
    pass


def steam_app_id(game_dir: Path) -> str:
    """The Steam app id, from the appmanifest next to the installation."""
    steamapps = game_dir.parent.parent
    for manifest in sorted(steamapps.glob("appmanifest_*.acf")):
        text = manifest.read_text(encoding="utf-8", errors="replace")
        install_dir = re.search(r'"installdir"\s+"([^"]*)"', text)
        app_id = re.search(r'"appid"\s+"(\d+)"', text)
        if (
            install_dir
            and app_id
            and install_dir.group(1).casefold() == game_dir.name.casefold()
        ):
            return app_id.group(1)
    raise GameControlError("The game's Steam app id was not found.")


def wait_until(
    condition: Callable[[], bool], timeout: float, interval: float = 1.0
) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(interval)
    return condition()


def launch_game(paths: Paths) -> str:
    if is_game_running():
        return "The game is already running."
    os.startfile(f"steam://rungameid/{steam_app_id(paths.game_dir)}")  # noqa: S606 - fixed Steam URL
    if wait_until(is_game_running, LAUNCH_TIMEOUT):
        return "The game is running."
    raise GameControlError(f"{GAME_EXE} did not start within {LAUNCH_TIMEOUT} s.")


def close_game(force: bool = False) -> str:
    if not is_game_running():
        return "The game is not running."
    _taskkill()
    if wait_until(lambda: not is_game_running(), CLOSE_TIMEOUT):
        return "The game was closed."
    if not force:
        return (
            f"{GAME_EXE} is still running after {CLOSE_TIMEOUT} s. Close it from "
            "the game, or call close_game with force=true."
        )
    _taskkill("/F")
    if wait_until(lambda: not is_game_running(), CLOSE_TIMEOUT):
        return "The game was force-closed."
    raise GameControlError(f"{GAME_EXE} could not be closed.")


def _taskkill(*flags: str) -> None:
    subprocess.run(  # noqa: S603 - fixed executable and arguments
        [str(SYSTEM32 / "taskkill.exe"), *flags, "/IM", GAME_EXE],
        capture_output=True,
        check=False,
    )


def screenshot(path: Path) -> Path:
    if not is_game_running():
        raise GameControlError("The game is not running.")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.unlink(missing_ok=True)
    encoded = base64.b64encode(SCREENSHOT_SCRIPT.encode("utf-16-le")).decode("ascii")
    result = subprocess.run(  # noqa: S603 - fixed executable and script
        [
            str(SYSTEM32 / "WindowsPowerShell" / "v1.0" / "powershell.exe"),
            "-NoProfile",
            "-NonInteractive",
            "-EncodedCommand",
            encoded,
        ],
        capture_output=True,
        text=True,
        errors="replace",
        timeout=60,
        check=False,
        env={**os.environ, SCREENSHOT_VARIABLE: str(path)},
    )
    if result.returncode or not path.is_file():
        raise GameControlError("The screenshot could not be taken.")
    return path


def python32_path(config_path: Path) -> Path:
    """The editor's 32-bit Python: IHFHC_PYTHON32, else python32 in
    config.local.json, else the project's .venv. Relative to the project."""
    value = os.environ.get(PYTHON32_VARIABLE) or read_settings(config_path).get(
        "python32"
    )
    python32 = Path(value) if value else DEFAULT_PYTHON32
    return python32 if python32.is_absolute() else ROOT / python32


def run_editor(python32: Path, *args: str) -> str:
    """Run `python -m app <args>` on the 32-bit Python and return its output."""
    if not python32.is_file():
        raise GameControlError(
            "The 32-bit Python was not found. Set python32 in config.local.json "
            "or the IHFHC_PYTHON32 environment variable."
        )
    result = subprocess.run(  # noqa: S603 - fixed module, arguments from the tools
        [str(python32), "-m", "app", *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=EDITOR_TIMEOUT,
        check=False,
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )
    output = (result.stdout + result.stderr).strip()
    if result.returncode:
        raise GameControlError(output.removeprefix("Error: ") or "The editor failed.")
    return output
