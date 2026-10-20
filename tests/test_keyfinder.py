import struct
from pathlib import Path

import pytest

from app import keyfinder
from app.config import Paths
from app.errors import ConfigError
from app.keyfinder import extract_key, find_key, key_candidates, sections

BASE = 0x400000
ADDRESSES = {".text": 0x1000, ".rdata": 0x2000, ".data": 0x3000}
FAKE_KEY = b"k3y\xdf with \xa7 odd bytes!"
DECOY = b"just a game string"
UNREFERENCED = b"nothing points here"


def build_pe(text: bytes, rdata: bytes, data: bytes, machine: int = 0x14C) -> bytes:
    """A minimal 32-bit PE: DOS header, PE header and three sections."""
    contents = {".text": text, ".rdata": rdata, ".data": data}
    dos = b"MZ" + bytes(0x3A) + struct.pack("<I", 0x40)
    optional = bytearray(224)
    struct.pack_into("<H", optional, 0, 0x10B)
    struct.pack_into("<I", optional, 28, BASE)
    coff = struct.pack("<HHIIIHH", machine, len(contents), 0, 0, 0, len(optional), 0)
    header = dos + b"PE\0\0" + coff + bytes(optional)
    raw = 0x200
    table = b""
    body = b""
    for name, blob in contents.items():
        table += name.encode().ljust(8, b"\0")
        table += struct.pack(
            "<IIIIIIHHI",
            len(blob),
            ADDRESSES[name],
            len(blob),
            raw + len(body),
            0,
            0,
            0,
            0,
            0,
        )
        body += blob
    return (header + table).ljust(raw, b"\0") + body


def address(offset: int) -> bytes:
    return struct.pack("<I", BASE + ADDRESSES[".rdata"] + offset)


def rdata_with_strings() -> bytes:
    # Offsets: unreferenced string at 0, decoy at 0x40, key at 0x80.
    rdata = bytearray(0x100)
    rdata[0 : len(UNREFERENCED)] = UNREFERENCED
    rdata[0x40 : 0x40 + len(DECOY)] = DECOY
    rdata[0x80 : 0x80 + len(FAKE_KEY)] = FAKE_KEY
    return bytes(rdata)


def test_key_through_a_data_pointer() -> None:
    exe = build_pe(b"\x90" * 16, rdata_with_strings(), address(0x40) + address(0x80))
    candidates = list(key_candidates(exe))
    assert candidates == [DECOY, FAKE_KEY]  # nothing points to the first string
    tried = []
    assert find_key(exe, lambda key: tried.append(key) or key == FAKE_KEY) == FAKE_KEY
    assert tried == [DECOY, FAKE_KEY]


def test_key_through_a_code_pointer() -> None:
    # An unaligned "push imm32" in the code, and no pointer in .data.
    text = b"\x90\x68" + address(0x80) + b"\x90"
    exe = build_pe(text, rdata_with_strings(), bytes(8))
    assert find_key(exe, lambda key: key == FAKE_KEY) == FAKE_KEY


def test_too_short_strings_are_skipped() -> None:
    rdata = bytearray(0x100)
    rdata[0x10:0x14] = b"abc!"
    exe = build_pe(b"", bytes(rdata), address(0x10))
    assert list(key_candidates(exe)) == []


def test_not_found_does_not_show_candidates() -> None:
    exe = build_pe(b"", rdata_with_strings(), address(0x80))
    with pytest.raises(ConfigError) as error:
        find_key(exe, lambda _key: False)
    assert FAKE_KEY.decode("latin-1") not in str(error.value)
    assert "not found" in str(error.value)


@pytest.mark.parametrize(
    "exe",
    [
        b"",
        b"not a program at all",
        build_pe(b"", b"", b"", machine=0x8664),  # 64-bit
        build_pe(b"", b"", b"")[:0x150],  # cut short
    ],
    ids=["empty", "text", "64-bit", "truncated"],
)
def test_other_files_are_refused(exe: bytes) -> None:
    with pytest.raises(ConfigError):
        sections(exe)


def paths_in(tmp_path: Path) -> Paths:
    game, save = tmp_path / "game", tmp_path / "save"
    game.mkdir()
    save.mkdir()
    return Paths(game_dir=game, save_dir=save)


def test_extract_key_tests_a_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = paths_in(tmp_path)
    exe = build_pe(b"", rdata_with_strings(), address(0x40) + address(0x80))
    (paths.game_dir / "HC14.exe").write_bytes(exe)
    paths.dump_path.write_bytes(b"encrypted data")
    copies = []

    class FakeCrypto:
        def __init__(self, _dll: Path, _key: bytes) -> None:
            pass

        def opens_with(self, path: Path, key: bytes) -> bool:
            copies.append(path)
            assert path != paths.dump_path and path.read_bytes() == b"encrypted data"
            return key == FAKE_KEY

    monkeypatch.setattr("app.gamecrypto.GameCrypto", FakeCrypto)
    assert extract_key(paths) == FAKE_KEY
    assert copies and not copies[0].exists()  # the temporary copy is gone
    assert keyfinder.key_opens(paths, FAKE_KEY)
    assert not keyfinder.key_opens(paths, DECOY)


def test_extract_key_needs_the_game_and_its_data(tmp_path: Path) -> None:
    paths = paths_in(tmp_path)
    with pytest.raises(ConfigError, match="HC14.exe"):
        extract_key(paths)
    (paths.game_dir / "HC14.exe").write_bytes(b"MZ")
    with pytest.raises(ConfigError, match="Start the game once"):
        extract_key(paths)
