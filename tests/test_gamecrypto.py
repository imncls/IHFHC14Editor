"""Round trip with the real game DLL and key, on a copy of the installed data."""

import shutil
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from app.config import load_config
from app.errors import ConfigError, WrongKeyError
from app.gamecrypto import GameCrypto, is_plain_sqlite

pytestmark = pytest.mark.game_dll

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.local.json"


@pytest.fixture
def real(tmp_path: Path) -> tuple[GameCrypto, Path, Path]:
    try:
        config = load_config(CONFIG_PATH)
    except ConfigError:
        pytest.skip("config.local.json, the game DLL or the save data is missing")
    encrypted = tmp_path / "encrypted.db"
    shutil.copyfile(config.dump_path, encrypted)
    return GameCrypto(config.dll_path, config.key), encrypted, config.dll_path


def test_round_trip(real: tuple[GameCrypto, Path, Path], tmp_path: Path) -> None:
    crypto, encrypted, _ = real
    assert crypto.verify(encrypted)
    clear = tmp_path / "clear.db"
    crypto.decrypt(encrypted, clear)
    assert is_plain_sqlite(clear)
    with closing(sqlite3.connect(clear)) as connection:
        (count,) = connection.execute("SELECT COUNT(*) FROM playerDBData").fetchone()
    assert count > 0
    again = tmp_path / "again.db"
    crypto.encrypt(clear, again)
    assert not is_plain_sqlite(again)
    assert crypto.verify(again)


def test_wrong_key(real: tuple[GameCrypto, Path, Path], tmp_path: Path) -> None:
    _, encrypted, dll_path = real
    wrong = GameCrypto(dll_path, b"\x00" * 55)
    assert not wrong.verify(encrypted)
    with pytest.raises(WrongKeyError):
        wrong.decrypt(encrypted, tmp_path / "clear.db")
