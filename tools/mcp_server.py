"""MCP server to drive IHF Handball Challenge 14 during development (stdio).

Runs on 64-bit Python. It never loads the game DLL or reads the key: work
that needs them runs through the editor's command line on the 32-bit Python.
"""

import functools
import logging
import sys
import time
from collections.abc import Callable
from contextlib import closing
from pathlib import Path
from typing import Literal

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mcp.server.mcpserver import Image, MCPServer  # noqa: E402
from mcp.server.mcpserver.exceptions import ToolError  # noqa: E402

from app.config import Paths, load_paths  # noqa: E402
from app.database import connect, open_read_only  # noqa: E402
from app.diff import diff_databases  # noqa: E402
from app.errors import AppError  # noqa: E402
from app.logs import setup_logging  # noqa: E402
from app.portraits import PortraitStore, changed_logos, changed_portraits  # noqa: E402
from app.repository import PlayerRepository  # noqa: E402
from app.workspace import is_game_running  # noqa: E402
from tools import game_control, game_input  # noqa: E402

WORK_DIR = ROOT / "work"
WORKING_COPY = WORK_DIR / "clear.db"
INSTALLED_COPY = WORK_DIR / "installed.db"
ORIGINAL_COPY = WORK_DIR / "original.db"
SCREENSHOT = WORK_DIR / "screenshot.png"
MAX_DIFF_LINES = 300
MAX_NAME_LENGTH = 50
MAX_CAPTURE_DELAY = 10
WINDOW_TIMEOUT = 60
MAX_SCREENSHOT_DELAY = 30

logger = logging.getLogger("tools.mcp")
server = MCPServer(
    "ihfhc14",
    instructions=(
        "Controls IHF Handball Challenge 14 for the editor's development: game "
        "process, applying and restoring the player database, diffs and reads."
    ),
)


def _paths() -> Paths:
    return load_paths(ROOT / "config.local.json")


def _python32() -> Path:
    return game_control.python32_path(ROOT / "config.local.json")


def _tool[F: Callable](function: F) -> F:
    """Register a tool whose errors reach the client as safe messages."""

    @functools.wraps(function)
    def run(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except AppError as error:
            raise ToolError(str(error)) from None
        except Exception:
            logger.exception("%s failed", function.__name__)
            raise ToolError("Unexpected failure; see work/mcp.log.") from None

    return server.tool()(run)


def _require_game_closed() -> None:
    if is_game_running():
        raise game_control.GameControlError(
            "The game is running. Close it first (close_game)."
        )


@_tool
def is_running() -> str:
    """Whether the game process (HC14.exe) is running."""
    return "The game is running." if is_game_running() else "The game is not running."


@_tool
def launch_game() -> str:
    """Start the game through Steam and wait until its window is visible.

    The intro videos play first; on the profile screen, escape asks to quit.
    """
    result = game_control.launch_game(_paths())
    if not game_control.wait_until(
        lambda: game_input.game_window() is not None, WINDOW_TIMEOUT
    ):
        raise game_control.GameControlError(
            f"The game is running but its window did not appear within "
            f"{WINDOW_TIMEOUT} s."
        )
    return result


@_tool
def close_game(force: bool = False) -> str:
    """Ask the game to close and confirm the process ended.

    With force=true, kill it if it does not close by itself.
    """
    return game_control.close_game(force)


@_tool
def apply_db() -> str:
    """Encrypt the working copy (work/clear.db) and install it in the save folder.

    Refuses while the game runs. Backs up the installed file first.
    """
    _require_game_closed()
    return game_control.run_editor(_python32(), "apply")


@_tool
def restore_backup() -> str:
    """Install the original player database again (refuses while the game runs)."""
    _require_game_closed()
    return game_control.run_editor(_python32(), "restore", "--yes")


@_tool
def diff_db() -> str:
    """Compare the installed player database with the original backup."""
    game_control.run_editor(_python32(), "snapshot")
    lines = diff_databases(INSTALLED_COPY, ORIGINAL_COPY) + _portrait_diff()
    if not lines:
        return "No differences from the original."
    shown = lines[:MAX_DIFF_LINES]
    if len(lines) > MAX_DIFF_LINES:
        shown.append(f"... and {len(lines) - MAX_DIFF_LINES} more changes.")
    return "\n".join([f"{len(lines)} changes from the original:", *shown])


@_tool
def read_player(
    player_id: int | None = None,
    name: str | None = None,
    source: Literal["installed", "working"] = "installed",
) -> str:
    """Show every field of a player, by id or by searching the name.

    source: "installed" (what the game reads now) or "working" (work/clear.db).
    """
    if player_id is None and not name:
        raise game_control.GameControlError("Give a player_id or a name.")
    if name and len(name) > MAX_NAME_LENGTH:
        raise game_control.GameControlError("The name is too long.")
    if source == "installed":
        game_control.run_editor(_python32(), "snapshot")
        database = INSTALLED_COPY
    else:
        database = _working_copy()
    with closing(open_read_only(database)) as connection:
        repository = PlayerRepository(connection)
        if player_id is None:
            found = repository.find_players(name or "")
            if not found:
                return "No player matches that name."
            if len(found) > 1:
                return "Several players match:\n" + "\n".join(
                    f"{player.id}: {player.first_name} {player.name}"
                    for player in found
                )
            player_id = found[0].id
        player = repository.get_player(player_id)
    lines = [f"player {player_id} ({source})"]
    lines += [f"{key}: {value}" for key, value in player.items()]
    return "\n".join(lines)


@_tool
def set_player_field(player_id: int, key: str, value: str) -> str:
    """Change one field of a player in the working copy (work/clear.db).

    Same validation and average recalculation as the editor. Use apply_db to
    install it in the game. Keys are playerDBData keys (name, first_name,
    jersey_nr, ...), skills (sk_*) or overall (goalkeepers).
    """
    with closing(connect(_working_copy())) as connection:
        written = PlayerRepository(connection).set_values(player_id, {key: value})
    return f"player {player_id}: {key} = {written[key]!r} in the working copy."


@_tool
def screenshot(delay: float = 0) -> Image:
    """Capture the game window as a PNG image, after `delay` seconds (0 to 30).

    The delay helps to wait for loading screens and intro videos.
    """
    if not 0 <= delay <= MAX_SCREENSHOT_DELAY:
        raise game_control.GameControlError(
            f"delay must be between 0 and {MAX_SCREENSHOT_DELAY} seconds."
        )
    time.sleep(delay)
    return Image(path=game_control.screenshot(SCREENSHOT))


@_tool
def press_key(
    key: str, times: int = 1, capture_after: float | None = 0.5
) -> Image | str:
    """Press a key in the game to navigate its menus, then capture the screen.

    key: up, down, left, right, enter (confirm), escape (back), space,
    backspace or tab. times: 1 to 20. capture_after: seconds to wait before
    the screenshot (0 to 10), or null for no screenshot. Keys are only sent
    while the game window is in the foreground.
    """
    if capture_after is not None and not 0 <= capture_after <= MAX_CAPTURE_DELAY:
        raise game_control.GameControlError(
            f"capture_after must be between 0 and {MAX_CAPTURE_DELAY} seconds."
        )
    result = game_input.press_key(key, times)
    if capture_after is None:
        return result
    time.sleep(capture_after)
    return Image(path=game_control.screenshot(SCREENSHOT))


def _portrait_diff() -> list[str]:
    paths = _paths()
    store = PortraitStore(paths.portraits_path, ROOT)
    if not store.has_original():
        return []
    archive, original = paths.portraits_path.read_bytes(), store.original_bytes()
    installed = changed_portraits(archive, original)
    lines = [f"player {pid}: portrait differs from the original" for pid in installed]
    lines += [
        f"player {pid}: custom portrait in the working copy, not installed yet"
        for pid in store.custom_ids()
        if pid not in installed
    ]
    logos = changed_logos(archive, original)
    lines += [f"team {tid}: custom logo installed" for tid in logos]
    lines += [
        f"team {tid}: custom logo in the working copy, not installed yet"
        for tid in store.custom_logo_ids()
        if tid not in logos
    ]
    return lines


def _working_copy() -> Path:
    if not WORKING_COPY.is_file():
        game_control.run_editor(_python32(), "start")
    return WORKING_COPY


if __name__ == "__main__":
    setup_logging(WORK_DIR, "mcp.log")
    server.run("stdio")
