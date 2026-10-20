import json
from pathlib import Path

import pytest

from app import config
from app.config import (
    DLL_NAME,
    DUMP_NAME,
    ENVIRONMENT,
    Paths,
    check_folders,
    documents_dir,
    get_key,
    load_config,
    load_paths,
    save_settings,
)
from app.errors import ConfigError, FolderError

SECRET_HEX = "deadbeef" * 4


@pytest.fixture
def folders(tmp_path: Path) -> tuple[Path, Path]:
    game_dir = tmp_path / "game"
    save_dir = tmp_path / "save"
    game_dir.mkdir()
    save_dir.mkdir()
    (game_dir / DLL_NAME).write_bytes(b"")
    (save_dir / DUMP_NAME).write_bytes(b"")
    return game_dir, save_dir


def write_config(tmp_path: Path, **values: str) -> Path:
    path = tmp_path / "config.local.json"
    path.write_text(json.dumps(values), encoding="utf-8")
    return path


class FakeFinder:
    """Stands in for the search in HC14.exe and the DLL check."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, key: bytes) -> None:
        self.key = key
        self.searches = 0
        monkeypatch.setattr("app.keyfinder.extract_key", self.extract)
        monkeypatch.setattr("app.keyfinder.key_opens", self.opens)

    def extract(self, _paths) -> bytes:
        self.searches += 1
        return self.key

    def opens(self, _paths, key: bytes) -> bool:
        return key == self.key


def saved(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_first_start_finds_and_saves_the_key(
    tmp_path: Path, folders: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    game_dir, save_dir = folders
    path = write_config(tmp_path, game_dir=str(game_dir), save_dir=str(save_dir))
    finder = FakeFinder(monkeypatch, bytes.fromhex("5a12a7"))
    config = load_config(path)
    assert config.key == bytes.fromhex("5a12a7")
    assert config.dump_path == save_dir / DUMP_NAME
    assert "key=" not in repr(config)
    assert saved(path) == {
        "game_dir": str(game_dir),
        "save_dir": str(save_dir),
        "key_hex": "5A 12 A7",
    }
    # Later starts use the saved key without searching.
    assert load_config(path).key == bytes.fromhex("5a12a7")
    assert finder.searches == 1


def test_no_config_file_is_created_with_the_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = Paths(game_dir=tmp_path, save_dir=tmp_path)
    path = tmp_path / "config.local.json"
    FakeFinder(monkeypatch, bytes.fromhex("aabb"))
    assert get_key(path, paths) == bytes.fromhex("aabb")
    assert saved(path) == {"key_hex": "AA BB"}


@pytest.mark.parametrize("stored", ["11 22", "", "   ", SECRET_HEX + "zz", "abc"])
def test_wrong_or_malformed_saved_key_is_searched_again(
    tmp_path: Path,
    folders: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    stored: str,
) -> None:
    game_dir, save_dir = folders
    path = write_config(
        tmp_path, game_dir=str(game_dir), save_dir=str(save_dir), key_hex=stored
    )
    finder = FakeFinder(monkeypatch, bytes.fromhex("ccdd"))
    assert load_config(path).key == bytes.fromhex("ccdd")
    assert finder.searches == 1
    assert saved(path)["key_hex"] == "CC DD"


def test_the_key_is_not_logged(
    tmp_path: Path,
    folders: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    game_dir, save_dir = folders
    path = write_config(tmp_path, game_dir=str(game_dir), save_dir=str(save_dir))
    FakeFinder(monkeypatch, bytes.fromhex(SECRET_HEX))
    with caplog.at_level("DEBUG"):
        load_config(path)
    assert caplog.messages == ["key found in HC14.exe and saved in config.local.json"]
    assert SECRET_HEX.upper() not in caplog.text.upper().replace(" ", "")


def test_missing_file_uses_defaults(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Point the defaults nowhere, so the real game is never searched here.
    monkeypatch.setenv("IHFHC_GAME_DIR", str(tmp_path / "no game"))
    with pytest.raises(FolderError) as error:
        load_config(tmp_path / "config.local.json")
    assert error.value.field == "game_dir"
    assert not (tmp_path / "config.local.json").exists()


def test_invalid_json(tmp_path: Path) -> None:
    path = tmp_path / "config.local.json"
    path.write_text('{"game_dir": "C:\\a"}', encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(path)


def test_game_dir_without_dll(tmp_path: Path, folders: tuple[Path, Path]) -> None:
    game_dir, save_dir = folders
    (game_dir / DLL_NAME).unlink()
    path = write_config(tmp_path, game_dir=str(game_dir), save_dir=str(save_dir))
    with pytest.raises(ConfigError):
        load_config(path)


@pytest.fixture(autouse=True)
def no_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for variable in ENVIRONMENT.values():
        monkeypatch.delenv(variable, raising=False)


def test_environment_overrides_the_file(
    tmp_path: Path, folders: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    game_dir, save_dir = folders
    path = write_config(tmp_path, game_dir="C:/nowhere")
    monkeypatch.setenv("IHFHC_GAME_DIR", str(game_dir))
    monkeypatch.setenv("IHFHC_SAVE_DIR", str(save_dir))
    assert load_paths(path).game_dir == game_dir
    assert "key_hex" not in ENVIRONMENT  # the key never comes from the environment


def test_paths_do_not_need_the_key(tmp_path: Path, folders: tuple[Path, Path]) -> None:
    game_dir, save_dir = folders
    path = write_config(tmp_path, game_dir=str(game_dir), save_dir=str(save_dir))
    paths = load_paths(path)
    assert paths.dump_path == save_dir / DUMP_NAME
    assert not hasattr(paths, "python32")  # only the MCP server needs it
    assert "key_hex" not in saved(path)


def test_documents_dir_exists() -> None:
    assert documents_dir().is_dir()


def test_game_folder_found_in_steam(
    tmp_path: Path, folders: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    game_dir, save_dir = folders
    monkeypatch.delenv("IHFHC_GAME_DIR", raising=False)
    monkeypatch.setattr(config.steam, "find_game_dir", lambda: game_dir)
    path = write_config(tmp_path, save_dir=str(save_dir))
    assert load_paths(path).game_dir == game_dir.resolve()
    # A folder set by hand wins over the one found.
    other = tmp_path / "other"
    other.mkdir()
    (other / DLL_NAME).write_bytes(b"")
    path = write_config(tmp_path, game_dir=str(other), save_dir=str(save_dir))
    assert load_paths(path).game_dir == other.resolve()


def test_game_not_found_anywhere(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("IHFHC_GAME_DIR", raising=False)
    monkeypatch.setattr(config.steam, "find_game_dir", lambda: None)
    with pytest.raises(FolderError) as error:
        load_paths(tmp_path / "config.local.json")
    assert error.value.field == "game_dir"


@pytest.mark.parametrize(
    ("broken", "field"),
    [("dll", "game_dir"), ("dump", "save_dir"), ("game", "game_dir")],
)
def test_check_folders_names_the_wrong_one(
    tmp_path: Path, folders: tuple[Path, Path], broken: str, field: str
) -> None:
    game_dir, save_dir = folders
    if broken == "dll":
        (game_dir / DLL_NAME).unlink()
    elif broken == "dump":
        (save_dir / DUMP_NAME).unlink()
    else:
        game_dir = tmp_path / "nowhere"
    with pytest.raises(FolderError) as error:
        check_folders(game_dir, save_dir)
    assert error.value.field == field


def test_save_settings_keeps_the_key(
    tmp_path: Path, folders: tuple[Path, Path]
) -> None:
    game_dir, save_dir = folders
    path = write_config(tmp_path, key_hex=SECRET_HEX, python32="C:/Py32/python.exe")
    save_settings(path, game_dir, save_dir)
    stored = saved(path)
    assert stored["game_dir"] == str(game_dir.resolve())
    assert stored["save_dir"] == str(save_dir.resolve())
    assert stored["key_hex"] == SECRET_HEX
    assert stored["python32"] == "C:/Py32/python.exe"
    assert not path.with_name(path.name + ".tmp").exists()


def test_save_settings_refuses_bad_folders(
    tmp_path: Path, folders: tuple[Path, Path]
) -> None:
    game_dir, save_dir = folders
    path = write_config(tmp_path, key_hex=SECRET_HEX)
    before = path.read_text(encoding="utf-8")
    with pytest.raises(FolderError):
        save_settings(path, tmp_path / "nowhere", save_dir)
    assert path.read_text(encoding="utf-8") == before


def test_folder_values_must_be_text(tmp_path: Path) -> None:
    path = tmp_path / "config.local.json"
    path.write_text('{"game_dir": 5}', encoding="utf-8")
    with pytest.raises(ConfigError, match="game_dir"):
        load_paths(path)
