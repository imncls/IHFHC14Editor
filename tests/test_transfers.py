import sqlite3

import pytest
from conftest import (
    CLUB,
    CLUB_POOL,
    GOALKEEPER_AVERAGE,
    NATIONAL_TEAM,
    OTHER_CLUB,
    PLAYER_WITH_IMAGE,
    PLAYER_WITHOUT_IMAGE,
    POOL,
    build_game_db,
)

from app.averages import stored_float
from app.errors import ValidationError
from app.repository import PlayerRepository


@pytest.fixture
def repository(connection: sqlite3.Connection) -> PlayerRepository:
    return PlayerRepository(connection)


def memberships(connection: sqlite3.Connection, player_id: int) -> set[int]:
    rows = connection.execute(
        "SELECT team_id FROM playerDBPlayerTeam WHERE player_id = ?", (player_id,)
    )
    return {team_id for (team_id,) in rows}


def captain(connection: sqlite3.Connection, team_id: int) -> int:
    (player_id,) = connection.execute(
        "SELECT sr_captain_player_id FROM playerDBTeam WHERE id = ?", (team_id,)
    ).fetchone()
    return player_id


def team_average(connection: sqlite3.Connection, team_id: int) -> float:
    (average,) = connection.execute(
        "SELECT skill_average FROM playerDBTeamAverages WHERE team_id = ?",
        (team_id,),
    ).fetchone()
    return average


def test_get_player_shows_teams(repository: PlayerRepository) -> None:
    player = repository.get_player(PLAYER_WITH_IMAGE)
    assert player["club"] == str(CLUB)
    assert player["national_team"] == str(NATIONAL_TEAM)


def test_no_club_row_shows_the_pool(
    repository: PlayerRepository, connection: sqlite3.Connection
) -> None:
    with connection:
        connection.execute(
            "DELETE FROM playerDBPlayerTeam WHERE player_id = ? AND team_id = ?",
            (PLAYER_WITHOUT_IMAGE, CLUB),
        )
    assert repository.get_player(PLAYER_WITHOUT_IMAGE)["club"] == str(CLUB_POOL)
    repository.set_values(PLAYER_WITHOUT_IMAGE, {"club": str(OTHER_CLUB)})
    assert memberships(connection, PLAYER_WITHOUT_IMAGE) == {OTHER_CLUB, POOL}


def test_team_options(repository: PlayerRepository) -> None:
    clubs = repository.team_options("club")
    assert clubs[0] == (CLUB_POOL, "No club (pool)")
    # Granollers has no players in the test data, so it is marked unused.
    assert (OTHER_CLUB, "Fraikin BM. Granollers [unused]") in clubs
    assert all(team_id not in {NATIONAL_TEAM, POOL} for team_id, _ in clubs)
    national = dict(repository.team_options("national_team"))
    assert national[POOL] == "No national team (pool)"
    assert national[115] == "Spain (Original) [unused]"


def test_club_transfer(
    repository: PlayerRepository, connection: sqlite3.Connection
) -> None:
    repository.set_values(PLAYER_WITHOUT_IMAGE, {"club": str(OTHER_CLUB)})
    assert memberships(connection, PLAYER_WITHOUT_IMAGE) == {OTHER_CLUB, POOL}
    # He was Barcelona's captain: the role is cleared when he leaves.
    assert captain(connection, CLUB) == 0
    # Barcelona keeps only the goalkeeper; Granollers only the new player.
    assert team_average(connection, CLUB) == stored_float(GOALKEEPER_AVERAGE)
    assert team_average(connection, OTHER_CLUB) == stored_float(50)


def test_national_transfer_and_back_to_the_pool(
    repository: PlayerRepository, connection: sqlite3.Connection
) -> None:
    repository.set_values(PLAYER_WITHOUT_IMAGE, {"national_team": str(NATIONAL_TEAM)})
    assert memberships(connection, PLAYER_WITHOUT_IMAGE) == {CLUB, NATIONAL_TEAM}
    repository.set_values(PLAYER_WITHOUT_IMAGE, {"national_team": str(POOL)})
    assert memberships(connection, PLAYER_WITHOUT_IMAGE) == {CLUB, POOL}


def test_same_team_changes_nothing(
    repository: PlayerRepository, connection: sqlite3.Connection
) -> None:
    repository.set_values(PLAYER_WITHOUT_IMAGE, {"club": str(CLUB)})
    assert captain(connection, CLUB) == PLAYER_WITHOUT_IMAGE


@pytest.mark.parametrize(
    "changes",
    [
        {"club": str(NATIONAL_TEAM)},  # a national team is not a club
        {"national_team": str(CLUB)},
        {"club": "9999"},
        {"club": "abc"},
        {"name": "Pérez", "club": "9999"},  # all or nothing
    ],
)
def test_invalid_transfers_write_nothing(
    repository: PlayerRepository, connection: sqlite3.Connection, changes: dict
) -> None:
    before = repository.get_player(PLAYER_WITHOUT_IMAGE)
    with pytest.raises(ValidationError) as error:
        repository.set_values(PLAYER_WITHOUT_IMAGE, changes)
    assert error.value.field in {"club", "national_team"}
    assert repository.get_player(PLAYER_WITHOUT_IMAGE) == before
    assert memberships(connection, PLAYER_WITHOUT_IMAGE) == {CLUB, POOL}


def test_transfer_is_logged_with_team_names(
    repository: PlayerRepository, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level("INFO", logger="app.activity"):
        repository.set_values(PLAYER_WITHOUT_IMAGE, {"club": str(OTHER_CLUB)})
    assert caplog.messages == [
        "player 1 (Ana Morel): club 'FC Barcelona Intersport (105)' -> "
        "'Fraikin BM. Granollers (150)'"
    ]


def test_revert_restores_teams_and_roles(
    repository: PlayerRepository, connection: sqlite3.Connection, tmp_path
) -> None:
    original = tmp_path / "original.db"
    build_game_db(original)
    repository.set_values(
        PLAYER_WITHOUT_IMAGE,
        {"club": str(OTHER_CLUB), "national_team": str(NATIONAL_TEAM)},
    )
    repository.revert_player(PLAYER_WITHOUT_IMAGE, original)
    assert memberships(connection, PLAYER_WITHOUT_IMAGE) == {CLUB, POOL}
    assert captain(connection, CLUB) == PLAYER_WITHOUT_IMAGE
    assert team_average(connection, OTHER_CLUB) == 0  # left without players


def test_team_warnings(
    repository: PlayerRepository, connection: sqlite3.Connection
) -> None:
    repository.set_values(PLAYER_WITHOUT_IMAGE, {"club": str(OTHER_CLUB)})
    warnings = repository.team_warnings(OTHER_CLUB, PLAYER_WITHOUT_IMAGE)
    assert warnings == [
        "Fraikin BM. Granollers has 1 players (real rosters have 14 to 25)."
    ]
    assert repository.team_warnings(POOL, PLAYER_WITHOUT_IMAGE) == []
    # Both test players wear number 7 at first: same number in Barcelona.
    with connection:
        connection.execute(
            "UPDATE playerDBPlayerData SET value = '7' WHERE data_id = 3"
        )
    repository.set_values(PLAYER_WITHOUT_IMAGE, {"club": str(CLUB)})
    assert "Another player of FC Barcelona Intersport has the same jersey number." in (
        repository.team_warnings(CLUB, PLAYER_WITHOUT_IMAGE)
    )
