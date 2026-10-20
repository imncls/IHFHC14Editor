import sqlite3

import pytest
from conftest import (
    CLUB,
    EMPTY_ORIGINAL_TEAM,
    FAKE_TEAM,
    GOALKEEPER_AVERAGE,
    NATIONAL_TEAM,
    PLAYER_WITH_IMAGE,
    PLAYER_WITHOUT_IMAGE,
    POOL,
    SKILLS_BY_PLAYER,
    build_game_db,
)

from app.averages import SKILLS, player_averages, stored_float
from app.errors import ValidationError
from app.repository import PlayerRepository, Team, name_warnings


@pytest.fixture
def repository(connection: sqlite3.Connection) -> PlayerRepository:
    return PlayerRepository(connection)


def test_fields_exclude_unused_appearance_fields(repository: PlayerRepository) -> None:
    fields = repository.fields()
    assert "name" in fields
    assert "body" not in fields


def test_get_player_with_and_without_image(repository: PlayerRepository) -> None:
    assert "player_img" not in repository.get_player(PLAYER_WITHOUT_IMAGE)
    assert repository.get_player(PLAYER_WITH_IMAGE)["player_img"] == "MOREL.jpg"


def test_get_missing_player(repository: PlayerRepository) -> None:
    with pytest.raises(ValidationError):
        repository.get_player(999)


def test_find_players(repository: PlayerRepository) -> None:
    found = repository.find_players("mor")
    assert [player.id for player in found] == [PLAYER_WITHOUT_IMAGE, PLAYER_WITH_IMAGE]
    assert repository.find_players("%") == []


def test_set_value_updates(repository: PlayerRepository) -> None:
    assert repository.set_value(PLAYER_WITHOUT_IMAGE, "name", " Pérez ") == "Pérez"
    assert repository.set_value(PLAYER_WITHOUT_IMAGE, "dob", "01.02.1995") == "1.2.1995"
    assert repository.set_value(PLAYER_WITHOUT_IMAGE, "main_pos", "28") == "28"
    assert repository.set_value(PLAYER_WITHOUT_IMAGE, "country", "2") == "2"
    player = repository.get_player(PLAYER_WITHOUT_IMAGE)
    assert player["name"] == "Pérez"
    assert player["dob"] == "1.2.1995"


def test_set_image_inserts_and_empty_deletes(repository: PlayerRepository) -> None:
    repository.set_value(PLAYER_WITHOUT_IMAGE, "player_img", "ANA.png")
    assert repository.get_player(PLAYER_WITHOUT_IMAGE)["player_img"] == "ANA.png"
    repository.set_value(PLAYER_WITH_IMAGE, "player_img", "")
    assert "player_img" not in repository.get_player(PLAYER_WITH_IMAGE)


def test_empty_image_does_not_create_row(repository: PlayerRepository) -> None:
    repository.set_value(PLAYER_WITHOUT_IMAGE, "player_img", "")
    assert "player_img" not in repository.get_player(PLAYER_WITHOUT_IMAGE)


@pytest.mark.parametrize(
    "key, raw",
    [
        ("name", "x" * 50),
        ("main_pos", "9"),
        ("throw_arm", "22"),
        ("country", "3"),
        ("body", "1"),
        ("missing", "1"),
        ("name; DROP TABLE Country", "x"),
    ],
)
def test_invalid_values_change_nothing(
    repository: PlayerRepository, key: str, raw: str
) -> None:
    before = repository.get_player(PLAYER_WITHOUT_IMAGE)
    with pytest.raises(ValidationError):
        repository.set_value(PLAYER_WITHOUT_IMAGE, key, raw)
    assert repository.get_player(PLAYER_WITHOUT_IMAGE) == before


def test_set_value_on_missing_player(repository: PlayerRepository) -> None:
    with pytest.raises(ValidationError):
        repository.set_value(999, "name", "Ana")


def test_changes_are_committed(repository: PlayerRepository, game_db) -> None:
    repository.set_value(PLAYER_WITHOUT_IMAGE, "name", "Pérez")
    with sqlite3.connect(game_db) as other:
        (value,) = other.execute(
            "SELECT value FROM playerDBPlayerData WHERE player_id = ? AND data_id = 1",
            (PLAYER_WITHOUT_IMAGE,),
        ).fetchone()
    assert value == "Pérez"


def test_teams_groups_and_counts(repository: PlayerRepository) -> None:
    teams = {team.id: team for team in repository.teams()}
    assert teams[CLUB].group == "Liga ASOBAL"
    assert teams[CLUB].player_count == 2
    assert teams[NATIONAL_TEAM].group == "National teams"
    assert teams[POOL].group == "National teams"


def test_unused_teams(repository: PlayerRepository) -> None:
    teams = {team.id: team for team in repository.teams()}
    assert teams[EMPTY_ORIGINAL_TEAM].is_unused
    assert teams[FAKE_TEAM].is_unused
    assert not teams[CLUB].is_unused
    assert teams[POOL].is_pool
    assert not teams[POOL].is_unused


def test_unused_rules() -> None:
    assert Team(1, "Random_Test", "Not in use", 5).is_unused
    assert Team(1, "Billère Handball (not in use)", "Not in use", 25).is_unused
    assert Team(1, "Empty", "Liga ASOBAL", 0).is_unused
    assert not Team(194, "Not assigned", "Not in use", 0).is_unused


def test_team_players_ordered_by_jersey(repository: PlayerRepository) -> None:
    roster = repository.team_players(CLUB)
    assert [player.id for player in roster] == [PLAYER_WITH_IMAGE, PLAYER_WITHOUT_IMAGE]
    assert roster[0].jersey == "3"
    assert roster[0].position == "Goalkeeper"
    assert roster[0].average == "87.25"
    assert repository.team_players(EMPTY_ORIGINAL_TEAM) == []


def test_options(repository: PlayerRepository) -> None:
    fields = repository.fields()
    assert repository.options(fields["main_pos"]) == [
        (22, "Left wing"),
        (24, "Centre back"),
        (28, "Goalkeeper"),
    ]
    assert repository.options(fields["country"]) == [(2, "France"), (1, "Spain")]
    assert repository.options(fields["name"]) == []


def test_set_values_is_all_or_nothing(repository: PlayerRepository) -> None:
    before = repository.get_player(PLAYER_WITHOUT_IMAGE)
    with pytest.raises(ValidationError) as error:
        repository.set_values(
            PLAYER_WITHOUT_IMAGE, {"name": "Pérez", "dob": "31.2.1990"}
        )
    assert error.value.field == "dob"
    assert repository.get_player(PLAYER_WITHOUT_IMAGE) == before
    written = repository.set_values(
        PLAYER_WITHOUT_IMAGE, {"name": "Pérez", "dob": "01.02.1995"}
    )
    assert written == {"name": "Pérez", "dob": "1.2.1995"}


def stored_averages(connection: sqlite3.Connection, player_id: int) -> tuple:
    return connection.execute(
        "SELECT total_avg, total_def_avg, total_off_avg FROM playerDBPlayer "
        "WHERE id = ?",
        (player_id,),
    ).fetchone()


def team_average(connection: sqlite3.Connection, team_id: int) -> float | None:
    row = connection.execute(
        "SELECT skill_average FROM playerDBTeamAverages WHERE team_id = ?",
        (team_id,),
    ).fetchone()
    return row and row[0]


def test_get_player_includes_skills(repository: PlayerRepository) -> None:
    player = repository.get_player(PLAYER_WITH_IMAGE)
    assert player["sk_react"] == "90"
    assert player["total_avg"] == player["overall"] == "87.25"


def test_skill_change_recalculates_player_and_teams(
    repository: PlayerRepository, connection: sqlite3.Connection
) -> None:
    repository.set_values(PLAYER_WITHOUT_IMAGE, {"sk_throw": "99"})
    skills = dict(zip(SKILLS, SKILLS_BY_PLAYER[PLAYER_WITHOUT_IMAGE], strict=True))
    skills["sk_throw"] = 99
    expected = player_averages(skills, 22)
    assert stored_averages(connection, PLAYER_WITHOUT_IMAGE) == expected
    club_mean = stored_float((expected[0] + stored_float(GOALKEEPER_AVERAGE)) / 2)
    assert team_average(connection, CLUB) == club_mean
    assert team_average(connection, POOL) == club_mean
    assert team_average(connection, NATIONAL_TEAM) == 0
    assert team_average(connection, FAKE_TEAM) is None


def test_position_change_switches_between_formula_and_hand_set(
    repository: PlayerRepository, connection: sqlite3.Connection
) -> None:
    repository.set_values(PLAYER_WITHOUT_IMAGE, {"main_pos": "28"})
    assert stored_averages(connection, PLAYER_WITHOUT_IMAGE) == (50, 50, 50)
    repository.set_values(PLAYER_WITHOUT_IMAGE, {"main_pos": "22"})
    skills = dict(zip(SKILLS, SKILLS_BY_PLAYER[PLAYER_WITHOUT_IMAGE], strict=True))
    expected = player_averages(skills, 22)
    assert stored_averages(connection, PLAYER_WITHOUT_IMAGE) == expected


def test_goalkeeper_overall(
    repository: PlayerRepository, connection: sqlite3.Connection
) -> None:
    repository.set_values(PLAYER_WITH_IMAGE, {"overall": "88.5", "sk_react": "99"})
    assert stored_averages(connection, PLAYER_WITH_IMAGE) == (stored_float(88.5),) * 3
    assert repository.get_player(PLAYER_WITH_IMAGE)["overall"] == "88.50"


@pytest.mark.parametrize(
    "player_id, changes, field",
    [
        (PLAYER_WITHOUT_IMAGE, {"overall": "80"}, "overall"),
        (PLAYER_WITH_IMAGE, {"overall": "100.5"}, "overall"),
        (PLAYER_WITH_IMAGE, {"overall": "8.555"}, "overall"),
        (PLAYER_WITHOUT_IMAGE, {"name": "Pérez", "sk_fit": "101"}, "sk_fit"),
        (PLAYER_WITHOUT_IMAGE, {"sk_fit": "-1"}, "sk_fit"),
    ],
)
def test_invalid_skill_changes_write_nothing(
    repository: PlayerRepository,
    connection: sqlite3.Connection,
    player_id: int,
    changes: dict,
    field: str,
) -> None:
    before = repository.get_player(player_id)
    with pytest.raises(ValidationError) as error:
        repository.set_values(player_id, changes)
    assert error.value.field == field
    assert repository.get_player(player_id) == before


@pytest.fixture
def original_db(tmp_path):
    path = tmp_path / "original.db"
    build_game_db(path)
    return path


def test_revert_player(
    repository: PlayerRepository, connection: sqlite3.Connection, original_db
) -> None:
    before = repository.get_player(PLAYER_WITHOUT_IMAGE)
    repository.set_values(
        PLAYER_WITHOUT_IMAGE,
        {"name": "Pérez", "sk_throw": "99", "player_img": "ANA.png"},
    )
    repository.revert_player(PLAYER_WITHOUT_IMAGE, original_db)
    assert repository.get_player(PLAYER_WITHOUT_IMAGE) == before
    expected_club = stored_float((50 + stored_float(GOALKEEPER_AVERAGE)) / 2)
    assert team_average(connection, CLUB) == expected_club
    databases = [row[1] for row in connection.execute("PRAGMA database_list")]
    assert "original" not in databases


def test_revert_player_missing_from_original(
    repository: PlayerRepository, original_db
) -> None:
    with sqlite3.connect(original_db) as original:
        original.execute(
            "DELETE FROM playerDBPlayerData WHERE player_id = ?",
            (PLAYER_WITHOUT_IMAGE,),
        )
    original.close()
    repository.set_values(PLAYER_WITHOUT_IMAGE, {"name": "Pérez"})
    with pytest.raises(ValidationError):
        repository.revert_player(PLAYER_WITHOUT_IMAGE, original_db)
    assert repository.get_player(PLAYER_WITHOUT_IMAGE)["name"] == "Pérez"


def test_changes_are_written_to_the_activity_log(
    repository: PlayerRepository, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level("INFO", logger="app.activity"):
        repository.set_values(PLAYER_WITHOUT_IMAGE, {"name": "Pérez"})
    assert caplog.messages == ["player 1 (Ana Morel): name 'Morel' -> 'Pérez'"]


def test_long_names_are_allowed_with_a_warning(repository: PlayerRepository) -> None:
    long_name = "Fernández-Villaverde de la Torre Gómez"  # 38: shown by the game
    assert repository.set_value(PLAYER_WITHOUT_IMAGE, "name", long_name) == long_name
    assert name_warnings({"name": long_name})
    assert name_warnings({"name": "Morel", "sk_fit": "x" * 30}) == []
    with pytest.raises(ValidationError):
        repository.set_value(PLAYER_WITHOUT_IMAGE, "name", "A" * 41)


def test_search_players_ignores_case_and_accents(
    repository: PlayerRepository, connection: sqlite3.Connection
) -> None:
    with connection:
        connection.execute(
            "UPDATE playerDBPlayerData SET value = 'Entrerríos' "
            "WHERE player_id = ? AND data_id = 1",
            (PLAYER_WITHOUT_IMAGE,),
        )
    (found,) = repository.search_players("  ANA entrerrios ")
    assert (found.id, found.name, found.club) == (
        PLAYER_WITHOUT_IMAGE,
        "Entrerríos",
        "FC Barcelona Intersport",
    )
    assert {p.id for p in repository.search_players("ana")} == {
        PLAYER_WITHOUT_IMAGE,
        PLAYER_WITH_IMAGE,
    }
    assert repository.search_players("nobody") == []
    assert repository.search_players("   ") == []
