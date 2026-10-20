import subprocess
from pathlib import Path

import pytest

from tools import game_control
from tools.game_control import GameControlError


def make_steam_tree(tmp_path: Path) -> Path:
    steamapps = tmp_path / "steamapps"
    game_dir = steamapps / "common" / "IHF Handball Challenge 14"
    game_dir.mkdir(parents=True)
    (steamapps / "appmanifest_1.acf").write_text(
        '"AppState"\n{\n\t"appid"\t\t"1"\n\t"installdir"\t\t"Other Game"\n}\n'
    )
    (steamapps / "appmanifest_279460.acf").write_text(
        '"AppState"\n{\n\t"appid"\t\t"279460"\n'
        '\t"installdir"\t\t"IHF Handball Challenge 14"\n}\n'
    )
    return game_dir


def test_steam_app_id(tmp_path: Path) -> None:
    assert game_control.steam_app_id(make_steam_tree(tmp_path)) == "279460"


def test_steam_app_id_missing(tmp_path: Path) -> None:
    with pytest.raises(GameControlError):
        game_control.steam_app_id(tmp_path / "steamapps" / "common" / "Game")


def test_python32_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = tmp_path / "config.local.json"
    monkeypatch.delenv(game_control.PYTHON32_VARIABLE, raising=False)
    assert game_control.python32_path(config) == (
        game_control.ROOT / ".venv" / "Scripts" / "python.exe"
    )
    config.write_text('{"python32": "C:/Py32/python.exe"}', encoding="utf-8")
    assert game_control.python32_path(config) == Path("C:/Py32/python.exe")
    monkeypatch.setenv(game_control.PYTHON32_VARIABLE, "D:/Other/python.exe")
    assert game_control.python32_path(config) == Path("D:/Other/python.exe")


def test_run_editor_passes_arguments_as_a_list(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    python32 = tmp_path / "python.exe"
    python32.write_bytes(b"")
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, "Changes installed.\n", "")

    monkeypatch.setattr(game_control.subprocess, "run", fake_run)
    output = game_control.run_editor(python32, "restore", "--yes")
    assert output == "Changes installed."
    command, kwargs = calls[0]
    assert command == [str(python32), "-m", "app", "restore", "--yes"]
    assert "shell" not in kwargs


def test_run_editor_reports_the_editor_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    python32 = tmp_path / "python.exe"
    python32.write_bytes(b"")
    monkeypatch.setattr(
        game_control.subprocess,
        "run",
        lambda command, **kwargs: subprocess.CompletedProcess(
            command, 1, "", "Error: Close HC14.exe first.\n"
        ),
    )
    with pytest.raises(GameControlError, match="^Close HC14.exe first.$"):
        game_control.run_editor(python32, "apply")


def test_run_editor_without_python(tmp_path: Path) -> None:
    with pytest.raises(GameControlError):
        game_control.run_editor(tmp_path / "missing.exe", "apply")


@pytest.mark.parametrize(
    "force, still_running_after, expected",
    [
        (False, 0, "The game was closed."),
        (False, 99, "still running"),
        (True, 1, "The game was force-closed."),
    ],
)
def test_close_game(
    monkeypatch: pytest.MonkeyPatch, force: bool, still_running_after: int, expected
) -> None:
    kills = []
    monkeypatch.setattr(game_control, "_taskkill", lambda *flags: kills.append(flags))
    monkeypatch.setattr(
        game_control, "is_game_running", lambda: len(kills) <= still_running_after
    )
    monkeypatch.setattr(
        game_control, "wait_until", lambda condition, timeout: condition()
    )
    assert expected in game_control.close_game(force)
    assert kills[0] == ()
    assert ("/F",) in kills if force else ("/F",) not in kills
