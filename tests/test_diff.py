import sqlite3
from contextlib import closing
from pathlib import Path

import pytest
from conftest import CLUB, FAKE_TEAM, PLAYER_WITHOUT_IMAGE, build_game_db

from app.diff import diff_databases
from app.repository import PlayerRepository


@pytest.fixture
def databases(tmp_path: Path) -> tuple[Path, Path]:
    original = tmp_path / "original.db"
    current = tmp_path / "current.db"
    build_game_db(original)
    build_game_db(current)
    return current, original


def test_no_differences(databases: tuple[Path, Path]) -> None:
    assert diff_databases(*databases) == []


def test_readable_changes(databases: tuple[Path, Path]) -> None:
    current, original = databases
    with closing(sqlite3.connect(current)) as connection:
        PlayerRepository(connection).set_values(
            PLAYER_WITHOUT_IMAGE, {"name": "Pérez", "sk_throw": "99"}
        )
        with connection:
            connection.execute(
                "INSERT INTO playerDBPlayerTeam VALUES (?, ?)",
                (PLAYER_WITHOUT_IMAGE, FAKE_TEAM),
            )
            connection.execute("INSERT INTO Country VALUES (3, 'Italy')")
    lines = diff_databases(current, original)
    player = "player 1 (Ana Pérez)"
    assert f"{player}: name 'Morel' -> 'Pérez'" in lines
    assert f"{player}: sk_throw 77 -> 99" in lines
    assert any(line.startswith(f"{player}: total_avg 50.00 -> ") for line in lines)
    assert f"{player}: joined team {FAKE_TEAM} ((Fake) Nîmes Gard (Fake))" in lines
    assert any(line.startswith(f"team {CLUB} (FC Barcelona") for line in lines)
    assert (
        "table Country: 1 rows new or changed, 0 original rows changed or removed"
        in lines
    )


def test_added_player(databases: tuple[Path, Path]) -> None:
    current, original = databases
    with closing(sqlite3.connect(current)) as connection, connection:
        connection.execute("INSERT INTO playerDBPlayerData VALUES (9, 1, 'New')")
    assert "player 9 (New): added" in diff_databases(current, original)


def test_team_changes(databases: tuple[Path, Path]) -> None:
    current, original = databases
    with closing(sqlite3.connect(current)) as connection, connection:
        connection.execute(
            "UPDATE playerDBTeam SET coach = 'Test', rating = 2.5 WHERE id = ?",
            (CLUB,),
        )
        connection.execute(
            "INSERT INTO playerDBTeam (id, name, type) VALUES (400, 'New', 'LEAGUE')"
        )
    lines = diff_databases(current, original)
    team = f"team {CLUB} (FC Barcelona Intersport)"
    assert f"{team}: coach 'Xavier Pascual Fuertes' -> 'Test'" in lines
    assert f"{team}: rating 5.00 -> 2.50" in lines
    assert "team 400 (New): added" in lines
    assert not any(line.startswith("table playerDBTeam") for line in lines)


def test_league_changes(databases: tuple[Path, Path]) -> None:
    current, original = databases
    with closing(sqlite3.connect(current)) as connection, connection:
        connection.execute("UPDATE playerDBLeague SET name = 'Liga Otra' WHERE id = 4")
        connection.execute("INSERT INTO playerDBLeague (id, name) VALUES (19, 'New')")
    lines = diff_databases(current, original)
    assert "league 4 (Liga Otra): name 'Liga ASOBAL' -> 'Liga Otra'" in lines
    assert "league 19 (New): added" in lines
    assert not any(line.startswith("table playerDBLeague") for line in lines)


def test_look_changes(databases: tuple[Path, Path]) -> None:
    current, original = databases
    with closing(sqlite3.connect(current)) as connection, connection:
        connection.execute(
            "UPDATE Customize_table SET skin_color = 'African01' WHERE player_id = 1"
        )
        connection.execute("INSERT INTO Customize_table (player_id) VALUES (2)")
    lines = diff_databases(current, original)
    assert "player 1 (Ana Morel) look: skin_color 'Euro01' -> 'African01'" in lines
    assert "player 2 (Ana Morel) look: added" in lines
    assert not any(line.startswith("table Customize_table") for line in lines)
