from pathlib import Path

import pytest

from app import steam


def make_library(
    path: Path, install_dir: str | None = "IHF Handball Challenge 14"
) -> Path:
    """A Steam library; with the game when install_dir is given."""
    steamapps = path / "steamapps"
    steamapps.mkdir(parents=True, exist_ok=True)
    if install_dir is not None:
        game = steamapps / "common" / install_dir
        game.mkdir(parents=True)
        (game / steam.DLL_NAME).write_bytes(b"")
        (steamapps / f"appmanifest_{steam.APP_ID}.acf").write_text(
            f'"AppState"\n{{\n\t"appid"\t\t"{steam.APP_ID}"\n'
            f'\t"installdir"\t\t"{install_dir}"\n}}\n',
            encoding="utf-8",
        )
        return game
    return path


def library_list(steam_dir: Path, *libraries: Path) -> None:
    # Steam writes backslashes doubled, as in a C string.
    escaped = [str(library).replace("\\", "\\\\") for library in libraries]
    entries = "".join(
        f'\t"{n}"\n\t{{\n\t\t"path"\t\t"{path}"\n\t}}\n'
        for n, path in enumerate(escaped)
    )
    (steam_dir / "steamapps" / "libraryfolders.vdf").write_text(
        f'"libraryfolders"\n{{\n{entries}}}\n', encoding="utf-8"
    )


@pytest.fixture(autouse=True)
def no_default_steam(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Never look at the real Steam of the machine running the tests."""
    monkeypatch.setattr(steam, "DEFAULT_STEAM_DIR", tmp_path / "no default steam")


def test_game_in_the_steam_folder(tmp_path: Path) -> None:
    game = make_library(tmp_path / "Steam")
    assert steam.find_game_dir(lambda: tmp_path / "Steam") == game


def test_game_in_another_library(tmp_path: Path) -> None:
    steam_dir = make_library(tmp_path / "Steam", install_dir=None)
    other = tmp_path / "D" / "SteamLibrary"
    game = make_library(other)
    library_list(steam_dir, steam_dir, other)
    assert steam.find_game_dir(lambda: steam_dir) == game


def test_default_steam_folder_when_the_registry_has_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    game = make_library(tmp_path / "Program Files" / "Steam")
    monkeypatch.setattr(
        steam, "DEFAULT_STEAM_DIR", tmp_path / "Program Files" / "Steam"
    )
    assert steam.find_game_dir(lambda: None) == game


@pytest.mark.parametrize(
    "install_dir", ["..", "../../Windows", "C:\\Windows", "a\\..\\..\\b"]
)
def test_manifest_cannot_point_outside_the_library(
    tmp_path: Path, install_dir: str
) -> None:
    steam_dir = make_library(tmp_path / "Steam", install_dir=None)
    (steam_dir / "steamapps" / f"appmanifest_{steam.APP_ID}.acf").write_text(
        f'"AppState"\n{{\n\t"installdir"\t\t"{install_dir}"\n}}\n', encoding="utf-8"
    )
    assert steam.find_game_dir(lambda: steam_dir) is None


def test_folder_without_the_dll_is_skipped(tmp_path: Path) -> None:
    game = make_library(tmp_path / "Steam")
    (game / steam.DLL_NAME).unlink()
    assert steam.find_game_dir(lambda: tmp_path / "Steam") is None


def test_damaged_or_huge_library_list(tmp_path: Path) -> None:
    steam_dir = make_library(tmp_path / "Steam", install_dir=None)
    vdf = steam_dir / "steamapps" / "libraryfolders.vdf"
    vdf.write_bytes(b"\xff\xfe not vdf \x00")
    assert steam.find_game_dir(lambda: steam_dir) is None
    vdf.write_bytes(b" " * (steam.MAX_STEAM_FILE_BYTES + 1))
    assert steam.find_game_dir(lambda: steam_dir) is None


def test_no_steam_at_all(tmp_path: Path) -> None:
    assert steam.find_game_dir(lambda: None) is None
    assert steam.find_game_dir(lambda: tmp_path / "missing") is None
