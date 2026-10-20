"""The only module that loads the game's SQLiteEncrypt.dll."""

import ctypes
import shutil
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Protocol

from app.errors import CryptoError, WrongKeyError

SQLITE_OK = 0
PLAIN_HEADER = b"SQLite format 3\x00"

_ROW_CALLBACK = ctypes.CFUNCTYPE(
    ctypes.c_int,
    ctypes.c_void_p,
    ctypes.c_int,
    ctypes.POINTER(ctypes.c_char_p),
    ctypes.POINTER(ctypes.c_char_p),
)


class Crypto(Protocol):
    def decrypt(self, source: Path, target: Path) -> None: ...

    def encrypt(self, source: Path, target: Path) -> None: ...

    def verify(self, path: Path) -> bool: ...


def is_plain_sqlite(path: Path) -> bool:
    with path.open("rb") as file:
        return file.read(len(PLAIN_HEADER)) == PLAIN_HEADER


class GameCrypto:
    def __init__(self, dll_path: Path, key: bytes) -> None:
        if ctypes.sizeof(ctypes.c_void_p) != 4:
            raise CryptoError(
                "The game DLL needs a 32-bit Python. Recreate .venv with it."
            )
        try:
            self._lib = ctypes.CDLL(str(dll_path))
            self._declare_functions()
        except (OSError, AttributeError):
            raise CryptoError("The game DLL could not be loaded.") from None
        self._key = key

    def _declare_functions(self) -> None:
        handle = ctypes.c_void_p
        lib = self._lib
        lib.sqlite3_open.argtypes = [ctypes.c_char_p, ctypes.POINTER(handle)]
        lib.sqlite3_open.restype = ctypes.c_int
        lib.sqlite3_close.argtypes = [handle]
        lib.sqlite3_close.restype = ctypes.c_int
        for name in ("sqlite3_key", "sqlite3_rekey"):
            function = getattr(lib, name)
            function.argtypes = [handle, ctypes.c_char_p, ctypes.c_int]
            function.restype = ctypes.c_int
        lib.sqlite3_exec.argtypes = [
            handle,
            ctypes.c_char_p,
            _ROW_CALLBACK,
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_void_p),
        ]
        lib.sqlite3_exec.restype = ctypes.c_int
        lib.sqlite3_free.argtypes = [ctypes.c_void_p]
        lib.sqlite3_free.restype = None

    @contextmanager
    def _open(
        self, path: Path, *, with_key: bool, key: bytes | None = None
    ) -> Iterator[ctypes.c_void_p]:
        # sqlite3_open creates missing files, so check first.
        if not path.is_file():
            raise CryptoError("Database file not found.")
        key = self._key if key is None else key
        db = ctypes.c_void_p()
        try:
            if self._lib.sqlite3_open(str(path).encode("utf-8"), ctypes.byref(db)):
                raise CryptoError("The database file could not be opened.")
            if with_key and self._lib.sqlite3_key(db, key, len(key)):
                raise CryptoError("The key could not be applied.")
            yield db
        finally:
            if db:
                self._lib.sqlite3_close(db)

    def verify(self, path: Path) -> bool:
        """True if the file opens with the key and lists at least one table."""
        return self.opens_with(path, self._key)

    def opens_with(self, path: Path, key: bytes) -> bool:
        """True if the file opens with this key and lists at least one table.

        Opening reads the file; use a copy when testing candidate keys.
        """
        table_count = 0

        def count_row(_context, _columns, _values, _names) -> int:
            nonlocal table_count
            table_count += 1
            return 0

        callback = _ROW_CALLBACK(count_row)
        error = ctypes.c_void_p()
        with self._open(path, with_key=True, key=key) as db:
            rc = self._lib.sqlite3_exec(
                db,
                b"SELECT name FROM sqlite_master WHERE type='table'",
                callback,
                None,
                ctypes.byref(error),
            )
            if error.value:
                self._lib.sqlite3_free(error)
        return rc == SQLITE_OK and table_count > 0

    def decrypt(self, source: Path, target: Path) -> None:
        if not self.verify(source):
            raise WrongKeyError("The database does not open with the configured key.")
        shutil.copyfile(source, target)
        with self._open(target, with_key=True) as db:
            rc = self._lib.sqlite3_rekey(db, None, 0)
        if rc != SQLITE_OK or not is_plain_sqlite(target):
            raise CryptoError("The database copy could not be decrypted.")

    def encrypt(self, source: Path, target: Path) -> None:
        if not is_plain_sqlite(source):
            raise CryptoError("The working copy is not a plain SQLite database.")
        shutil.copyfile(source, target)
        with self._open(target, with_key=False) as db:
            rc = self._lib.sqlite3_rekey(db, self._key, len(self._key))
        if rc != SQLITE_OK or is_plain_sqlite(target) or not self.verify(target):
            raise CryptoError("The database could not be encrypted.")
