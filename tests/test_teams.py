import sqlite3
from pathlib import Path

import pytest
from conftest import (
    BARCELONA_LOGO_FILE,
    CLUB,
    CLUB_POOL,
    NATIONAL_TEAM,
    OTHER_CLUB,
    PLAYER_WITH_IMAGE,
    PLAYER_WITHOUT_IMAGE,
    POOL,
    build_game_db,
)

from app.errors import ValidationError
from app.leagues import LeagueRepository
from app.portraits import logo_file
from app.repository import PlayerRepository
from app.teams import TEAM_FIELDS, NewTeam, TeamRepository, suggest_abbrev


@pytest.fixture
def teams(connection: sqlite3.Connection) -> TeamRepository:
    return TeamRepository(connection)


def column(connection: sqlite3.Connection, team_id: int, key: str) -> object:
    (value,) = connection.execute(
        f"SELECT {key} FROM playerDBTeam WHERE id = ?",  # noqa: S608 - test keys
        (team_id,),
    ).fetchone()
    return value


def test_get_team(teams: TeamRepository) -> None:
    team = teams.get_team(CLUB)
    assert set(team) == {field.key for field in TEAM_FIELDS}
    assert team["name"] == "FC Barcelona Intersport"
    assert team["rating"] == "5.0"
    assert team["home_colour"] == ""  # 0: no colour
    assert team["sr_captain_player_id"] == str(PLAYER_WITHOUT_IMAGE)
    with pytest.raises(ValidationError):
        teams.get_team(9999)


def test_options(teams: TeamRepository, connection: sqlite3.Connection) -> None:
    assert teams.options("home_arena", CLUB)[0] == (0, "None")
    assert (24, "Training gym") in teams.options("home_arena", CLUB)
    assert teams.options("spectators_average", CLUB) == [
        (1, "0 - 500"),
        (4, "2000 - 5000"),
    ]
    roles = teams.options("sr_captain_player_id", CLUB)
    assert roles[0] == (0, "(none)")
    assert (PLAYER_WITHOUT_IMAGE, "Ana Morel (1)") in roles
    # A role pointing outside the team is kept as an option.
    with connection:
        connection.execute(
            "UPDATE playerDBTeam SET sr_shooter_player_id = ? WHERE id = ?",
            (PLAYER_WITH_IMAGE, OTHER_CLUB),
        )
    shooters = teams.options("sr_shooter_player_id", OTHER_CLUB)
    assert shooters == [(0, "(none)"), (2, "player 2 (not in this team)")]
    assert teams.options("name", CLUB) == []


def test_set_team_values(teams: TeamRepository, connection: sqlite3.Connection) -> None:
    written = teams.set_team_values(
        CLUB,
        {
            "name": "Barça Test",
            "abbrev": "bts",
            "city": "",
            "rating": "2.5",
            "off_team_speed": "90",
            "home_arena": "9",
            "home_colour": "FF8800",
            "sr_captain_player_id": str(PLAYER_WITH_IMAGE),
        },
    )
    assert written["abbrev"] == "BTS"
    assert column(connection, CLUB, "name") == "Barça Test"
    assert column(connection, CLUB, "rating") == 2.5
    assert column(connection, CLUB, "off_team_speed") == 90
    assert column(connection, CLUB, "home_colour") == 0xFF8800
    assert column(connection, CLUB, "sr_captain_player_id") == PLAYER_WITH_IMAGE
    team = teams.get_team(CLUB)
    assert (team["home_colour"], team["city"]) == ("FF8800", "")


@pytest.mark.parametrize(
    "changes",
    [
        {"name": ""},
        {"coach": "x" * 31},
        {"abbrev": "TOOLONG"},
        {"rating": "6"},
        {"off_plays": "101"},
        {"home_arena": "11"},  # not in this game's arenas
        {"sr_captain_player_id": "999"},  # not in the team
        {"home_colour": "red"},
        {"type": "NATIONAL_TEAM"},  # not editable
        {"min_skill_average": "90"},  # above the highest (85)
        {"coach": "Test", "rating": "0"},  # all or nothing
    ],
)
def test_invalid_changes_write_nothing(
    teams: TeamRepository, changes: dict[str, str]
) -> None:
    before = teams.get_team(CLUB)
    with pytest.raises(ValidationError) as error:
        teams.set_team_values(CLUB, changes)
    assert error.value.field in changes
    assert teams.get_team(CLUB) == before


def test_pools_cannot_be_edited(teams: TeamRepository) -> None:
    for pool in (CLUB_POOL, POOL):
        with pytest.raises(ValidationError):
            teams.set_team_values(pool, {"coach": "Test"})


def test_activity_log(teams: TeamRepository, caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level("INFO", logger="app.activity"):
        teams.set_team_values(CLUB, {"coach": "Test", "rating": "2.5"})
    assert caplog.messages == [
        f"team {CLUB} (FC Barcelona Intersport): coach "
        "'Xavier Pascual Fuertes' -> 'Test'",
        f"team {CLUB} (FC Barcelona Intersport): rating '5.0' -> '2.5'",
    ]


def test_revert_team(
    teams: TeamRepository, connection: sqlite3.Connection, tmp_path: Path
) -> None:
    original = tmp_path / "original.db"
    build_game_db(original)
    before = teams.get_team(CLUB)
    teams.set_team_values(CLUB, {"name": "Renamed", "def_holding": "1"})
    teams.revert_team(CLUB, original)
    assert teams.get_team(CLUB) == before
    databases = [row[1] for row in connection.execute("PRAGMA database_list")]
    assert "original" not in databases
    with connection:
        connection.execute(
            "INSERT INTO playerDBTeam (id, name, type) VALUES (400, 'New', 'LEAGUE')"
        )
    with pytest.raises(ValidationError):
        teams.revert_team(400, original)


@pytest.fixture
def original_db(tmp_path: Path) -> Path:
    path = tmp_path / "original.db"
    build_game_db(path)
    return path


def outfits(connection: sqlite3.Connection, team_id: int) -> list[tuple]:
    return connection.execute(
        "SELECT o.type, o.texture, COUNT(t.ID) FROM playerDBTeamOutfit o "
        "LEFT JOIN TextureCustomization t ON t.TeamOutfitID = o.id "
        "WHERE o.team_id = ? GROUP BY o.id ORDER BY o.type",
        (team_id,),
    ).fetchall()


@pytest.fixture
def league(connection: sqlite3.Connection) -> int:
    """A league made with the editor."""
    return LeagueRepository(connection).create_league("Friends League", 1)


def test_create_team(
    teams: TeamRepository,
    connection: sqlite3.Connection,
    league: int,
    original_db: Path,
) -> None:
    team_id = teams.create_team(CLUB, league, original_db)
    assert team_id == max(
        team for (team,) in connection.execute("SELECT id FROM playerDBTeam")
    )
    team, template = teams.get_team(team_id), teams.get_team(CLUB)
    assert (team["name"], team["abbrev"]) == ("New team", "NEW")
    assert team["sr_captain_player_id"] == "0"
    same = {
        key for key in template if key not in {"name", "abbrev"} and "sr_" not in key
    }
    assert {key: team[key] for key in same} == {key: template[key] for key in same}
    assert column(connection, team_id, "league_id") == league
    assert teams.logo_file(team_id) is None  # the game's "no logo" logo
    # Kits and the numbers printed on them, with new outfit ids.
    assert outfits(connection, team_id) == outfits(connection, CLUB)
    assert connection.execute(
        "SELECT skill_average FROM playerDBTeamAverages WHERE team_id = ?", (team_id,)
    ).fetchone() == (0,)
    assert connection.execute(
        "SELECT COUNT(*) FROM playerDBPlayerTeam WHERE team_id = ?", (team_id,)
    ).fetchone() == (0,)
    assert teams.get_team(teams.create_team(CLUB, league, original_db))["name"] == (
        "New team (2)"
    )


def test_create_team_with_a_name(
    teams: TeamRepository, league: int, original_db: Path
) -> None:
    team_id = teams.create_team(OTHER_CLUB, league, original_db, " Friends FC ")
    assert teams.get_team(team_id)["name"] == "Friends FC"
    for name in ("friends fc", "", "A<b>"):
        with pytest.raises(ValidationError):
            teams.create_team(OTHER_CLUB, league, original_db, name)


@pytest.mark.parametrize(
    ("template", "league_id"),
    [
        (POOL, None),  # pools are not copied
        (NATIONAL_TEAM, None),  # only clubs
        (CLUB, 4),  # original leagues never get new teams
        (CLUB, 999),
    ],
)
def test_create_team_refusals(
    teams: TeamRepository,
    connection: sqlite3.Connection,
    league: int,
    original_db: Path,
    template: int,
    league_id: int | None,
) -> None:
    count = connection.execute("SELECT COUNT(*) FROM playerDBTeam").fetchone()
    with pytest.raises(ValidationError):
        teams.create_team(template, league_id or league, original_db)
    assert connection.execute("SELECT COUNT(*) FROM playerDBTeam").fetchone() == count


def test_move_team(teams: TeamRepository, league: int, original_db: Path) -> None:
    other = LeagueRepository(teams._connection).create_league("Second League", 1)
    team_id = teams.create_team(CLUB, league, original_db)
    teams.move_team(team_id, other, original_db)
    assert teams.league_of(team_id) == other
    with pytest.raises(ValidationError):
        teams.move_team(team_id, 4, original_db)  # an original league
    with pytest.raises(ValidationError):
        teams.move_team(CLUB, other, original_db)  # an original team
    assert teams.league_of(CLUB) == 4


def test_names_are_unique(teams: TeamRepository) -> None:
    with pytest.raises(ValidationError) as error:
        teams.set_team_values(OTHER_CLUB, {"name": "fc barcelona intersport"})
    assert error.value.field == "name"
    teams.set_team_values(CLUB, {"name": "FC Barcelona Intersport"})  # its own name


def test_delete_team(
    teams: TeamRepository,
    connection: sqlite3.Connection,
    league: int,
    original_db: Path,
) -> None:
    team_id = teams.create_team(CLUB, league, original_db, "Friends FC")
    PlayerRepository(connection).set_values(
        PLAYER_WITHOUT_IMAGE, {"club": str(team_id)}
    )
    teams.set_team_values(team_id, {"sr_captain_player_id": str(PLAYER_WITHOUT_IMAGE)})
    teams.delete_team(team_id, original_db)
    for table, key in (
        ("playerDBTeam", "id"),
        ("playerDBTeamOutfit", "team_id"),
        ("playerDBTeamAverages", "team_id"),
        ("playerDBPlayerTeam", "team_id"),
    ):
        query = f"SELECT COUNT(*) FROM {table} WHERE {key} = ?"  # noqa: S608 - test
        assert connection.execute(query, (team_id,)).fetchone() == (0,)
    assert connection.execute(
        "SELECT COUNT(*) FROM TextureCustomization WHERE TeamOutfitID NOT IN "
        "(SELECT id FROM playerDBTeamOutfit)"
    ).fetchone() == (0,)
    # His player went to the club pool.
    assert PlayerRepository(connection).get_player(PLAYER_WITHOUT_IMAGE)["club"] == str(
        CLUB_POOL
    )


def test_original_teams_cannot_be_deleted(
    teams: TeamRepository, original_db: Path
) -> None:
    with pytest.raises(ValidationError):
        teams.delete_team(CLUB, original_db)
    assert teams.get_team(CLUB)["name"] == "FC Barcelona Intersport"


def test_custom_logo_file(
    teams: TeamRepository, league: int, original_db: Path
) -> None:
    teams.use_custom_logo(CLUB)
    assert teams.logo_file(CLUB) == logo_file(CLUB)
    teams.restore_logo_file(CLUB, original_db)
    assert teams.logo_file(CLUB) == BARCELONA_LOGO_FILE
    teams.use_custom_logo(CLUB)
    teams.revert_team(CLUB, original_db)
    assert teams.logo_file(CLUB) == BARCELONA_LOGO_FILE
    new_team = teams.create_team(CLUB, league, original_db)
    teams.use_custom_logo(new_team)
    teams.restore_logo_file(new_team, original_db)
    assert teams.logo_file(new_team) is None


def test_delete_team_is_all_or_nothing(
    teams: TeamRepository,
    connection: sqlite3.Connection,
    league: int,
    original_db: Path,
) -> None:
    team_id = teams.create_team(CLUB, league, original_db, "Friends FC")
    players = PlayerRepository(connection)
    players.set_values(PLAYER_WITHOUT_IMAGE, {"club": str(team_id)})
    # A failure after the players were moved must undo the moves too.
    connection.execute("DROP TABLE TextureCustomization")
    with pytest.raises(sqlite3.OperationalError):
        teams.delete_team(team_id, original_db)
    assert players.get_player(PLAYER_WITHOUT_IMAGE)["club"] == str(team_id)
    assert teams.get_team(team_id)["name"] == "Friends FC"


def members(connection: sqlite3.Connection, team_id: int) -> list[int]:
    return [
        player
        for (player,) in connection.execute(
            "SELECT player_id FROM playerDBPlayerTeam WHERE team_id = ? "
            "ORDER BY player_id",
            (team_id,),
        )
    ]


def test_create_team_with_players(
    teams: TeamRepository,
    connection: sqlite3.Connection,
    league: int,
    original_db: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    players = PlayerRepository(connection)
    with caplog.at_level("INFO", logger="app.activity"):
        team_id, copies = teams.create_team_with_players(
            CLUB, league, original_db, "Friends FC"
        )
    assert sorted(copies) == [PLAYER_WITHOUT_IMAGE, PLAYER_WITH_IMAGE]
    assert members(connection, team_id) == sorted(copies.values())
    assert not set(copies.values()) & {PLAYER_WITHOUT_IMAGE, PLAYER_WITH_IMAGE}
    # The club keeps its players; a player belongs to one club.
    assert members(connection, CLUB) == [PLAYER_WITHOUT_IMAGE, PLAYER_WITH_IMAGE]
    for source_id, player_id in copies.items():
        copy, source = players.get_player(player_id), players.get_player(source_id)
        assert copy["club"] == str(team_id)
        assert copy["national_team"] == str(POOL)  # never in two national teams
        # Same names, data and skills; the unused player_img is not copied.
        same = set(source) - {"club", "national_team", "player_img"}
        assert {key: copy[key] for key in same} == {key: source[key] for key in same}
    # The roles point to the copies, not to the club's players.
    assert (
        column(connection, team_id, "sr_captain_player_id")
        == copies[PLAYER_WITHOUT_IMAGE]
    )
    assert column(connection, team_id, "sr_shooter_player_id") == 0
    assert connection.execute(
        "SELECT COUNT(*) FROM Customize_table WHERE player_id = ?",
        (copies[PLAYER_WITHOUT_IMAGE],),
    ).fetchone() == (1,)
    (average,) = connection.execute(
        "SELECT skill_average FROM playerDBTeamAverages WHERE team_id = ?",
        (team_id,),
    ).fetchone()
    assert average > 0
    assert f"created as a copy of player {PLAYER_WITH_IMAGE}" in caplog.text
    assert teams.player_count(team_id) == len(copies)


def test_create_team_with_players_is_all_or_nothing(
    teams: TeamRepository,
    connection: sqlite3.Connection,
    league: int,
    original_db: Path,
) -> None:
    count = connection.execute("SELECT COUNT(*) FROM playerDBPlayer").fetchone()
    # A failure while copying the players must undo the new team too.
    connection.execute("DROP TABLE Customize_table")
    with pytest.raises(sqlite3.OperationalError):
        teams.create_team_with_players(CLUB, league, original_db, "Friends FC")
    assert connection.execute(
        "SELECT COUNT(*) FROM playerDBTeam WHERE name = 'Friends FC'"
    ).fetchone() == (0,)
    assert connection.execute("SELECT COUNT(*) FROM playerDBPlayer").fetchone() == count


@pytest.mark.parametrize(
    ("name", "abbrev"),
    [
        ("Friends FC", "FRI"),
        ("BM. Atlético de Madrid", "ATL"),
        ("THW Kiel", "THW"),
        ("FC Barcelona", "BAR"),
        ("Ñandú", "NAN"),
        ("12 October", "OCT"),
        ("A B", "AB"),
        ("!!!", "NEW"),
        ("", "NEW"),
    ],
)
def test_suggest_abbrev(name: str, abbrev: str) -> None:
    assert suggest_abbrev(name) == abbrev


def test_create_team_abbreviation(
    teams: TeamRepository, league: int, original_db: Path
) -> None:
    team_id = teams.create_team(CLUB, league, original_db, "Friends FC")
    assert teams.get_team(team_id)["abbrev"] == "FRI"
    team_id = teams.create_team(CLUB, league, original_db, "Friends Two", " fr2 ")
    assert teams.get_team(team_id)["abbrev"] == "FR2"
    with pytest.raises(ValidationError) as error:
        teams.create_team(CLUB, league, original_db, "Friends Three", "LONG")
    assert error.value.field == "abbrev"


def team_count(connection: sqlite3.Connection) -> tuple[int, int]:
    return connection.execute(
        "SELECT (SELECT COUNT(*) FROM playerDBTeam), "
        "(SELECT COUNT(*) FROM playerDBLeague)"
    ).fetchone()


def test_create_teams_with_details(
    teams: TeamRepository,
    league: int,
    original_db: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    details = {"coach": " Joe ", "home_colour": "ff0000", "rating": "3.5"}
    with caplog.at_level("INFO", logger="app.activity"):
        league_id, created = teams.create_teams(
            [NewTeam(CLUB, "Friends FC", details=details)], original_db, league
        )
    team = teams.get_team(created[0][0])
    assert league_id == league
    assert (team["coach"], team["home_colour"], team["rating"]) == (
        "Joe",
        "FF0000",
        "3.5",
    )
    assert team["city"] == teams.get_team(CLUB)["city"]  # the rest is copied
    assert "coach 'Joe'" in caplog.text


def test_create_teams_in_a_new_league(
    teams: TeamRepository, connection: sqlite3.Connection, original_db: Path
) -> None:
    league_id, created = teams.create_teams(
        [
            NewTeam(CLUB, "Friends FC", with_players=True),
            NewTeam(OTHER_CLUB),
            NewTeam(CLUB),
        ],
        original_db,
        new_league=(" New League ", 1),
    )
    league = LeagueRepository(connection).get_league(league_id)
    assert (league.name, league.team_count) == ("New League", 3)
    names = [teams.get_team(team_id)["name"] for team_id, _copies in created]
    assert names == ["Friends FC", "New team", "New team (2)"]
    assert len(created[0][1]) == 2  # Barcelona's two players
    assert created[1][1] == {}
    assert all(teams.league_of(team_id) == league_id for team_id, _ in created)


@pytest.mark.parametrize(
    ("batch", "field", "team"),
    [
        ([NewTeam(CLUB, "Friends"), NewTeam(OTHER_CLUB, "friends ")], "name", 1),
        ([NewTeam(CLUB, "Friends", details={"coach": ""})], "coach", 0),
        (
            [
                NewTeam(
                    CLUB,
                    "Friends",
                    details={"min_skill_average": "95", "max_skill_average": "90"},
                )
            ],
            "min_skill_average",
            0,
        ),
        (
            [NewTeam(CLUB, "Friends", details={"sr_captain_player_id": "1"})],
            "sr_captain_player_id",
            0,
        ),
        ([NewTeam(POOL, "Friends")], None, 0),
    ],
    ids=["duplicate name", "empty coach", "levels", "role", "pool"],
)
def test_create_teams_refusals_write_nothing(
    teams: TeamRepository,
    connection: sqlite3.Connection,
    original_db: Path,
    batch: list,
    field: str | None,
    team: int,
) -> None:
    before = team_count(connection)
    with pytest.raises(ValidationError) as error:
        teams.create_teams(batch, original_db, new_league=("New League", 1))
    assert (error.value.field, error.value.team) == (field, team)
    assert team_count(connection) == before


def test_create_teams_league_refusals(
    teams: TeamRepository, connection: sqlite3.Connection, original_db: Path
) -> None:
    before = team_count(connection)
    with pytest.raises(ValidationError) as error:
        teams.create_teams([NewTeam(CLUB)], original_db, new_league=("", 1))
    assert (error.value.field, error.value.team) == ("name", None)
    with pytest.raises(ValidationError) as error:
        teams.create_teams([NewTeam(CLUB)], original_db, league_id=4)
    assert error.value.field == "league_id"
    assert team_count(connection) == before


def test_create_teams_is_all_or_nothing(
    teams: TeamRepository, connection: sqlite3.Connection, original_db: Path
) -> None:
    before = team_count(connection)
    connection.execute("DROP TABLE TextureCustomization")
    with pytest.raises(sqlite3.OperationalError):
        teams.create_teams(
            [NewTeam(CLUB, "Friends FC")], original_db, new_league=("New League", 1)
        )
    assert team_count(connection) == before
