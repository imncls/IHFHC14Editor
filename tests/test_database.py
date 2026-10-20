import sqlite3
from contextlib import closing
from pathlib import Path

import pytest
from conftest import CLUB, build_game_db

from app.database import attached_original, connect, name_taken, open_read_only


@pytest.fixture
def original_db(tmp_path: Path) -> Path:
    # Spaces, "#" and "%" in the name: the URI must still point to this file.
    path = tmp_path / "original data #1 100%.db"
    build_game_db(path)
    return path


def test_original_is_attached_read_only(
    connection: sqlite3.Connection, original_db: Path
) -> None:
    with attached_original(connection, original_db):
        (name,) = connection.execute(
            "SELECT name FROM original.playerDBTeam WHERE id = ?", (CLUB,)
        ).fetchone()
        assert name == "FC Barcelona Intersport"
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            connection.execute("UPDATE original.playerDBTeam SET name = 'X'")
        with connection:  # writing the working copy from it still works
            connection.execute(
                "UPDATE main.playerDBTeam SET coach = "
                "(SELECT coach FROM original.playerDBTeam WHERE id = ?) WHERE id = ?",
                (CLUB, CLUB),
            )
    assert not (original_db.parent / "original").exists()  # no stray file


def test_original_is_detached_after_an_error(
    connection: sqlite3.Connection, original_db: Path
) -> None:
    with pytest.raises(RuntimeError), attached_original(connection, original_db):
        raise RuntimeError
    schemas = [row[1] for row in connection.execute("PRAGMA database_list")]
    assert "original" not in schemas


def test_open_read_only(original_db: Path) -> None:
    with closing(open_read_only(original_db)) as connection:
        assert connection.execute("SELECT COUNT(*) FROM playerDBTeam").fetchone()[0]
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            connection.execute("DELETE FROM playerDBTeam")


def test_connect_takes_a_plain_path(tmp_path: Path) -> None:
    path = tmp_path / "plain path #1.db"
    with closing(connect(path)) as connection:
        connection.execute("CREATE TABLE t (x)")
    assert path.is_file()


def test_name_taken(connection: sqlite3.Connection) -> None:
    assert name_taken(connection, "playerDBTeam", "fc barcelona intersport")
    assert not name_taken(connection, "playerDBTeam", "Friends FC")
    # Renaming a team to its own name is not taking another's.
    assert not name_taken(connection, "playerDBTeam", "FC Barcelona Intersport", CLUB)
    assert name_taken(connection, "playerDBLeague", "LIGA ASOBAL")
    with pytest.raises(ValueError):
        name_taken(connection, "playerDBPlayer", "x")
