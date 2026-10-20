import sqlite3
from pathlib import Path

import pytest
from conftest import build_game_db

from app.errors import ValidationError
from app.leagues import League, LeagueRepository

ORIGINAL_LEAGUE = 4  # Liga ASOBAL in the test data


@pytest.fixture
def leagues(connection: sqlite3.Connection) -> LeagueRepository:
    return LeagueRepository(connection)


@pytest.fixture
def original_db(tmp_path: Path) -> Path:
    path = tmp_path / "original.db"
    build_game_db(path)
    return path


def add_teams(connection: sqlite3.Connection, league_id: int, count: int) -> None:
    with connection:
        connection.executemany(
            "INSERT INTO playerDBTeam (name, type, league_id) VALUES (?, 'LEAGUE', ?)",
            [(f"Team {league_id}-{n}", league_id) for n in range(count)],
        )


def test_create_league(
    leagues: LeagueRepository, connection: sqlite3.Connection, original_db: Path
) -> None:
    league_id = leagues.create_league(" Friends League ", 2)
    assert league_id == 19  # after the original ids, by AUTOINCREMENT
    row = connection.execute(
        "SELECT name, country_id, inFinalGame, parentID, teamCount, logoFile "
        "FROM playerDBLeague WHERE id = ?",
        (league_id,),
    ).fetchone()
    assert row == ("Friends League", 2, 1, 0, 0, None)
    assert leagues.editor_leagues(original_db) == [
        League(league_id, "Friends League", 2, 0)
    ]


@pytest.mark.parametrize(
    ("name", "country"),
    [
        ("liga asobal", 1),  # taken, whatever the case
        ("National teams", 1),  # the team tree's own group
        ("", 1),
        ("A<b>", 1),
        ("Friends League", 99),  # no such country
    ],
)
def test_create_league_refusals(
    leagues: LeagueRepository, connection: sqlite3.Connection, name: str, country: int
) -> None:
    count = connection.execute("SELECT COUNT(*) FROM playerDBLeague").fetchone()
    with pytest.raises(ValidationError):
        leagues.create_league(name, country)
    assert connection.execute("SELECT COUNT(*) FROM playerDBLeague").fetchone() == count


def test_only_editor_leagues_change(
    leagues: LeagueRepository, original_db: Path
) -> None:
    league_id = leagues.create_league("Friends League", 1)
    written = leagues.set_league_values(
        league_id, {"name": "Third League", "country_id": "2"}, original_db
    )
    assert written == {"name": "Third League", "country_id": "2"}
    assert leagues.get_league(league_id).name == "Third League"
    with pytest.raises(ValidationError):
        leagues.set_league_values(ORIGINAL_LEAGUE, {"name": "Otra"}, original_db)
    with pytest.raises(ValidationError) as error:
        leagues.set_league_values(league_id, {"inFinalGame": "0"}, original_db)
    assert error.value.field == "inFinalGame"


def test_delete_only_empty_editor_leagues(
    leagues: LeagueRepository, connection: sqlite3.Connection, original_db: Path
) -> None:
    league_id = leagues.create_league("Friends League", 1)
    add_teams(connection, league_id, 1)
    with pytest.raises(ValidationError, match="teams first"):
        leagues.delete_league(league_id, original_db)
    with pytest.raises(ValidationError):
        leagues.delete_league(ORIGINAL_LEAGUE, original_db)
    with connection:
        connection.execute("DELETE FROM playerDBTeam WHERE league_id = ?", (league_id,))
    leagues.delete_league(league_id, original_db)
    assert leagues.editor_leagues(original_db) == []


@pytest.mark.parametrize(
    ("count", "warned"), [(0, True), (3, True), (4, False), (5, True), (6, False)]
)
def test_league_warnings(
    leagues: LeagueRepository,
    connection: sqlite3.Connection,
    count: int,
    warned: bool,
) -> None:
    league_id = leagues.create_league("Friends League", 1)
    add_teams(connection, league_id, count)
    warnings = leagues.league_warnings(league_id)
    assert bool(warnings) == warned
    if warned:
        assert warnings == [
            f"Friends League has {count} teams; league mode needs an even number, "
            "at least 4."
        ]


def test_activity_log(
    leagues: LeagueRepository, original_db: Path, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level("INFO", logger="app.activity"):
        league_id = leagues.create_league("Friends League", 1)
        leagues.set_league_values(league_id, {"name": "Third League"}, original_db)
        leagues.delete_league(league_id, original_db)
    assert caplog.messages == [
        f"league {league_id} (Friends League): created",
        f"league {league_id} (Friends League): name 'Friends League' -> 'Third League'",
        f"league {league_id} (Third League): deleted",
    ]


def test_saving_the_same_values_writes_nothing(
    leagues: LeagueRepository, original_db: Path, caplog: pytest.LogCaptureFixture
) -> None:
    league_id = leagues.create_league("Test", 1)
    with caplog.at_level("INFO", logger="app.activity"):
        assert (
            leagues.set_league_values(
                league_id, {"name": " Test ", "country_id": "1"}, original_db
            )
            == {}
        )
    assert caplog.messages == []


def test_check_new_league_writes_nothing(connection: sqlite3.Connection) -> None:
    leagues = LeagueRepository(connection)
    count = connection.execute("SELECT COUNT(*) FROM playerDBLeague").fetchone()
    assert leagues.check_new_league(" New League ", 1) == ("New League", 1)
    with pytest.raises(ValidationError) as error:
        leagues.check_new_league("Liga ASOBAL", 1)
    assert error.value.field == "name"
    with pytest.raises(ValidationError) as error:
        leagues.check_new_league("New League", 999)
    assert error.value.field == "country_id"
    assert connection.execute("SELECT COUNT(*) FROM playerDBLeague").fetchone() == count
