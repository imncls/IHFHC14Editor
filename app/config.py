"""Paths and key: environment variables, then config.local.json, then defaults."""

import ctypes
import json
import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from app import steam
from app.errors import ConfigError, FolderError

activity = logging.getLogger("app.activity")

DLL_NAME = "SQLiteEncrypt.dll"
DUMP_NAME = "handball.challenge.metadata.dump.v13.0.0"
PORTRAITS_ARCHIVE = "guiportraits.v"
# Holds XMLData/CharacterCustomizationConfig.xml (the valid 3D looks).
GFX_ARCHIVE = "gfx.v"
SAVE_SUBDIR = Path("neutron games") / "HandballChallenge14" / "save"
ENVIRONMENT = {
    "game_dir": "IHFHC_GAME_DIR",
    "save_dir": "IHFHC_SAVE_DIR",
}
FOLDER_KEYS = tuple(ENVIRONMENT)
# FOLDERID_Documents
_DOCUMENTS_FOLDER = "{FDD39AD0-238F-46AF-ADB4-6C85480369C7}"


@dataclass(frozen=True)
class Paths:
    game_dir: Path
    save_dir: Path

    @property
    def dll_path(self) -> Path:
        return self.game_dir / DLL_NAME

    @property
    def dump_path(self) -> Path:
        return self.save_dir / DUMP_NAME

    @property
    def portraits_path(self) -> Path:
        return self.game_dir / PORTRAITS_ARCHIVE

    @property
    def gfx_path(self) -> Path:
        return self.game_dir / GFX_ARCHIVE


@dataclass(frozen=True)
class Config(Paths):
    """The folders and the key."""

    key: bytes = field(repr=False, kw_only=True)


def read_settings(path: Path) -> dict[str, str]:
    """Values from config.local.json, overridden by IHFHC_* variables."""
    settings: dict[str, str] = {}
    raw = _read_json(path)
    for key in (*FOLDER_KEYS, "key_hex"):
        value = raw.get(key)
        if value is not None and not isinstance(value, str):
            raise ConfigError(f"{key} in config.local.json must be text.")
    settings.update(
        (key, value) for key, value in raw.items() if isinstance(value, str)
    )
    for key, variable in ENVIRONMENT.items():
        if os.environ.get(variable):
            settings[key] = os.environ[variable]
    return settings


def load_paths(path: Path) -> Paths:
    """The game and save folders: as set (config.local.json, IHFHC_*), else
    found (Steam libraries, the Documents folder). Never reads the key.
    A FolderError says which folder is missing or wrong."""
    settings = read_settings(path)
    game_dir = settings.get("game_dir") or steam.find_game_dir()
    if game_dir is None:
        raise FolderError(
            "The game was not found in the Steam libraries. Choose its folder.",
            "game_dir",
        )
    save_dir = settings.get("save_dir") or default_save_dir()
    return check_folders(Path(game_dir), Path(save_dir))


def check_folders(game_dir: Path, save_dir: Path) -> Paths:
    """Both folders, resolved, if they hold what the editor needs."""
    return Paths(game_dir=check_game_dir(game_dir), save_dir=check_save_dir(save_dir))


def check_game_dir(game_dir: Path) -> Path:
    """The game's installation folder, resolved: it holds the game's DLL."""
    game = _existing_dir(str(game_dir), "game_dir", "The game folder")
    if not (game / DLL_NAME).is_file():
        raise FolderError(
            f"The game folder has no {DLL_NAME}: it is not the game's folder.",
            "game_dir",
        )
    return game


def check_save_dir(save_dir: Path) -> Path:
    """The game's save folder, resolved: it holds the game's data."""
    save = _existing_dir(str(save_dir), "save_dir", "The save folder")
    if not (save / DUMP_NAME).is_file():
        raise FolderError(
            "The save folder has no game data: start the game once, or choose "
            "the folder that holds it.",
            "save_dir",
        )
    return save


def detected_folders() -> tuple[Path | None, Path]:
    """Where the game and its save data are when nothing is set."""
    return steam.find_game_dir(), default_save_dir()


def save_settings(config_path: Path, game_dir: Path, save_dir: Path) -> Paths:
    """Check both folders and store them in config.local.json, keeping the
    other settings (such as the key)."""
    paths = check_folders(game_dir, save_dir)
    settings = _read_json(config_path)
    settings["game_dir"] = str(paths.game_dir)
    settings["save_dir"] = str(paths.save_dir)
    _write_json(config_path, settings)
    activity.info(
        "settings: game folder %s, save folder %s", paths.game_dir, paths.save_dir
    )
    return paths


def load_config(path: Path) -> Config:
    paths = load_paths(path)
    key = get_key(path, paths)
    return Config(game_dir=paths.game_dir, save_dir=paths.save_dir, key=key)


def get_key(config_path: Path, paths: Paths) -> bytes:
    """The single place the encryption key comes from.

    No key is shipped or typed: the first time, it is found in the installed
    HC14.exe and saved in config.local.json (git-ignored). A saved key that no
    longer opens the data, for example after a game update, is searched again.
    """
    from app import keyfinder  # needs the game's 32-bit DLL

    saved = _saved_key(read_settings(config_path))
    if saved is not None and keyfinder.key_opens(paths, saved):
        return saved
    key = keyfinder.extract_key(paths)
    save_key(config_path, key)
    activity.info("key found in HC14.exe and saved in %s", config_path.name)
    return key


def save_key(config_path: Path, key: bytes) -> None:
    """Store the key in config.local.json, keeping the other settings."""
    settings = _read_json(config_path)
    settings["key_hex"] = key.hex(" ").upper()
    _write_json(config_path, settings)


def _read_json(path: Path) -> dict[str, object]:
    """config.local.json as a dict ({} when there is none)."""
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        raise ConfigError("config.local.json cannot be read as JSON.") from None
    if not isinstance(raw, dict):
        raise ConfigError("config.local.json must contain a JSON object.")
    return raw


def _write_json(path: Path, settings: dict[str, object]) -> None:
    """Write the whole file, or leave the old one if writing fails."""
    staging = path.with_name(path.name + ".tmp")
    try:
        staging.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
        os.replace(staging, path)
    except OSError:
        staging.unlink(missing_ok=True)
        raise ConfigError("config.local.json cannot be written.") from None


def _saved_key(settings: Mapping[str, str]) -> bytes | None:
    """The saved key, or None when missing or malformed (search again)."""
    try:
        key = bytes.fromhex(settings.get("key_hex", ""))
    except ValueError:
        return None
    return key or None


def default_save_dir() -> Path:
    return documents_dir() / SAVE_SUBDIR


def documents_dir() -> Path:
    """The user's Documents folder, also when it is redirected (OneDrive)."""
    path = ctypes.c_wchar_p()
    try:
        folder = _Guid(_DOCUMENTS_FOLDER)
        shell32 = ctypes.windll.shell32
        shell32.SHGetKnownFolderPath.argtypes = [
            ctypes.POINTER(_Guid),
            ctypes.c_uint32,
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_wchar_p),
        ]
        shell32.SHGetKnownFolderPath.restype = ctypes.c_long
        if shell32.SHGetKnownFolderPath(
            ctypes.byref(folder), 0, None, ctypes.byref(path)
        ):
            return Path.home() / "Documents"
        return Path(path.value)
    except (AttributeError, OSError):
        return Path.home() / "Documents"
    finally:
        if path.value is not None:
            ctypes.windll.ole32.CoTaskMemFree(path)


class _Guid(ctypes.Structure):
    _fields_ = [
        ("data1", ctypes.c_uint32),
        ("data2", ctypes.c_uint16),
        ("data3", ctypes.c_uint16),
        ("data4", ctypes.c_ubyte * 8),
    ]

    def __init__(self, text: str) -> None:
        parts = text.strip("{}").split("-")
        tail = bytes.fromhex(parts[3] + parts[4])
        super().__init__(
            int(parts[0], 16),
            int(parts[1], 16),
            int(parts[2], 16),
            (ctypes.c_ubyte * 8)(*tail),
        )


def _existing_dir(value: str, field_name: str, label: str) -> Path:
    path = Path(value.strip()).resolve()
    if not value.strip() or not path.is_dir():
        raise FolderError(f"{label} does not exist or is not a folder.", field_name)
    return path
