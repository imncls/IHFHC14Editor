"""Find the database key in the installed game's HC14.exe.

The game keeps its key as a plain NUL-terminated string in the executable's
read-only data, with a pointer to it in its data section (see
docs/game-data.md). Strings that some pointer refers to are tried one by one
with the game's DLL on a copy of the save data; the first one that opens it is
the key. Candidates are never shown or logged.
"""

import shutil
import struct
import tempfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING

from app.errors import ConfigError

if TYPE_CHECKING:
    from app.config import Paths

GAME_EXE = "HC14.exe"
MIN_KEY = 8
MAX_KEY = 256
I386 = 0x14C
NOT_AN_EXE = "HC14.exe is not a game executable this editor knows."
NOT_FOUND = "The key was not found in HC14.exe; this game build may hide it."


def sections(exe: bytes) -> dict[str, tuple[int, bytes]]:
    """Section name -> (virtual address, raw contents) of a 32-bit PE file."""
    try:
        if exe[:2] != b"MZ":
            raise ValueError
        header = struct.unpack_from("<I", exe, 0x3C)[0]
        if exe[header : header + 4] != b"PE\0\0":
            raise ValueError
        machine, count = struct.unpack_from("<HH", exe, header + 4)
        optional_size = struct.unpack_from("<H", exe, header + 20)[0]
        if machine != I386:
            raise ValueError
        image_base = struct.unpack_from("<I", exe, header + 24 + 28)[0]
        table = header + 24 + optional_size
        found = {}
        for index in range(count):
            entry = table + 40 * index
            name = exe[entry : entry + 8].rstrip(b"\0").decode("ascii", "replace")
            _size, address, raw_size, raw = struct.unpack_from("<IIII", exe, entry + 8)
            if raw + raw_size > len(exe):
                raise ValueError
            found[name] = (image_base + address, exe[raw : raw + raw_size])
    except (struct.error, ValueError):
        raise ConfigError(NOT_AN_EXE) from None
    if not {".text", ".rdata", ".data"} <= found.keys():
        raise ConfigError(NOT_AN_EXE)
    return found


def key_candidates(exe: bytes) -> Iterator[bytes]:
    """NUL-terminated strings of .rdata that a pointer refers to.

    First the pointers stored in .data (where the game keeps the key's), then
    any 4 bytes in .data and .text that look like one.
    """
    found = sections(exe)
    rdata_address, rdata = found[".rdata"]
    seen: set[int] = set()

    def strings(addresses: Iterator[int]) -> Iterator[bytes]:
        for address in addresses:
            offset = address - rdata_address
            if not 0 <= offset < len(rdata) or offset in seen:
                continue
            seen.add(offset)
            end = rdata.find(b"\0", offset, offset + MAX_KEY + 1)
            if end != -1 and end - offset >= MIN_KEY:
                yield rdata[offset:end]

    data = found[".data"][1]
    yield from strings(
        struct.unpack_from("<I", data, at)[0] for at in range(0, len(data) - 3, 4)
    )
    for name in (".data", ".text"):
        blob = found[name][1]
        yield from strings(
            int.from_bytes(blob[at : at + 4], "little") for at in range(len(blob) - 3)
        )


def find_key(exe: bytes, opens: Callable[[bytes], bool]) -> bytes:
    """The first candidate that opens the game's data."""
    for candidate in key_candidates(exe):
        if opens(candidate):
            return candidate
    raise ConfigError(NOT_FOUND)


def extract_key(paths: "Paths") -> bytes:
    """Read the key from the installed game, checked against its save data."""
    exe_path = paths.game_dir / GAME_EXE
    if not exe_path.is_file():
        raise ConfigError(f"game_dir does not contain {GAME_EXE}.")
    with _opener(paths) as opens:
        return find_key(exe_path.read_bytes(), opens)


def key_opens(paths: "Paths", key: bytes) -> bool:
    """Whether a key (for example a saved one) opens the installed data."""
    with _opener(paths) as opens:
        return opens(key)


@contextmanager
def _opener(paths: "Paths") -> Iterator[Callable[[bytes], bool]]:
    """A key test on a temporary copy, so the real save is never touched."""
    from app.gamecrypto import GameCrypto  # the 32-bit DLL; not for the MCP side

    if not paths.dump_path.is_file():
        raise ConfigError("Start the game once so it creates its data.")
    crypto = GameCrypto(paths.dll_path, b"")
    with tempfile.TemporaryDirectory(prefix="ihfhc-key-") as folder:
        copy = Path(folder) / "data.db"
        shutil.copyfile(paths.dump_path, copy)
        yield lambda key: crypto.opens_with(copy, key)
