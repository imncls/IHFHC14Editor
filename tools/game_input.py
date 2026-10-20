"""Keyboard input for the game window, for menu navigation (64-bit Python).

The game reads input through DirectInput, so keys are injected with
SendInput as hardware scan codes. Keys are only sent while the foreground
window belongs to the game, and only from an allowlist.
"""

import ctypes
import time
from ctypes import wintypes

from app.workspace import GAME_EXE
from tools.game_control import GameControlError, wait_until

INPUT_KEYBOARD = 1
KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_SCANCODE = 0x0008
MAPVK_VK_TO_VSC = 0
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
SW_RESTORE = 9
KEY_HOLD = 0.06
KEY_GAP = 0.25
MAX_TIMES = 20
WINDOW_WAIT = 5

# Menu navigation keys: directions, confirm, back and secondary buttons.
KEYS = {
    "up": 0x26,
    "down": 0x28,
    "left": 0x25,
    "right": 0x27,
    "enter": 0x0D,
    "escape": 0x1B,
    "space": 0x20,
    "backspace": 0x08,
    "tab": 0x09,
}
EXTENDED_KEYS = {"up", "down", "left", "right"}


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", wintypes.DWORD),
        ("wParamL", wintypes.WORD),
        ("wParamH", wintypes.WORD),
    ]


class _InputUnion(ctypes.Union):
    # MOUSEINPUT is the largest member and sets the size Windows expects.
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("union", _InputUnion)]


_user32 = ctypes.WinDLL("user32", use_last_error=True)
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_ENUM_WINDOWS_PROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)


def _declare(function, argtypes: list, restype) -> None:
    function.argtypes = argtypes
    function.restype = restype


_declare(_user32.EnumWindows, [_ENUM_WINDOWS_PROC, wintypes.LPARAM], wintypes.BOOL)
_declare(
    _user32.GetWindowThreadProcessId,
    [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)],
    wintypes.DWORD,
)
_declare(_user32.IsWindowVisible, [wintypes.HWND], wintypes.BOOL)
_declare(_user32.IsIconic, [wintypes.HWND], wintypes.BOOL)
_declare(_user32.ShowWindow, [wintypes.HWND, ctypes.c_int], wintypes.BOOL)
_declare(_user32.BringWindowToTop, [wintypes.HWND], wintypes.BOOL)
_declare(_user32.SetForegroundWindow, [wintypes.HWND], wintypes.BOOL)
_declare(_user32.GetForegroundWindow, [], wintypes.HWND)
_declare(
    _user32.AttachThreadInput,
    [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL],
    wintypes.BOOL,
)
_declare(_user32.MapVirtualKeyW, [wintypes.UINT, wintypes.UINT], wintypes.UINT)
_declare(
    _user32.SendInput,
    [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int],
    wintypes.UINT,
)
_declare(_kernel32.GetCurrentThreadId, [], wintypes.DWORD)
_declare(
    _kernel32.OpenProcess,
    [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD],
    wintypes.HANDLE,
)
_declare(
    _kernel32.QueryFullProcessImageNameW,
    [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)],
    wintypes.BOOL,
)
_declare(_kernel32.CloseHandle, [wintypes.HANDLE], wintypes.BOOL)


def _process_id(window: int) -> int:
    process_id = wintypes.DWORD()
    _user32.GetWindowThreadProcessId(window, ctypes.byref(process_id))
    return process_id.value


def _process_name(process_id: int) -> str | None:
    handle = _kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, process_id)
    if not handle:
        return None
    try:
        size = wintypes.DWORD(1024)
        buffer = ctypes.create_unicode_buffer(size.value)
        if not _kernel32.QueryFullProcessImageNameW(
            handle, 0, buffer, ctypes.byref(size)
        ):
            return None
        return buffer.value.rsplit("\\", 1)[-1]
    finally:
        _kernel32.CloseHandle(handle)


def is_game_window(window: int | None) -> bool:
    if not window:
        return False
    name = _process_name(_process_id(window))
    return name is not None and name.casefold() == GAME_EXE.casefold()


def game_window() -> int | None:
    """The visible top-level window of HC14.exe, if any."""
    found: list[int] = []

    def check(window, _parameter) -> bool:
        if _user32.IsWindowVisible(window) and is_game_window(window):
            found.append(window)
            return False
        return True

    _user32.EnumWindows(_ENUM_WINDOWS_PROC(check), 0)
    return found[0] if found else None


def foreground_window() -> int | None:
    return _user32.GetForegroundWindow()


def focus_game() -> int:
    """Bring the game window to the front and confirm it is the foreground."""
    # The window disappears for a moment between the intro videos.
    wait_until(lambda: game_window() is not None, WINDOW_WAIT, 0.5)
    window = game_window()
    if window is None:
        raise GameControlError("The game window was not found.")
    if is_game_window(foreground_window()):
        return window
    if _user32.IsIconic(window):
        _user32.ShowWindow(window, SW_RESTORE)
    # Windows only lets the foreground thread change the foreground window;
    # attaching to its input queue allows it without sending any key.
    foreground_thread = _user32.GetWindowThreadProcessId(foreground_window(), None)
    own_thread = _kernel32.GetCurrentThreadId()
    attached = foreground_thread and _user32.AttachThreadInput(
        own_thread, foreground_thread, True
    )
    try:
        _user32.BringWindowToTop(window)
        _user32.SetForegroundWindow(window)
    finally:
        if attached:
            _user32.AttachThreadInput(own_thread, foreground_thread, False)
    if not wait_until(lambda: is_game_window(foreground_window()), 2, 0.1):
        raise GameControlError(
            "The game could not be brought to the front. Click its window first."
        )
    return window


def key_events(key: str) -> tuple[INPUT, INPUT]:
    """Down and up events for an allowed key, as hardware scan codes."""
    if key not in KEYS:
        allowed = ", ".join(sorted(KEYS))
        raise GameControlError(f"Unknown key. Allowed keys: {allowed}.")
    scan_code = _user32.MapVirtualKeyW(KEYS[key], MAPVK_VK_TO_VSC)
    flags = KEYEVENTF_SCANCODE
    if key in EXTENDED_KEYS:
        flags |= KEYEVENTF_EXTENDEDKEY
    return (
        _keyboard_input(scan_code, flags),
        _keyboard_input(scan_code, flags | KEYEVENTF_KEYUP),
    )


def _keyboard_input(scan_code: int, flags: int) -> INPUT:
    event = INPUT(type=INPUT_KEYBOARD)
    event.union.ki = KEYBDINPUT(wVk=0, wScan=scan_code, dwFlags=flags)
    return event


def send(event: INPUT) -> None:
    if _user32.SendInput(1, ctypes.byref(event), ctypes.sizeof(INPUT)) != 1:
        raise GameControlError("Windows refused the key input.")


def press_key(key: str, times: int = 1) -> str:
    down, up = key_events(key)
    if not 1 <= times <= MAX_TIMES:
        raise GameControlError(f"times must be between 1 and {MAX_TIMES}.")
    focus_game()
    for _ in range(times):
        # Checked before every key, so input never reaches another program.
        if not is_game_window(foreground_window()):
            raise GameControlError("The game is not in the foreground; key not sent.")
        send(down)
        time.sleep(KEY_HOLD)
        send(up)
        time.sleep(KEY_GAP)
    return f"Pressed {key} {times} time(s)."
