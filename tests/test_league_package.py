import io
import json
import sqlite3
import zipfile
from collections.abc import Iterator
from contextlib import closing
from pathlib import Path

import pytest
from conftest import (
    ARCHIVE_ENTRIES,
    CLUB,
    NATIONAL_TEAM,
    OTHER_CLUB,
    PLAYER_WITH_IMAGE,
    PLAYER_WITHOUT_IMAGE,
    PORTRAITS_ARCHIVE,
    build_game_db,
    build_portrait_archive,
    swatch_png,
)
from PIL import Image

from app import league_package
from app.averages import GOALKEEPER
from app.errors import ValidationError
from app.league_package import (
    MANIFEST,
    export_league,
    import_league,
    import_name,
    read_package,
)
from app.leagues import LeagueRepository
from app.portrait_image import PORTRAIT_SIZE, logo_from_bytes
from app.portraits import DDS_SIGNATURE, PortraitStore
from app.repository import APPEARANCE_COLUMNS, OVERALL, PlayerRepository
from app.teams import SHEET_KEYS, NewTeam, TeamRepository

# A logo as the editor makes it (images are written again on import).
LOGO = logo_from_bytes(swatch_png("green"))
# Averages are recalculated from the skills on import (the game's data follows
# the formula; the test data does not), and the club is the new team.
IGNORED = {"club", OVERALL, "total_avg", "total_def_avg", "total_off_avg"}


@pytest.fixture
def original_db(tmp_path: Path) -> Path:
    path = tmp_path / "original.db"
    build_game_db(path)
    return path


@pytest.fixture
def league(
    connection: sqlite3.Connection, original_db: Path, portraits: PortraitStore
) -> int:
    """An editor league: a copy of Barcelona with its players, a coach and a
    logo, and a team with a new goalkeeper in a national team, with a photo."""
    teams = TeamRepository(connection)
    league_id, created = teams.create_teams(
        [
            NewTeam(CLUB, "Friends FC", with_players=True, details={"coach": "Joe"}),
            NewTeam(OTHER_CLUB, "Mates"),
        ],
        original_db,
        new_league=("Friends League", 1),
    )
    (friends, copies), (mates, _none) = created
    portraits.set_logo(friends, *LOGO)
    teams.use_custom_logo(friends)
    keeper = PlayerRepository(connection).create_player(
        mates,
        {
            "first_name": "Sam",
            "name": "Keeper",
            "main_pos": str(GOALKEEPER),
            OVERALL: "88",
            "national_team": str(NATIONAL_TEAM),
        },
    )
    portraits.set(keeper, swatch_png("orange"))
    # A date with leading zeros, as many in the game's data.
    connection.execute(
        "UPDATE playerDBPlayerData SET value = '05.03.1999' WHERE player_id = ? "
        "AND data_id = (SELECT id FROM playerDBData WHERE key = 'dob')",
        (keeper,),
    )
    connection.commit()
    assert copies[PLAYER_WITHOUT_IMAGE]  # the copy with a 3D look
    return league_id


@pytest.fixture
def other(tmp_path: Path) -> Iterator[tuple[sqlite3.Connection, PortraitStore]]:
    """A second game: where the package is imported."""
    path = tmp_path / "other.db"
    build_game_db(path)
    archive = tmp_path / "other-game" / PORTRAITS_ARCHIVE
    build_portrait_archive(archive, {})
    root = tmp_path / "other-project"
    root.mkdir()
    with closing(sqlite3.connect(path)) as connection:
        yield connection, PortraitStore(archive, root)


@pytest.fixture
def package_path(
    tmp_path: Path,
    connection: sqlite3.Connection,
    league: int,
    original_db: Path,
    portraits: PortraitStore,
) -> Path:
    path = tmp_path / "liga.hc14league"
    summary = export_league(connection, league, original_db, portraits, path)
    assert (summary.league, summary.teams, summary.players) == ("Friends League", 2, 3)
    return path


def snapshot(connection: sqlite3.Connection, league_name: str) -> list:
    """What a league looks like, without ids."""
    teams, players = TeamRepository(connection), PlayerRepository(connection)
    result = []
    for (team_id,) in connection.execute(
        "SELECT t.id FROM playerDBTeam t JOIN playerDBLeague l ON l.id = t.league_id "
        "WHERE l.name = ? ORDER BY t.id",
        (league_name,),
    ):
        team = teams.get_team(team_id)
        members = players.team_players(team_id)
        names = {member.id: member.name for member in members}
        roles = [
            names.get(team[key]) if team[key] != "0" else None
            for key in team
            if key.startswith("sr_")
        ]
        kits = connection.execute(
            "SELECT o.type, o.texture, COUNT(x.ID) FROM playerDBTeamOutfit o "
            "LEFT JOIN TextureCustomization x ON x.TeamOutfitID = o.id "
            "WHERE o.team_id = ? GROUP BY o.id ORDER BY o.type",
            (team_id,),
        ).fetchall()
        people = sorted(
            (
                tuple(
                    sorted(
                        (key, value)
                        for key, value in players.get_player(member.id).items()
                        if key not in IGNORED
                    )
                ),
                connection.execute(
                    f"SELECT {', '.join(APPEARANCE_COLUMNS)} FROM Customize_table "  # noqa: S608 - constants
                    "WHERE player_id = ?",
                    (member.id,),
                ).fetchone(),
            )
            for member in members
        )
        result.append(({key: team[key] for key in SHEET_KEYS}, roles, kits, people))
    return result


def test_round_trip(
    connection: sqlite3.Connection,
    package_path: Path,
    other: tuple[sqlite3.Connection, PortraitStore],
    appearance_options,
) -> None:
    target, target_portraits = other
    package = read_package(package_path)
    assert import_name(target, package) == "Friends League"
    league_id = import_league(
        target, package, target_portraits, appearance_options, "liga.hc14league"
    )
    assert LeagueRepository(target).get_league(league_id).name == "Friends League"
    imported, exported = (
        snapshot(target, "Friends League"),
        snapshot(connection, "Friends League"),
    )
    assert len(imported) == len(exported) == 2
    for theirs, ours in zip(imported, exported, strict=True):
        for part, (a, b) in zip(
            ("sheet", "roles", "kits", "players"),
            zip(theirs, ours, strict=True),
            strict=True,
        ):
            assert (part, a) == (part, b)
    teams = TeamRepository(target)
    friends, mates = [
        team_id
        for (team_id,) in target.execute(
            "SELECT id FROM playerDBTeam WHERE league_id = ? ORDER BY id", (league_id,)
        )
    ]
    assert teams.get_team(friends)["coach"] == "Joe"
    big, small = target_portraits.get_logo(friends)
    assert big.startswith(DDS_SIGNATURE) and small.startswith(DDS_SIGNATURE)
    assert len(big) == len(LOGO[0])  # same size and format: 512 DXT5
    assert "custom_logo" in teams.logo_file(friends)
    assert teams.logo_file(mates) is None
    (keeper,) = PlayerRepository(target).team_players(mates)
    with Image.open(io.BytesIO(target_portraits.get(keeper.id))) as photo:
        assert (photo.format, photo.size) == ("PNG", PORTRAIT_SIZE)
        assert photo.convert("RGB").getpixel((65, 79)) == (255, 165, 0)  # orange
    player = PlayerRepository(target).get_player(keeper.id)
    assert (player["dob"], player[OVERALL], player["national_team"]) == (
        "05.03.1999",  # as written
        "88.00",
        str(NATIONAL_TEAM),
    )


def test_importing_twice_gives_free_names(
    package_path: Path,
    other: tuple[sqlite3.Connection, PortraitStore],
    appearance_options,
) -> None:
    target, target_portraits = other
    import_league(
        target, read_package(package_path), target_portraits, appearance_options
    )
    package = read_package(package_path)
    assert import_name(target, package) == "Friends League (2)"
    league_id = import_league(target, package, target_portraits, appearance_options)
    names = [
        name
        for (name,) in target.execute(
            "SELECT name FROM playerDBTeam WHERE league_id = ? ORDER BY id",
            (league_id,),
        )
    ]
    assert names == ["Friends FC (2)", "Mates (2)"]


def test_original_leagues_are_not_exported(
    tmp_path: Path, connection: sqlite3.Connection, original_db: Path
) -> None:
    path = tmp_path / "asobal.hc14league"
    with pytest.raises(ValidationError):
        export_league(connection, 4, original_db, None, path)
    assert not path.exists()


def rewrite(
    source: Path,
    target: Path,
    change=None,
    files: dict[str, bytes | None] | None = None,
) -> Path:
    """A copy of a package with a changed manifest or entries."""
    with zipfile.ZipFile(source) as archive:
        entries = {name: archive.read(name) for name in archive.namelist()}
    manifest = json.loads(entries[MANIFEST])
    if change is not None:
        change(manifest)
    entries[MANIFEST] = json.dumps(manifest).encode()
    for name, data in (files or {}).items():
        if data is None:
            entries.pop(name, None)
        else:
            entries[name] = data
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
    target.write_bytes(output.getvalue())
    return target


def set_in(path: tuple, value: object):
    def change(manifest: dict) -> None:
        item = manifest
        for part in path[:-1]:
            item = item[part]
        item[path[-1]] = value

    return change


def in_look(column: str, value: object):
    """Change the look of the first player who has one."""

    def change(manifest: dict) -> None:
        player = next(p for p in manifest["players"] if p["look"] is not None)
        player["look"][column] = value

    return change


@pytest.mark.parametrize(
    ("change", "files"),
    [
        (set_in(("version",), 2), None),
        (set_in(("format",), "other"), None),
        (set_in(("players", 0, "team"), "t9"), None),
        (set_in(("teams", 0, "roles"), {"sr_captain_player_id": "p99"}), None),
        (set_in(("teams", 0, "sheet", "abbrev"), {"a": 1}), None),
        (in_look("muscularity", 7), None),
        (in_look("single_morphs", "x'; DROP"), None),
        (set_in(("teams", 0, "outfits", 0, "customizations", 0, "Color"), "red"), None),
        (None, {"../evil.png": b"x"}),
        (None, {"portraits/p99.png": swatch_png("red")}),
        (None, {"logos/t1.dds": b"not a dds"}),
        (None, {"logos/t1_small.dds": None}),
        (
            set_in(("players", 2, "portrait"), True),
            {"portraits/p3.png": b"\x89PNGjunk"},
        ),
        (
            set_in(("players", 2, "portrait"), True),
            {
                "portraits/p3.png": swatch_png("red")
                + bytes(league_package.MAX_PORTRAIT_BYTES)
            },
        ),
    ],
    ids=[
        "version",
        "format",
        "unknown team",
        "unknown role player",
        "sheet type",
        "look range",
        "morph text",
        "kit colour",
        "path",
        "extra entry",
        "logo signature",
        "missing logo",
        "broken png",
        "large png",
    ],
)
def test_bad_packages_are_refused_when_read(
    tmp_path: Path, package_path: Path, change, files
) -> None:
    bad = rewrite(package_path, tmp_path / "bad.hc14league", change, files)
    with pytest.raises(ValidationError):
        read_package(bad)


def test_not_a_package(tmp_path: Path) -> None:
    path = tmp_path / "x.hc14league"
    path.write_bytes(b"hello")
    with pytest.raises(ValidationError):
        read_package(path)
    with pytest.raises(ValidationError):
        read_package(tmp_path / "missing.hc14league")


def league_count(connection: sqlite3.Connection) -> tuple:
    return connection.execute(
        "SELECT (SELECT COUNT(*) FROM playerDBLeague), "
        "(SELECT COUNT(*) FROM playerDBTeam), (SELECT COUNT(*) FROM playerDBPlayer)"
    ).fetchone()


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (
            set_in(("teams", 0, "outfits", 0, "texture"), "textures\\evil.dds"),
            "texture",
        ),
        (
            set_in(
                ("teams", 0, "outfits", 0, "customizations", 0, "ResourcePath0"),
                "gui\\fonts\\evil.fnt",
            ),
            "font",
        ),
        (set_in(("teams", 1, "sheet", "abbrev"), "TOO LONG"), "Team 2 (Mates)"),
        (set_in(("teams", 0, "country_id"), 999), "country"),
        (set_in(("players", 0, "skills", "sk_speed"), "150"), "Player p1"),
        (set_in(("players", 0, "data", "player_img"), "x.png"), "Player p1"),
        (in_look("skin_color", "Martian"), "look"),
        (set_in(("league", "country_id"), 999), "allowed"),
    ],
    ids=[
        "texture",
        "font",
        "abbreviation",
        "team country",
        "skill",
        "player image",
        "look value",
        "league country",
    ],
)
def test_bad_values_are_refused_and_nothing_is_written(
    tmp_path: Path,
    package_path: Path,
    other: tuple[sqlite3.Connection, PortraitStore],
    appearance_options,
    change,
    message: str,
) -> None:
    target, target_portraits = other
    before = league_count(target)
    package = read_package(rewrite(package_path, tmp_path / "bad.hc14league", change))
    with pytest.raises(ValidationError) as error:
        import_league(target, package, target_portraits, appearance_options)
    assert message in str(error.value)
    assert league_count(target) == before
    assert target_portraits.custom_ids() == []


def test_import_is_all_or_nothing(
    package_path: Path,
    other: tuple[sqlite3.Connection, PortraitStore],
    appearance_options,
) -> None:
    target, target_portraits = other
    before = league_count(target)
    target.execute("DROP TABLE Customize_table")
    with pytest.raises(sqlite3.OperationalError):
        import_league(
            target, read_package(package_path), target_portraits, appearance_options
        )
    assert league_count(target) == before
    assert target_portraits.custom_ids() == []


def test_looks_need_the_games_list(
    package_path: Path, other: tuple[sqlite3.Connection, PortraitStore]
) -> None:
    target, target_portraits = other
    before = league_count(target)
    with pytest.raises(ValidationError) as error:
        import_league(target, read_package(package_path), target_portraits, None)
    assert "looks cannot be checked" in str(error.value)
    assert league_count(target) == before


def test_empty_leagues_are_not_exported(
    tmp_path: Path, connection: sqlite3.Connection, original_db: Path
) -> None:
    league_id = LeagueRepository(connection).create_league("Empty League", 1)
    path = tmp_path / "empty.hc14league"
    with pytest.raises(ValidationError):
        export_league(connection, league_id, original_db, None, path)
    assert not path.exists()


def test_folder_entries_are_ignored(tmp_path: Path, package_path: Path) -> None:
    rezipped = rewrite(
        package_path, tmp_path / "rezipped.hc14league", files={"portraits/": b""}
    )
    assert read_package(rezipped).summary.players == 3


def test_huge_numbers_are_refused(tmp_path: Path, package_path: Path) -> None:
    path = rewrite(package_path, tmp_path / "huge.hc14league")
    with zipfile.ZipFile(path) as archive:
        entries = {name: archive.read(name) for name in archive.namelist()}
    entries[MANIFEST] = entries[MANIFEST].replace(
        b'"version": 1', b'"version": ' + b"9" * 5000
    )
    with zipfile.ZipFile(path, "w") as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
    with pytest.raises(ValidationError):
        read_package(path)


def test_images_are_written_again(tmp_path: Path, package_path: Path) -> None:
    big = Image.new("RGBA", (300, 100), "red")
    output = io.BytesIO()
    big.save(output, "DDS", pixel_format="DXT5")
    path = rewrite(
        package_path,
        tmp_path / "odd.hc14league",
        files={"logos/t1.dds": output.getvalue()},
    )
    files = read_package(path).files
    with Image.open(io.BytesIO(files["logos/t1.dds"])) as image:
        assert (image.format, image.size) == ("DDS", (512, 512))
    with Image.open(io.BytesIO(files["logos/t1_small.dds"])) as image:
        assert image.size == (48, 48)


def test_game_portraits_travel_by_reference(
    tmp_path: Path,
    connection: sqlite3.Connection,
    original_db: Path,
    portraits: PortraitStore,
    other: tuple[sqlite3.Connection, PortraitStore],
    appearance_options,
) -> None:
    """A copied player's photo is the game's own: the file only names it, and
    the importing game uses its own copy (nothing of the game is shared)."""
    teams = TeamRepository(connection)
    league_id, [(_team, copies)] = teams.create_teams(
        [NewTeam(CLUB, "Friends FC", with_players=True)],
        original_db,
        new_league=("Friends League", 1),
    )
    copy_id = copies[PLAYER_WITH_IMAGE]
    assert portraits.copy(PLAYER_WITH_IMAGE, copy_id)
    path = tmp_path / "liga.hc14league"
    export_league(connection, league_id, original_db, portraits, path)
    package = read_package(path)
    assert package.files == {}
    item = next(p for p in package.manifest["players"] if p["game_portrait"])
    assert (item["game_portrait"], item["portrait"]) == (PLAYER_WITH_IMAGE, False)

    target, bare = other
    archive = tmp_path / "their-game" / PORTRAITS_ARCHIVE
    build_portrait_archive(archive, ARCHIVE_ENTRIES)
    (tmp_path / "their-project").mkdir()
    theirs = PortraitStore(archive, tmp_path / "their-project")
    theirs.backup()
    imported = import_league(target, package, theirs, appearance_options)
    (team_id,) = [
        team_id
        for (team_id,) in target.execute(
            "SELECT id FROM playerDBTeam WHERE league_id = ?", (imported,)
        )
    ]
    new_ids = theirs.custom_ids()
    assert len(new_ids) == 1
    assert theirs.get(new_ids[0]) == theirs.original_portrait(PLAYER_WITH_IMAGE)
    assert new_ids[0] in {m.id for m in PlayerRepository(target).team_players(team_id)}
    # A game without that portrait just leaves the player without one.
    league_count_before = league_count(target)
    import_league(target, package, bare, appearance_options)
    assert league_count(target) != league_count_before
    assert bare.custom_ids() == []


def league_ids(connection: sqlite3.Connection, name: str) -> tuple[int, list, list]:
    """A league's id, its teams' ids and its players' ids."""
    (league_id,) = connection.execute(
        "SELECT id FROM playerDBLeague WHERE name = ?", (name,)
    ).fetchone()
    teams = [
        team_id
        for (team_id,) in connection.execute(
            "SELECT id FROM playerDBTeam WHERE league_id = ? ORDER BY id", (league_id,)
        )
    ]
    players = sorted(
        member.id
        for team_id in teams
        for member in PlayerRepository(connection).team_players(team_id)
    )
    return league_id, teams, players


def test_the_senders_ids_never_collide(
    connection: sqlite3.Connection,
    original_db: Path,
    package_path: Path,
    other: tuple[sqlite3.Connection, PortraitStore],
    appearance_options,
) -> None:
    """The receiver made its own league with the editor, which got the very
    ids the sender's league had. The package carries no ids (only local keys
    t1, p1...), so the import gets free ids and leaves that league alone."""
    target, target_portraits = other
    TeamRepository(target).create_teams(
        [
            NewTeam(CLUB, "Their Friends", with_players=True),
            NewTeam(OTHER_CLUB, "Their Mates"),
        ],
        original_db,
        new_league=("Their League", 1),
    )
    theirs = league_ids(target, "Their League")
    senders = league_ids(connection, "Friends League")
    assert theirs[0] == senders[0]  # the same league id,
    assert theirs[1] == senders[1]  # the same team ids,
    assert set(theirs[2]) & set(senders[2])  # and some of the same player ids
    their_snapshot = snapshot(target, "Their League")

    imported = import_league(
        target, read_package(package_path), target_portraits, appearance_options
    )
    new = league_ids(target, "Friends League")
    assert imported == new[0] != theirs[0]
    assert not set(new[1]) & set(theirs[1])
    assert not set(new[2]) & set(theirs[2])
    assert len(new[2]) == 3  # every player of the package, none lost
    assert league_ids(target, "Their League") == theirs  # theirs untouched
    assert snapshot(target, "Their League") == their_snapshot
