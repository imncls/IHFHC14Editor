import sqlite3
from pathlib import Path

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

from app.averages import GOALKEEPER, SKILLS, stored_float
from app.errors import ValidationError
from app.repository import CLUB as CLUB_KEY
from app.repository import NATIONAL_TEAM as NATIONAL_KEY
from app.repository import OVERALL, PlayerRepository

NEW_ID = 3  # the test data has players 1 and 2
TEAM_KEYS = {CLUB_KEY, NATIONAL_KEY}
NEW_PLAYER_AVERAGE = 65  # every skill at 65


@pytest.fixture
def repository(connection: sqlite3.Connection) -> PlayerRepository:
    return PlayerRepository(connection)


@pytest.fixture
def original_db(tmp_path: Path) -> Path:
    path = tmp_path / "original.db"
    build_game_db(path)
    return path


def memberships(connection: sqlite3.Connection, player_id: int) -> set[int]:
    rows = connection.execute(
        "SELECT team_id FROM playerDBPlayerTeam WHERE player_id = ?", (player_id,)
    )
    return {team_id for (team_id,) in rows}


def appearance(connection: sqlite3.Connection, player_id: int) -> tuple | None:
    return connection.execute(
        "SELECT skin_color, hairstyle FROM Customize_table WHERE player_id = ?",
        (player_id,),
    ).fetchone()


def count_rows(connection: sqlite3.Connection, player_id: int) -> int:
    (count,) = connection.execute(
        "SELECT (SELECT COUNT(*) FROM playerDBPlayerData WHERE player_id = :id) "
        "+ (SELECT COUNT(*) FROM playerDBPlayer WHERE id = :id) "
        "+ (SELECT COUNT(*) FROM playerDBPlayerTeam WHERE player_id = :id) "
        "+ (SELECT COUNT(*) FROM Customize_table WHERE player_id = :id)",
        {"id": player_id},
    ).fetchone()
    return count


def team_average(connection: sqlite3.Connection, team_id: int) -> float:
    (average,) = connection.execute(
        "SELECT skill_average FROM playerDBTeamAverages WHERE team_id = ?",
        (team_id,),
    ).fetchone()
    return average


def test_new_id_is_after_every_table(
    repository: PlayerRepository, connection: sqlite3.Connection
) -> None:
    assert repository.create_player() == NEW_ID
    with connection:
        connection.execute("INSERT INTO Customize_table (player_id) VALUES (10)")
    assert repository.create_player() == 11


def test_new_player(
    repository: PlayerRepository, connection: sqlite3.Connection
) -> None:
    player_id = repository.create_player(OTHER_CLUB)
    player = repository.get_player(player_id)
    assert (player["first_name"], player["name"]) == ("New", "Player")
    assert player["jersey_nr"] == "1"  # Granollers is empty
    assert player["country"] == "2"  # Granollers' country
    assert player["main_pos"] == "24"
    assert all(player[skill] == "65" for skill in SKILLS)
    assert player["total_avg"] == "65.00"
    assert "player_img" not in player
    # Every value passes the editor's own validation unchanged.
    fields = repository.fields()
    for key in fields.keys() & player.keys():
        assert repository._validate_key(fields, key, player[key]) == player[key]
    assert memberships(connection, player_id) == {OTHER_CLUB, POOL}
    assert team_average(connection, OTHER_CLUB) == stored_float(65)
    assert appearance(connection, player_id) is None


def test_new_player_takes_the_first_free_number(
    repository: PlayerRepository, connection: sqlite3.Connection
) -> None:
    with connection:
        connection.execute(
            "UPDATE playerDBPlayerData SET value = '1' "
            "WHERE player_id = ? AND data_id = 3",
            (PLAYER_WITHOUT_IMAGE,),
        )
    player_id = repository.create_player(CLUB)  # numbers 1 and 3 are taken
    assert repository.get_player(player_id)["jersey_nr"] == "2"


@pytest.mark.parametrize(
    ("team_id", "teams"),
    [
        (CLUB, {CLUB, POOL}),
        (NATIONAL_TEAM, {CLUB_POOL, NATIONAL_TEAM}),
        (POOL, {CLUB_POOL, POOL}),
        (None, {CLUB_POOL, POOL}),
    ],
)
def test_new_player_teams(
    repository: PlayerRepository,
    connection: sqlite3.Connection,
    team_id: int | None,
    teams: set[int],
) -> None:
    player_id = repository.create_player(team_id)
    assert memberships(connection, player_id) == teams


def test_country_without_a_real_team(
    repository: PlayerRepository, connection: sqlite3.Connection
) -> None:
    assert repository.get_player(repository.create_player())["country"] == "1"
    with connection:
        connection.execute("INSERT INTO Country VALUES (195, 'Spain (game id)')")
    # Spain when it exists; a pool's country is not used.
    assert repository.get_player(repository.create_player(POOL))["country"] == "195"
    assert repository.get_player(repository.create_player(CLUB))["country"] == "1"


def test_new_player_in_a_missing_team(
    repository: PlayerRepository, connection: sqlite3.Connection
) -> None:
    with pytest.raises(ValidationError):
        repository.create_player(9999)
    assert count_rows(connection, NEW_ID) == 0


def test_duplicate(
    repository: PlayerRepository, connection: sqlite3.Connection
) -> None:
    source = repository.get_player(PLAYER_WITH_IMAGE)
    player_id = repository.duplicate_player(PLAYER_WITH_IMAGE, OTHER_CLUB)
    copy = repository.get_player(player_id)
    unchanged = source.keys() - TEAM_KEYS - {"player_img"}
    assert {key: copy[key] for key in unchanged} == {
        key: source[key] for key in unchanged
    }
    assert "player_img" not in copy
    assert memberships(connection, player_id) == {OTHER_CLUB, POOL}
    assert team_average(connection, OTHER_CLUB) == stored_float(GOALKEEPER_AVERAGE)
    assert appearance(connection, player_id) is None  # the source has none


def test_duplicate_copies_the_appearance(
    repository: PlayerRepository, connection: sqlite3.Connection
) -> None:
    player_id = repository.duplicate_player(PLAYER_WITHOUT_IMAGE)
    assert appearance(connection, player_id) == ("Euro01", "1061_Hair06")
    assert memberships(connection, player_id) == {CLUB_POOL, POOL}


def test_duplicate_missing_player(
    repository: PlayerRepository, connection: sqlite3.Connection
) -> None:
    with pytest.raises(ValidationError):
        repository.duplicate_player(99)
    assert count_rows(connection, NEW_ID) == 0


def test_delete_created_player(
    repository: PlayerRepository, connection: sqlite3.Connection, original_db: Path
) -> None:
    player_id = repository.duplicate_player(PLAYER_WITHOUT_IMAGE, OTHER_CLUB)
    with connection:
        connection.execute(
            "UPDATE playerDBTeam SET sr_captain_player_id = ? WHERE id = ?",
            (player_id, OTHER_CLUB),
        )
    assert not repository.is_original(player_id, original_db)
    repository.delete_player(player_id, original_db)
    assert count_rows(connection, player_id) == 0
    (captain,) = connection.execute(
        "SELECT sr_captain_player_id FROM playerDBTeam WHERE id = ?", (OTHER_CLUB,)
    ).fetchone()
    assert captain == 0
    assert team_average(connection, OTHER_CLUB) == 0  # empty again
    databases = [row[1] for row in connection.execute("PRAGMA database_list")]
    assert "original" not in databases


def test_original_players_cannot_be_deleted(
    repository: PlayerRepository, connection: sqlite3.Connection, original_db: Path
) -> None:
    assert repository.is_original(PLAYER_WITHOUT_IMAGE, original_db)
    before = count_rows(connection, PLAYER_WITHOUT_IMAGE)
    with pytest.raises(ValidationError, match="created with the editor"):
        repository.delete_player(PLAYER_WITHOUT_IMAGE, original_db)
    assert count_rows(connection, PLAYER_WITHOUT_IMAGE) == before


def test_activity_log(
    repository: PlayerRepository,
    original_db: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level("INFO", logger="app.activity"):
        new_id = repository.create_player(OTHER_CLUB)
        copy_id = repository.duplicate_player(PLAYER_WITHOUT_IMAGE)
        repository.delete_player(new_id, original_db)
    assert caplog.messages == [
        f"player {new_id} (New Player): created in "
        f"Fraikin BM. Granollers ({OTHER_CLUB}), NOT ASSIGNABLE ({POOL})",
        f"player {copy_id} (Ana Morel): created as a copy of player "
        f"{PLAYER_WITHOUT_IMAGE} in Not assigned ({CLUB_POOL}), "
        f"NOT ASSIGNABLE ({POOL})",
        f"player {new_id} (New Player): deleted",
    ]


def test_new_player_values(repository: PlayerRepository) -> None:
    values = repository.new_player_values(CLUB)
    assert (values[CLUB_KEY], values[NATIONAL_KEY]) == (str(CLUB), str(POOL))
    assert all(values[key] == "65" for key in SKILLS)
    assert repository.new_player_values(NATIONAL_TEAM)[CLUB_KEY] == str(CLUB_POOL)
    pools = repository.new_player_values()
    assert (pools[CLUB_KEY], pools[NATIONAL_KEY]) == (str(CLUB_POOL), str(POOL))


def test_new_player_with_values(
    repository: PlayerRepository,
    connection: sqlite3.Connection,
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level("INFO", logger="app.activity"):
        player_id = repository.create_player(
            CLUB,
            {
                "first_name": "Sam",
                "name": "Taylor",
                "jersey_nr": "77",
                "sk_speed": "90",
                CLUB_KEY: str(OTHER_CLUB),
                NATIONAL_KEY: str(NATIONAL_TEAM),
            },
        )
    player = repository.get_player(player_id)
    assert (player["first_name"], player["name"], player["jersey_nr"]) == (
        "Sam",
        "Taylor",
        "77",
    )
    assert player["sk_speed"] == "90"
    assert memberships(connection, player_id) == {OTHER_CLUB, NATIONAL_TEAM}
    # The averages follow the skills.
    assert float(player["total_avg"]) > NEW_PLAYER_AVERAGE
    assert caplog.messages == [
        f"player {player_id} (Sam Taylor): created in "
        f"Fraikin BM. Granollers ({OTHER_CLUB}), Spain ({NATIONAL_TEAM})"
    ]


def test_new_goalkeeper_with_an_overall(repository: PlayerRepository) -> None:
    player_id = repository.create_player(
        None,
        {
            "main_pos": str(GOALKEEPER),
            "first_name": "Sam",
            "name": "Keeper",
            OVERALL: "88",
        },
    )
    assert repository.get_player(player_id)["total_avg"] == "88.00"


@pytest.mark.parametrize(
    "values",
    [
        {"name": ""},
        {"sk_speed": "150"},
        {CLUB_KEY: "999"},
        {OVERALL: "88"},  # not a goalkeeper
    ],
    ids=["empty name", "skill", "club", "overall"],
)
def test_new_player_with_bad_values_writes_nothing(
    repository: PlayerRepository, connection: sqlite3.Connection, values: dict
) -> None:
    count = connection.execute("SELECT COUNT(*) FROM playerDBPlayer").fetchone()
    with pytest.raises(ValidationError) as error:
        repository.create_player(CLUB, values)
    assert error.value.field in values
    assert connection.execute("SELECT COUNT(*) FROM playerDBPlayer").fetchone() == (
        count
    )
