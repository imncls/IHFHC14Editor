"""Find the game's folder in the user's Steam libraries.

Read only, standard library only: Steam's folder from the registry, its
libraries from steamapps/libraryfolders.vdf, and the game's folder from its
app manifest in each library. Steam's files are untrusted input: their size
is capped and only the values needed are read.
"""

import logging
import re
from collections.abc import Callable, Iterable
from pathlib import Path

logger = logging.getLogger(__name__)

APP_ID = "279460"  # IHF Handball Challenge 14 on Steam
DLL_NAME = "SQLiteEncrypt.dll"  # what makes a folder the game's
DEFAULT_STEAM_DIR = Path(r"C:\Program Files (x86)\Steam")
MAX_STEAM_FILE_BYTES = 1024 * 1024
_PATH = re.compile(r'"path"\s+"((?:[^"\\]|\\.)*)"', re.IGNORECASE)
_INSTALL_DIR = re.compile(r'"installdir"\s+"((?:[^"\\]|\\.)*)"', re.IGNORECASE)
_ESCAPE = re.compile(r"\\(.)")


def find_game_dir(
    steam_dir: Callable[[], Path | None] | None = None,
) -> Path | None:
    """The game's installation folder, or None when Steam does not have it."""
    steam = (steam_dir or registry_steam_dir)()
    roots = [root for root in (steam, DEFAULT_STEAM_DIR) if root is not None]
    for library in _libraries(roots):
        game_dir = _game_in(library)
        if game_dir is not None:
            return game_dir
    return None


def registry_steam_dir() -> Path | None:
    """Steam's folder as Steam itself records it for this user."""
    try:
        import winreg
    except ImportError:  # not Windows
        return None
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as key:
            value, kind = winreg.QueryValueEx(key, "SteamPath")
    except OSError:
        return None
    if kind != winreg.REG_SZ or not isinstance(value, str) or not value.strip():
        return None
    return Path(value.strip())


def _libraries(roots: Iterable[Path]) -> list[Path]:
    """The Steam folders and every library their libraryfolders.vdf lists,
    once each, in order."""
    found: list[Path] = []
    for root in roots:
        candidates = [root]
        text = _read_small(root / "steamapps" / "libraryfolders.vdf")
        if text is not None:
            candidates += [Path(_unescape(path)) for path in _PATH.findall(text)]
        for library in candidates:
            if library not in found:
                found.append(library)
    return found


def _game_in(library: Path) -> Path | None:
    text = _read_small(library / "steamapps" / f"appmanifest_{APP_ID}.acf")
    if text is None:
        return None
    match = _INSTALL_DIR.search(text)
    if match is None:
        return None
    install_dir = _unescape(match.group(1))
    # A folder name only: never a path that leaves steamapps/common.
    if (
        not install_dir
        or Path(install_dir).name != install_dir
        or install_dir in {".", ".."}
    ):
        return None
    game_dir = library / "steamapps" / "common" / install_dir
    return game_dir if (game_dir / DLL_NAME).is_file() else None


def _read_small(path: Path) -> str | None:
    try:
        if not path.is_file() or path.stat().st_size > MAX_STEAM_FILE_BYTES:
            return None
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError as error:
        logger.warning("Steam file unreadable: %s", error)
        return None


def _unescape(value: str) -> str:
    return _ESCAPE.sub(r"\1", value)
