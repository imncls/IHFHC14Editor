"""Share a league made with the editor: export it to a file, import it back.

A package (`*.hc14league`) is a zip with a manifest.json (the league, its
teams and their players), the players' custom portraits and the teams' custom
logos. Only leagues made with the editor are exported, and an import always
makes a new editor league with new ids, so nothing of the importer's data
changes. A package is untrusted input: its structure is checked when it is
read, and every value is checked against the game's data before anything is
written; then everything is written in one transaction. See docs/game-data.md.
"""

import json
import logging
import math
import os
import re
import sqlite3
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from app.appearance import (
    CHOICE_LISTS,
    SLIDERS,
    AppearanceOptions,
    allowed_value,
)
from app.averages import GOALKEEPER, SKILLS
from app.database import name_taken
from app.errors import ValidationError
from app.leagues import LEAGUE_NAME_MAX, LeagueRepository, log_created
from app.portrait_image import logo_from_dds, portrait_from_bytes
from app.portraits import DDS_SIGNATURE, PortraitStore
from app.repository import (
    APPEARANCE_COLUMNS,
    NATIONAL_POOL,
    NATIONAL_TEAM,
    OVERALL,
    ROLE_COLUMNS,
    PlayerRepository,
)
from app.teams import SHEET_KEYS, TEXT_LIMITS, TeamRepository, unique_name

FORMAT = "hc14-league"
VERSION = 1
EXTENSION = ".hc14league"
MANIFEST = "manifest.json"
MAX_FILE_BYTES = 300 * 1024 * 1024
MAX_UNPACKED_BYTES = 300 * 1024 * 1024
MAX_ENTRIES = 5000
MAX_MANIFEST_BYTES = 5 * 1024 * 1024
MAX_PORTRAIT_BYTES = 1024 * 1024
MAX_LOGO_BYTES = 2 * 1024 * 1024
MAX_TEAMS = 64
MAX_PLAYERS = 2000
MAX_OUTFITS = 8
MAX_CUSTOMIZATIONS = 32
MAX_TEXT = 256
MAX_MORPHS = 5000
MAX_NUMBER = 1_000_000_000
_ENTRY = re.compile(r"(?:portraits/(p\d{1,5})\.png|logos/(t\d{1,3})(_small)?\.dds)")
_TEAM_KEY = re.compile(r"t\d{1,3}")
_PLAYER_KEY = re.compile(r"p\d{1,5}")
_COLOR = re.compile(r"(?:\d{1,3},){3}\d{1,3}")
_MORPHS = re.compile(r"[A-Za-z0-9_|:;.!\-]*")
# TextureCustomization columns a package may carry, by kind.
_CUSTOM_INTS = ("BaseAsset", "Type")
_CUSTOM_FLOATS = ("U1", "V1", "U2", "V2", "ZOrder")
_CUSTOM_PATHS = ("ResourcePath0", "ResourcePath1", "ResourcePath2")
_CUSTOM_COLOR = "Color"
_LOOK_COLUMNS = ", ".join(APPEARANCE_COLUMNS)
_TEAM_ITEMS = {
    "key",
    "sheet",
    "country_id",
    "annual_budget",
    "roles",
    "outfits",
    "logo",
}
_PLAYER_ITEMS = {
    "key",
    "team",
    "national_team",
    "data",
    "skills",
    "overall",
    "look",
    "portrait",
    "game_portrait",
}

activity = logging.getLogger("app.activity")


@dataclass(frozen=True)
class Summary:
    """What a package holds (or what was exported or imported)."""

    league: str
    teams: int
    players: int


@dataclass(frozen=True)
class Package:
    """A package as read: its manifest and its images by entry name."""

    manifest: dict
    files: dict[str, bytes] = field(default_factory=dict)

    @property
    def summary(self) -> Summary:
        return Summary(
            self.manifest["league"]["name"],
            len(self.manifest["teams"]),
            len(self.manifest["players"]),
        )


# Export


def export_league(
    connection: sqlite3.Connection,
    league_id: int,
    original_db: Path,
    portraits: PortraitStore | None,
    path: Path,
) -> Summary:
    """Write a league made with the editor, with its teams and players."""
    leagues = LeagueRepository(connection)
    league = leagues.get_league(league_id)
    if not leagues.is_editor_league(league_id, original_db):
        raise ValidationError("Only leagues made with the editor can be exported.")
    teams = TeamRepository(connection)
    players = PlayerRepository(connection)
    custom_portraits = set(portraits.custom_ids()) if portraits else set()
    files: dict[str, bytes] = {}
    team_items, player_items = [], []
    player_keys: dict[int, str] = {}
    team_ids = [
        team_id
        for (team_id,) in connection.execute(
            "SELECT id FROM playerDBTeam WHERE league_id = ? ORDER BY id", (league_id,)
        )
    ]
    for team_number, team_id in enumerate(team_ids, start=1):
        team_key = f"t{team_number}"
        for member in players.team_players(team_id):
            if member.id in player_keys:
                continue  # in two teams of the league: exported once
            player_key = f"p{len(player_keys) + 1}"
            player_keys[member.id] = player_key
            item = _export_player(connection, players, member.id, player_key, team_key)
            if member.id in custom_portraits:
                png = portraits.get(member.id)
                # A copied player's photo is the game's own: the file names
                # it, and the importer takes it from their own game.
                game_portrait = portraits.game_portrait_of(png)
                if game_portrait is not None:
                    item["game_portrait"] = game_portrait
                else:
                    files[f"portraits/{player_key}.png"] = png
                    item["portrait"] = True
            player_items.append(item)
        team_items.append(
            _export_team(connection, teams, team_id, team_key, player_keys)
        )
        logo = portraits.get_logo(team_id) if portraits else None
        if logo is not None:
            files[f"logos/{team_key}.dds"], files[f"logos/{team_key}_small.dds"] = logo
            team_items[-1]["logo"] = True
    manifest = {
        "format": FORMAT,
        "version": VERSION,
        "league": {"name": league.name, "country_id": league.country_id},
        "teams": team_items,
        "players": player_items,
    }
    # The same checks as an import, so a file is never written that no
    # editor would read (an empty league, too many teams or players...).
    _check_manifest(manifest)
    _write_zip(path, manifest, files)
    activity.info(
        "league %s (%s): exported to %s (%s teams, %s players)",
        league_id,
        league.name,
        path.name,
        len(team_items),
        len(player_items),
    )
    return Summary(league.name, len(team_items), len(player_items))


def _export_team(
    connection: sqlite3.Connection,
    teams: TeamRepository,
    team_id: int,
    team_key: str,
    player_keys: dict[int, str],
) -> dict:
    team = teams.get_team(team_id)
    country_id, budget = connection.execute(
        "SELECT country_id, annual_budget FROM playerDBTeam WHERE id = ?", (team_id,)
    ).fetchone()
    roles = connection.execute(
        f"SELECT {', '.join(ROLE_COLUMNS)} FROM playerDBTeam WHERE id = ?",  # noqa: S608 - ROLE_COLUMNS
        (team_id,),
    ).fetchone()
    custom_columns = [
        column
        for column in _columns(connection, "TextureCustomization")
        if column in (*_CUSTOM_INTS, *_CUSTOM_FLOATS, *_CUSTOM_PATHS, _CUSTOM_COLOR)
    ]
    outfits = []
    for outfit_id, outfit_type, texture in connection.execute(
        "SELECT id, type, texture FROM playerDBTeamOutfit WHERE team_id = ? "
        "ORDER BY type, id",
        (team_id,),
    ).fetchall():
        rows = connection.execute(
            f"SELECT {', '.join(custom_columns)} FROM TextureCustomization "  # noqa: S608 - known columns
            "WHERE TeamOutfitID = ? ORDER BY ID",
            (outfit_id,),
        ).fetchall()
        outfits.append(
            {
                "type": outfit_type,
                "texture": texture,
                "customizations": [
                    dict(zip(custom_columns, row, strict=True)) for row in rows
                ],
            }
        )
    return {
        "key": team_key,
        "sheet": {key: team[key] for key in SHEET_KEYS},
        "country_id": country_id,
        "annual_budget": budget or 0,
        "roles": {
            column: player_keys[player_id]
            for column, player_id in zip(ROLE_COLUMNS, roles, strict=True)
            if player_id in player_keys
        },
        "outfits": outfits,
        "logo": False,
    }


def _export_player(
    connection: sqlite3.Connection,
    players: PlayerRepository,
    player_id: int,
    player_key: str,
    team_key: str,
) -> dict:
    player = players.get_player(player_id)
    fields = players.fields()
    national = int(player[NATIONAL_TEAM])
    look = connection.execute(
        f"SELECT {', '.join(APPEARANCE_COLUMNS)} FROM Customize_table "  # noqa: S608 - constants
        "WHERE player_id = ?",
        (player_id,),
    ).fetchone()
    goalkeeper = player.get("main_pos") == str(GOALKEEPER)
    return {
        "key": player_key,
        "team": team_key,
        "national_team": None if national == NATIONAL_POOL else national,
        "data": {
            key: player[key] for key in fields if key != "player_img" and key in player
        },
        "skills": {key: player[key] for key in SKILLS},
        "overall": player.get(OVERALL) if goalkeeper else None,
        "look": dict(zip(APPEARANCE_COLUMNS, look, strict=True)) if look else None,
        "portrait": False,
        "game_portrait": None,
    }


def _write_zip(path: Path, manifest: dict, files: dict[str, bytes]) -> None:
    """Write the package next to its target, then replace the target."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, staging = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with (
            os.fdopen(handle, "wb") as output,
            zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive,
        ):
            archive.writestr(
                MANIFEST, json.dumps(manifest, ensure_ascii=False, indent=1)
            )
            for name, data in sorted(files.items()):
                archive.writestr(name, data)
        os.replace(staging, path)
    except BaseException:
        Path(staging).unlink(missing_ok=True)
        raise


# Reading


def read_package(path: Path) -> Package:
    """Read a package and check its structure. Nothing is written."""
    try:
        if path.stat().st_size > MAX_FILE_BYTES:
            raise _bad("The file is too large.")
        archive = zipfile.ZipFile(path)
    except (OSError, zipfile.BadZipFile):
        raise _bad("The file cannot be read as a league package.") from None
    with archive:
        infos = archive.infolist()
        if len(infos) > MAX_ENTRIES:
            raise _bad("The file has too many entries.")
        if sum(info.file_size for info in infos) > MAX_UNPACKED_BYTES:
            raise _bad("The file is too large once unpacked.")
        names = [info.filename for info in infos]
        if len(set(names)) != len(names):
            raise _bad("The file has repeated entries.")
        files: dict[str, bytes] = {}
        manifest_bytes = None
        for info in infos:
            if info.is_dir():
                continue  # a folder entry, as a zip made by hand may have
            if info.filename == MANIFEST:
                if info.file_size > MAX_MANIFEST_BYTES:
                    raise _bad("The league description is too large.")
                manifest_bytes = _read(archive, info)
            elif _ENTRY.fullmatch(info.filename):
                limit = (
                    MAX_PORTRAIT_BYTES
                    if info.filename.endswith(".png")
                    else MAX_LOGO_BYTES
                )
                if info.file_size > limit:
                    raise _bad(f"{info.filename} is too large.")
                files[info.filename] = _read(archive, info)
            else:
                raise _bad("The file has an unexpected entry.")
    if manifest_bytes is None:
        raise _bad("The file has no league description.")
    try:
        manifest = json.loads(manifest_bytes.decode("utf-8"))
    except (ValueError, RecursionError):  # also huge numbers and bad UTF-8
        raise _bad("The league description cannot be read.") from None
    _check_manifest(manifest)
    _check_files(manifest, files)
    return Package(manifest, files)


def _read(archive: zipfile.ZipFile, info: zipfile.ZipInfo) -> bytes:
    try:
        data = archive.read(info)
    except (zipfile.BadZipFile, OSError, NotImplementedError, RuntimeError):
        raise _bad("The file is damaged.") from None
    if len(data) != info.file_size:
        raise _bad("The file is damaged.")
    return data


def _check_manifest(manifest: object) -> None:
    _require(isinstance(manifest, dict), "The league description is not valid.")
    _require(
        manifest.get("format") == FORMAT and manifest.get("version") == VERSION,
        "This is not a league package this editor can read.",
    )
    _require(
        set(manifest) == {"format", "version", "league", "teams", "players"},
        "The league description has unknown parts.",
    )
    league = manifest["league"]
    _require(
        isinstance(league, dict)
        and set(league) == {"name", "country_id"}
        and _is_text(league["name"])
        and _is_int(league["country_id"]),
        "The league in the file is not valid.",
    )
    teams, players = manifest["teams"], manifest["players"]
    _require(
        isinstance(teams, list) and 0 < len(teams) <= MAX_TEAMS,
        f"A league package has 1 to {MAX_TEAMS} teams.",
    )
    _require(
        isinstance(players, list) and len(players) <= MAX_PLAYERS,
        f"A league package has at most {MAX_PLAYERS} players.",
    )
    team_keys = set()
    for team in teams:
        _check_team_item(team)
        _require(team["key"] not in team_keys, "The file repeats a team.")
        team_keys.add(team["key"])
    player_keys = set()
    for player in players:
        _check_player_item(player)
        _require(player["key"] not in player_keys, "The file repeats a player.")
        _require(player["team"] in team_keys, "A player's team is not in the file.")
        player_keys.add(player["key"])
    for team in teams:
        _require(
            all(key in player_keys for key in team["roles"].values()),
            "A team's role points to a player not in the file.",
        )


def _check_team_item(team: object) -> None:
    message = "A team in the file is not valid."
    _require(isinstance(team, dict) and set(team) == _TEAM_ITEMS, message)
    _require(isinstance(team["key"], str) and _TEAM_KEY.fullmatch(team["key"]), message)
    _require(_is_text_map(team["sheet"]), message)
    _require(_is_int(team["country_id"]) and _is_number(team["annual_budget"]), message)
    _require(isinstance(team["logo"], bool), message)
    roles = team["roles"]
    _require(
        isinstance(roles, dict)
        and all(column in ROLE_COLUMNS for column in roles)
        and all(isinstance(key, str) for key in roles.values()),
        message,
    )
    outfits = team["outfits"]
    _require(isinstance(outfits, list) and len(outfits) <= MAX_OUTFITS, message)
    for outfit in outfits:
        _require(
            isinstance(outfit, dict)
            and set(outfit) == {"type", "texture", "customizations"}
            and _is_int(outfit["type"])
            and _is_text(outfit["texture"])
            and isinstance(outfit["customizations"], list)
            and len(outfit["customizations"]) <= MAX_CUSTOMIZATIONS,
            message,
        )
        for custom in outfit["customizations"]:
            _require(isinstance(custom, dict), message)
            for column, value in custom.items():
                if column in _CUSTOM_INTS:
                    _require(_is_int(value), message)
                elif column in _CUSTOM_FLOATS:
                    _require(_is_number(value), message)
                elif column in _CUSTOM_PATHS:
                    _require(value is None or _is_text(value), message)
                elif column == _CUSTOM_COLOR:
                    _require(
                        value is None
                        or (
                            isinstance(value, str)
                            and (not value or _COLOR.fullmatch(value))
                        ),
                        message,
                    )
                else:
                    raise _bad(message)


def _check_player_item(player: object) -> None:
    message = "A player in the file is not valid."
    _require(isinstance(player, dict) and set(player) == _PLAYER_ITEMS, message)
    _require(
        isinstance(player["key"], str) and _PLAYER_KEY.fullmatch(player["key"]),
        message,
    )
    _require(isinstance(player["team"], str), message)
    _require(
        player["national_team"] is None or _is_int(player["national_team"]), message
    )
    _require(_is_text_map(player["data"]) and _is_text_map(player["skills"]), message)
    _require(player["overall"] is None or _is_text(player["overall"]), message)
    _require(isinstance(player["portrait"], bool), message)
    game_portrait = player["game_portrait"]
    _require(
        game_portrait is None or (_is_int(game_portrait) and not player["portrait"]),
        message,
    )
    look = player["look"]
    if look is not None:
        _require(
            isinstance(look, dict) and set(look) == set(APPEARANCE_COLUMNS), message
        )
        for column, value in look.items():
            if column in SLIDERS:
                _require(_is_number(value) and 0 <= value <= 1, message)
            elif column in {"single_morphs", "multi_morphs"}:
                _require(
                    value is None
                    or (
                        isinstance(value, str)
                        and len(value) <= MAX_MORPHS
                        and _MORPHS.fullmatch(value)
                    ),
                    message,
                )
            else:
                _require(value is None or _is_text(value), message)


def _check_files(manifest: dict, files: dict[str, bytes]) -> None:
    """Every image the manifest names is in the file, and no other. Each is
    decoded and written again as the editor writes them (portraits 130x158
    PNG, logos 512 and 48 DXT5 DDS), so only images the editor itself would
    make reach the game."""
    expected = set()
    for team in manifest["teams"]:
        if team["logo"]:
            expected |= {f"logos/{team['key']}.dds", f"logos/{team['key']}_small.dds"}
    for player in manifest["players"]:
        if player["portrait"]:
            expected.add(f"portraits/{player['key']}.png")
    _require(
        set(files) == expected,
        "The images in the file do not match its players and teams.",
    )
    for name in sorted(files):
        try:
            if name.startswith("portraits/"):
                files[name] = portrait_from_bytes(files[name])
            elif not name.endswith("_small.dds"):
                small = name.removesuffix(".dds") + "_small.dds"
                _require(files[small].startswith(DDS_SIGNATURE), "Not a DDS image.")
                files[name], files[small] = logo_from_dds(files[name])
        except ValidationError:
            raise _bad(f"{name} is not a valid image.") from None


# Import


def import_league(
    connection: sqlite3.Connection,
    package: Package,
    portraits: PortraitStore | None,
    appearance: AppearanceOptions | None,
    source: str = "",
) -> int:
    """Create the package's league as a new editor league (it never touches
    the game's own leagues and teams). Every value is checked first; then
    everything is written in one transaction."""
    manifest = package.manifest
    leagues = LeagueRepository(connection)
    teams = TeamRepository(connection)
    players = PlayerRepository(connection)

    league_name, country_id = leagues.check_new_league(
        _free_name(
            connection, "playerDBLeague", manifest["league"]["name"], LEAGUE_NAME_MAX
        ),
        manifest["league"]["country_id"],
    )
    countries = {country for country, _ in leagues.countries()}
    textures = _existing(connection, "playerDBTeamOutfit", "texture")
    custom_columns = set(_columns(connection, "TextureCustomization"))
    paths = {
        column: _existing(connection, "TextureCustomization", column)
        for column in _CUSTOM_PATHS
        if column in custom_columns
    }
    taken: set[str] = set()
    team_plans = []
    for number, team in enumerate(manifest["teams"], start=1):
        name = str(team["sheet"].get("name", ""))
        try:
            sheet = dict(team["sheet"])
            sheet["name"] = _free_name(
                connection, "playerDBTeam", name, TEXT_LIMITS["name"], taken
            )
            taken.add(sheet["name"].casefold())
            checked = teams.check_sheet(sheet)
            _require(team["country_id"] in countries, "Unknown country.")
            _check_outfits(team["outfits"], textures, paths, custom_columns)
        except ValidationError as error:
            raise ValidationError(f"Team {number} ({name}): {error}") from None
        team_plans.append((team, checked))

    national_ids = {team_id for team_id, _ in players.team_options(NATIONAL_TEAM)}
    player_plans = []
    for player in manifest["players"]:
        data = player["data"]
        label = f"{data.get('first_name', '')} {data.get('name', '')}".strip()
        try:
            fields = players.fields()
            # The game's goalkeepers have empty secondary positions: a new
            # player's neutral value (N/A) stands for them.
            values = {
                key: value
                for key, value in data.items()
                if value or key not in fields or fields[key].type != "LOOKUP"
            }
            values |= player["skills"]
            unknown = set(values) - set(fields) - set(SKILLS)
            _require(not unknown and "player_img" not in values, "Unknown player data.")
            _require(
                set(player["skills"]) == set(SKILLS), "The skills are not complete."
            )
            national = player["national_team"]
            if national in national_ids:
                values[NATIONAL_TEAM] = str(national)
            if player["overall"] is not None and data.get("main_pos") == str(
                GOALKEEPER
            ):
                values[OVERALL] = player["overall"]
            changes = players.check_new_player(None, values)
            # Checked, but kept as written: the game's data has dates with and
            # without leading zeros (whether that matters is untested).
            if "dob" in changes:
                changes["dob"] = values["dob"].strip()
            look = player["look"]
            if look is not None and appearance is None:
                raise _bad(
                    "The game's appearance list cannot be read, so the players' "
                    "looks cannot be checked."
                )
            if look is not None:
                for column in CHOICE_LISTS:
                    _require(
                        allowed_value(appearance, column, look[column] or ""),
                        "A look value is not one the game allows.",
                    )
        except ValidationError as error:
            raise ValidationError(
                f"Player {player['key']} ({label}): {error}"
            ) from None
        player_plans.append((player, changes))

    with connection:
        league_id = leagues.insert_league(league_name, country_id)
        team_ids: dict[str, int] = {}
        for team, sheet in team_plans:
            team_id = teams.insert_team(
                sheet, league_id, team["country_id"], float(team["annual_budget"])
            )
            _insert_outfits(connection, team_id, team["outfits"])
            team_ids[team["key"]] = team_id
        player_ids: dict[str, int] = {}
        for player, changes in player_plans:
            player_id = players.insert_new_player(team_ids[player["team"]], changes)
            if player["look"] is not None:
                look = player["look"]
                connection.execute(
                    f"INSERT INTO Customize_table (player_id, {_LOOK_COLUMNS}) "  # noqa: S608 - constants
                    f"VALUES (?{', ?' * len(APPEARANCE_COLUMNS)})",
                    (player_id, *(look[column] for column in APPEARANCE_COLUMNS)),
                )
            player_ids[player["key"]] = player_id
        for team, _sheet in team_plans:
            roles = {column: player_ids[key] for column, key in team["roles"].items()}
            if roles:
                connection.execute(
                    f"UPDATE playerDBTeam SET {', '.join(f'{c} = ?' for c in roles)} "  # noqa: S608 - ROLE_COLUMNS
                    "WHERE id = ?",
                    (*roles.values(), team_ids[team["key"]]),
                )

    log_created(league_id, league_name)
    activity.info(
        "league %s (%s): imported%s (%s teams, %s players)",
        league_id,
        league_name,
        f" from {source}" if source else "",
        len(team_ids),
        len(player_ids),
    )
    for team, sheet in team_plans:
        activity.info("team %s (%s): imported", team_ids[team["key"]], sheet["name"])
    if portraits is not None:
        for player, _changes in player_plans:
            if player["portrait"]:
                portraits.set(
                    player_ids[player["key"]],
                    package.files[f"portraits/{player['key']}.png"],
                )
            elif player["game_portrait"] is not None:
                # From this game's own portraits; none if this game lacks it.
                png = portraits.original_portrait(player["game_portrait"])
                if png is not None:
                    portraits.set(player_ids[player["key"]], png)
        for team, _sheet in team_plans:
            if team["logo"]:
                key = team["key"]
                portraits.set_logo(
                    team_ids[key],
                    package.files[f"logos/{key}.dds"],
                    package.files[f"logos/{key}_small.dds"],
                )
                teams.use_custom_logo(team_ids[key])
    return league_id


def import_name(connection: sqlite3.Connection, package: Package) -> str:
    """The name the package's league will get here."""
    return _free_name(
        connection,
        "playerDBLeague",
        package.manifest["league"]["name"],
        LEAGUE_NAME_MAX,
    )


def _check_outfits(
    outfits: list[dict],
    textures: set[str],
    paths: dict[str, set[str]],
    columns: set[str],
) -> None:
    """Kits may only use textures and fonts the game's data already uses."""
    for outfit in outfits:
        _require(outfit["texture"] in textures, "A kit uses a texture not in the game.")
        _require(0 <= outfit["type"] <= 10, "A kit is not valid.")
        for custom in outfit["customizations"]:
            for column, value in custom.items():
                _require(column in columns, "A kit is not valid.")
                if column in _CUSTOM_PATHS:
                    _require(
                        value in (None, "") or value in paths.get(column, set()),
                        "A kit uses a font not in the game.",
                    )
                elif column in _CUSTOM_INTS:
                    _require(0 <= value <= 1000, "A kit is not valid.")
                elif column in _CUSTOM_FLOATS:
                    _require(abs(value) <= 1000, "A kit is not valid.")
                elif column == _CUSTOM_COLOR and value:
                    _require(
                        all(int(part) <= 255 for part in value.split(",")),
                        "A kit colour is not valid.",
                    )


def _insert_outfits(
    connection: sqlite3.Connection, team_id: int, outfits: list
) -> None:
    for outfit in outfits:
        outfit_id = connection.execute(
            "INSERT INTO playerDBTeamOutfit (team_id, type, texture) VALUES (?, ?, ?)",
            (team_id, outfit["type"], outfit["texture"]),
        ).lastrowid
        for custom in outfit["customizations"]:
            columns = ["TeamOutfitID", *custom]
            connection.execute(
                f"INSERT INTO TextureCustomization ({', '.join(columns)}) "  # noqa: S608 - checked columns
                f"VALUES ({', '.join('?' * len(columns))})",
                (outfit_id, *custom.values()),
            )


def _free_name(
    connection: sqlite3.Connection,
    table: str,
    name: str,
    limit: int,
    taken: set[str] = frozenset(),
) -> str:
    """A name free in the table and in `taken` (see unique_name)."""
    return unique_name(
        name,
        limit,
        lambda candidate: (
            name_taken(connection, table, candidate) or candidate.casefold() in taken
        ),
    )


def _existing(connection: sqlite3.Connection, table: str, column: str) -> set[str]:
    return {
        value
        for (value,) in connection.execute(
            f"SELECT DISTINCT {column} FROM {table} WHERE {column} IS NOT NULL"  # noqa: S608 - fixed names
        )
    }


def _columns(connection: sqlite3.Connection, table: str) -> list[str]:
    return [row[1] for row in connection.execute(f"PRAGMA table_info({table})")]


def _is_int(value: object) -> bool:
    return (
        isinstance(value, int)
        and not isinstance(value, bool)
        and abs(value) <= MAX_NUMBER
    )


def _is_number(value: object) -> bool:
    return (
        (_is_int(value) or isinstance(value, float))
        and math.isfinite(value)
        and abs(value) <= MAX_NUMBER
    )


def _is_text(value: object) -> bool:
    return isinstance(value, str) and len(value) <= MAX_TEXT


def _is_text_map(value: object) -> bool:
    return (
        isinstance(value, dict)
        and len(value) <= 64
        and all(_is_text(key) and _is_text(item) for key, item in value.items())
    )


def _require(condition: object, message: str) -> None:
    if not condition:
        raise _bad(message)


def _bad(message: str) -> ValidationError:
    return ValidationError(message)
