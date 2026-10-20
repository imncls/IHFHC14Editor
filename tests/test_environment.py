import ctypes
import sqlite3


def test_interpreter_is_32_bit() -> None:
    assert ctypes.sizeof(ctypes.c_void_p) == 4, "The game DLL needs 32-bit Python"


def test_standard_sqlite3_works_in_memory() -> None:
    with sqlite3.connect(":memory:") as connection:
        assert connection.execute("SELECT 1").fetchone() == (1,)


def test_tkinter_is_available() -> None:
    import tkinter

    assert tkinter.TkVersion >= 8.6
