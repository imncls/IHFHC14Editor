import ctypes

import pytest

from tools import game_input
from tools.game_control import GameControlError


@pytest.fixture
def sent(monkeypatch: pytest.MonkeyPatch) -> list:
    events = []
    monkeypatch.setattr(game_input, "send", events.append)
    monkeypatch.setattr(game_input.time, "sleep", lambda _seconds: None)
    return events


def test_input_structure_has_the_size_windows_expects() -> None:
    expected = 28 if ctypes.sizeof(ctypes.c_void_p) == 4 else 40
    assert ctypes.sizeof(game_input.INPUT) == expected


def test_arrows_are_extended_scan_codes() -> None:
    down, up = game_input.key_events("up")
    flags = down.union.ki.dwFlags
    assert flags & game_input.KEYEVENTF_SCANCODE
    assert flags & game_input.KEYEVENTF_EXTENDEDKEY
    assert down.union.ki.wVk == 0
    assert down.union.ki.wScan == 0x48
    assert up.union.ki.dwFlags & game_input.KEYEVENTF_KEYUP


def test_enter_is_not_extended() -> None:
    down, _ = game_input.key_events("enter")
    assert not down.union.ki.dwFlags & game_input.KEYEVENTF_EXTENDEDKEY
    assert down.union.ki.wScan == 0x1C


@pytest.mark.parametrize("key", ["f4", "a", "alt", "Enter", ""])
def test_unknown_keys_are_rejected(key: str, sent: list) -> None:
    with pytest.raises(GameControlError):
        game_input.press_key(key)
    assert sent == []


@pytest.mark.parametrize("times", [0, 21, -1])
def test_times_out_of_range(times: int, sent: list) -> None:
    with pytest.raises(GameControlError):
        game_input.press_key("down", times)
    assert sent == []


def test_keys_are_sent_only_to_the_game(
    monkeypatch: pytest.MonkeyPatch, sent: list
) -> None:
    monkeypatch.setattr(game_input, "focus_game", lambda: 1)
    monkeypatch.setattr(game_input, "foreground_window", lambda: 2)
    monkeypatch.setattr(game_input, "is_game_window", lambda window: window == 1)
    with pytest.raises(GameControlError):
        game_input.press_key("down")
    assert sent == []


def test_press_key_sends_down_and_up_each_time(
    monkeypatch: pytest.MonkeyPatch, sent: list
) -> None:
    monkeypatch.setattr(game_input, "focus_game", lambda: 1)
    monkeypatch.setattr(game_input, "foreground_window", lambda: 1)
    monkeypatch.setattr(game_input, "is_game_window", lambda window: window == 1)
    assert game_input.press_key("down", 3) == "Pressed down 3 time(s)."
    flags = [event.union.ki.dwFlags & game_input.KEYEVENTF_KEYUP for event in sent]
    assert len(sent) == 6
    assert flags == [0, game_input.KEYEVENTF_KEYUP] * 3


def test_focus_fails_without_a_game_window(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(game_input, "game_window", lambda: None)
    monkeypatch.setattr(
        game_input, "wait_until", lambda condition, timeout, interval: condition()
    )
    with pytest.raises(GameControlError):
        game_input.focus_game()
