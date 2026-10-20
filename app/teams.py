"""Read and write team data (playerDBTeam) on the decrypted copy."""

import dataclasses
import sqlite3
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from app import portraits, validation
from app.database import attached_original, name_taken
from app.errors import ValidationError
from app.leagues import LeagueRepository, log_created
from app.repository import (
    POOL_TEAM_IDS,
    ROLE_COLUMNS,
    PlayerRepository,
    activity,
)

# Kinds of team fields, each with its own widget and validation.
TEXT = "text"
ABBREV = "abbrev"
RATING = "rating"
NUMBER = "number"
CHOICE = "choice"
ROLE = "role"
COLOUR = "colour"
# Sections of the team form.
TEAM = "Team"
TACTICS = "Tactics"
VENUE = "Venue and look"
SECTIONS = (TEAM, TACTICS, VENUE)
TEXT_LIMITS = {"name": 32, "city": 30, "coach": 30, "mascot": 20}
REQUIRED_TEXTS = {"name", "coach"}
RATING_RANGE = (0.5, 5.0)
NO_ROLE = 0
NEW_TEAM_NAME = "New team"
CLUB_TYPE = "LEAGUE"
NEW_TEAM_ABBREV = "NEW"


@dataclass(frozen=True)
class TeamField:
    key: str
    label: str
    kind: str
    section: str


# The editable playerDBTeam columns: the only source of column names in SQL.
# Tactic labels follow the sliders of the game's lineup screen (attack and
# defence tabs, checked in the game); 0 is the left end, 100 the right end.
# off_focus has no slider there.
TEAM_FIELDS = (
    TeamField("name", "Name", TEXT, TEAM),
    TeamField("abbrev", "Abbreviation", ABBREV, TEAM),
    TeamField("city", "City", TEXT, TEAM),
    TeamField("coach", "Coach", TEXT, TEAM),
    TeamField("rating", "Stars", RATING, TEAM),
    TeamField("sr_captain_player_id", "Captain", ROLE, TEAM),
    TeamField("sr_shooter_player_id", "Shooter", ROLE, TEAM),
    TeamField("sr_defense_player_id", "Defence leader", ROLE, TEAM),
    TeamField("sr_tricky_player_id", "Tricky player", ROLE, TEAM),
    TeamField("sr_7m_shooter", "7 m shooter", ROLE, TEAM),
    TeamField("off_team_speed", "Build-up (patient - fast)", NUMBER, TACTICS),
    TeamField("off_team_1vs1", "Goals (from the back - 1 on 1)", NUMBER, TACTICS),
    TeamField("off_plays", "Attack (free play - set plays)", NUMBER, TACTICS),
    TeamField("off_crossing", "Screens - crossing", NUMBER, TACTICS),
    TeamField("off_passing", "Passes (short - long)", NUMBER, TACTICS),
    TeamField("off_tricks", "Play (standard - tricky)", NUMBER, TACTICS),
    TeamField("off_focus", "Attack focus (no slider)", NUMBER, TACTICS),
    TeamField("def_position", "Defence (defensive - attacking)", NUMBER, TACTICS),
    TeamField("def_helping", "Marking (man - doubling)", NUMBER, TACTICS),
    TeamField("def_aggressive", "Contact (passive - aggressive)", NUMBER, TACTICS),
    TeamField("def_holding", "Tackling (block - push)", NUMBER, TACTICS),
    TeamField("def_intercept", "Defence (safe - intercepting)", NUMBER, TACTICS),
    TeamField("home_arena", "Arena", CHOICE, VENUE),
    TeamField("floor_type_id", "Floor", CHOICE, VENUE),
    TeamField("spectators_average", "Spectators", CHOICE, VENUE),
    TeamField("home_colour", "Home colour (RRGGBB)", COLOUR, VENUE),
    TeamField("away_colour", "Away colour (RRGGBB)", COLOUR, VENUE),
    TeamField("mascot", "Mascot", TEXT, VENUE),
    TeamField("min_skill_average", "Lowest level", NUMBER, VENUE),
    TeamField("max_skill_average", "Highest level", NUMBER, VENUE),
)
FIELDS_BY_KEY = {field.key: field for field in TEAM_FIELDS}
# A team's whole sheet without the roles (they point to players).
SHEET_KEYS = tuple(field.key for field in TEAM_FIELDS if field.kind != ROLE)
# What a new team can change from the club it copies, besides its name and
# abbreviation. Roles point to players: they follow the copied players.
DETAIL_KEYS = tuple(
    field.key
    for field in TEAM_FIELDS
    if field.kind != ROLE and field.key not in {"name", "abbrev"}
)
_COLUMNS = ", ".join(field.key for field in TEAM_FIELDS)
# Choices from the game's own tables: (query, extra first option).
_CHOICE_QUERIES = {
    "home_arena": ("SELECT id, name FROM playerDBArena ORDER BY id", (0, "None")),
    "floor_type_id": ("SELECT id, name FROM playerDBFloorTypes ORDER BY id", None),
    "spectators_average": (
        "SELECT id, min_average || ' - ' || max_average "
        "FROM playerDBSpectatorAverage ORDER BY id",
        None,
    ),
}


@dataclass(frozen=True)
class NewTeam:
    """One team to create: a copy of a club, maybe with its players.

    `details` holds changes to the copied team sheet (keys from DETAIL_KEYS).
    A missing name or abbreviation is made up ("New team", from the name).
    """

    template_id: int
    name: str | None = None
    abbrev: str | None = None
    with_players: bool = False
    details: dict[str, str] = dataclasses.field(default_factory=dict)


@dataclass(frozen=True)
class _Plan:
    """A checked NewTeam, as it will be written."""

    template_id: int
    name: str
    abbrev: str
    with_players: bool
    details: dict[str, str]


class TeamRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    @property
    def connection(self) -> sqlite3.Connection:
        return self._connection

    def get_team(self, team_id: int) -> dict[str, str]:
        row = self._connection.execute(
            f"SELECT {_COLUMNS} FROM playerDBTeam WHERE id = ?",  # noqa: S608 - constants
            (team_id,),
        ).fetchone()
        if row is None:
            raise ValidationError(f"Team {team_id} does not exist.")
        return {
            field.key: _display(field, value)
            for field, value in zip(TEAM_FIELDS, row, strict=True)
        }

    def options(self, key: str, team_id: int) -> list[tuple[int, str]]:
        """The allowed (id, label) pairs of a choice or role field."""
        field = FIELDS_BY_KEY[key]
        if field.kind == CHOICE:
            query, first = _CHOICE_QUERIES[key]
            rows = self._connection.execute(query).fetchall()
            return [first, *rows] if first else rows
        if field.kind != ROLE:
            return []
        options = [(NO_ROLE, "(none)")]
        members = PlayerRepository(self._connection).team_players(team_id)
        options += [
            (player.id, f"{player.first_name} {player.name} ({player.id})".strip())
            for player in members
        ]
        # The game's data has roles pointing to players outside the team.
        (current,) = self._connection.execute(
            f"SELECT {key} FROM playerDBTeam WHERE id = ?",  # noqa: S608 - key from TEAM_FIELDS
            (team_id,),
        ).fetchone()
        if current and current not in {option[0] for option in options}:
            options.append((current, f"player {current} (not in this team)"))
        return options

    def set_team_values(self, team_id: int, changes: dict[str, str]) -> dict[str, str]:
        """Validate every change, then store them all in one transaction.

        Returns the values as written. A ValidationError names the field.
        """
        if team_id in POOL_TEAM_IDS:
            raise ValidationError("The pools cannot be edited.")
        team = self.get_team(team_id)
        values = self._checked(team_id, team, changes)
        if "name" in values:
            self._require_unique_name(values["name"], team_id)
        if not values:
            return values
        stored = {
            key: _stored(FIELDS_BY_KEY[key], value) for key, value in values.items()
        }
        assignments = ", ".join(f"{key} = ?" for key in stored)
        with self._connection:
            self._connection.execute(
                f"UPDATE playerDBTeam SET {assignments} WHERE id = ?",  # noqa: S608 - keys from TEAM_FIELDS
                (*stored.values(), team_id),
            )
        for key, value in stored.items():
            activity.info(
                "%s: %s %r -> %r",
                _describe(team_id, team),
                key,
                team[key],
                _display(FIELDS_BY_KEY[key], value),
            )
        return values

    def revert_team(self, team_id: int, original_db: Path) -> None:
        """Put back the team's editable data and logo file from the original."""
        team = self.get_team(team_id)
        with attached_original(self._connection, original_db):
            if not self._in_original(team_id):
                raise ValidationError("This team is not in the original game data.")
            with self._connection:
                self._connection.execute(
                    f"UPDATE main.playerDBTeam SET ({_COLUMNS}, logoFile) = "  # noqa: S608 - constants
                    f"(SELECT {_COLUMNS}, logoFile FROM original.playerDBTeam "
                    "WHERE id = ?) WHERE id = ?",
                    (team_id, team_id),
                )
        activity.info("%s: reverted to original", _describe(team_id, team))

    def editor_made(self, original_db: Path) -> tuple[set[int], set[int]]:
        """Ids of the teams and leagues that are not in the original data."""
        with attached_original(self._connection, original_db):
            teams = {
                team_id
                for (team_id,) in self._connection.execute(
                    "SELECT id FROM main.playerDBTeam "
                    "WHERE id NOT IN (SELECT id FROM original.playerDBTeam)"
                )
            }
            leagues = {
                league_id
                for (league_id,) in self._connection.execute(
                    "SELECT id FROM main.playerDBLeague "
                    "WHERE id NOT IN (SELECT id FROM original.playerDBLeague)"
                )
            }
        return teams, leagues

    def is_original(self, team_id: int, original_db: Path) -> bool:
        with attached_original(self._connection, original_db):
            return self._in_original(team_id)

    def check_template(self, template_id: int) -> None:
        """New teams copy a club (not a national team or a pool)."""
        if template_id in POOL_TEAM_IDS:
            raise ValidationError("The pools cannot be copied.")
        self.get_team(template_id)
        if self.team_type(template_id) != CLUB_TYPE:
            raise ValidationError("Only clubs can be copied into a new team.")

    def check_new_name(self, name: str) -> str:
        """A valid name no other team has, as it will be written."""
        name = validation.validate_label(name, TEXT_LIMITS["name"])
        self._require_unique_name(name)
        return name

    def free_name(self) -> str:
        return self._free_name(NEW_TEAM_NAME)

    def team_type(self, team_id: int) -> str:
        row = self._connection.execute(
            "SELECT type FROM playerDBTeam WHERE id = ?", (team_id,)
        ).fetchone()
        if row is None:
            raise ValidationError(f"Team {team_id} does not exist.")
        return row[0]

    def country_of(self, team_id: int) -> int:
        (country_id,) = self._connection.execute(
            "SELECT country_id FROM playerDBTeam WHERE id = ?", (team_id,)
        ).fetchone()
        return country_id

    def league_of(self, team_id: int) -> int:
        (league_id,) = self._connection.execute(
            "SELECT COALESCE(league_id, 0) FROM playerDBTeam WHERE id = ?", (team_id,)
        ).fetchone()
        return league_id

    def player_count(self, team_id: int) -> int:
        (count,) = self._connection.execute(
            "SELECT COUNT(*) FROM playerDBPlayerTeam WHERE team_id = ?", (team_id,)
        ).fetchone()
        return count

    def create_team(
        self,
        template_id: int,
        league_id: int,
        original_db: Path,
        name: str | None = None,
        abbrev: str | None = None,
    ) -> int:
        """A new, empty team copied from a club (data, kits, tactics), in a
        league made with the editor. Original leagues have fixed fixtures and
        never get new teams. The abbreviation defaults to one from the name."""
        _league, created = self.create_teams(
            [NewTeam(template_id, name, abbrev)], original_db, league_id
        )
        return created[0][0]

    def create_team_with_players(
        self,
        template_id: int,
        league_id: int,
        original_db: Path,
        name: str | None = None,
        abbrev: str | None = None,
    ) -> tuple[int, dict[int, int]]:
        """Like create_team, plus a copy of each of the club's players (same
        names, new ids) with the club's roles. A player belongs to one club, so
        the originals stay where they are. Returns the team and the new id of
        each copied player, for copying portraits."""
        _league, created = self.create_teams(
            [NewTeam(template_id, name, abbrev, with_players=True)],
            original_db,
            league_id,
        )
        return created[0]

    def create_teams(
        self,
        teams: list[NewTeam],
        original_db: Path,
        league_id: int | None = None,
        new_league: tuple[str, int] | None = None,
    ) -> tuple[int, list[tuple[int, dict[int, int]]]]:
        """Teams copied from clubs, in a league made with the editor: an
        existing one, or a new one (name, country) made with them.

        Everything is checked first, then written in one transaction. A
        ValidationError names the field and, for a team, its index (`team`).
        Returns the league and, per team, its id and its copied players.
        """
        leagues = LeagueRepository(self._connection)
        if new_league is not None:
            league_name, country_id = leagues.check_new_league(*new_league)
        elif league_id is None or not leagues.is_editor_league(league_id, original_db):
            raise ValidationError(
                "New teams go into leagues made with the editor.", "league_id"
            )
        plans: list[_Plan] = []
        taken: set[str] = set()
        for index, team in enumerate(teams):
            try:
                plans.append(self._plan(team, taken))
            except ValidationError as error:
                message = f"Team {index + 1}: {error}" if len(teams) > 1 else str(error)
                raise ValidationError(message, error.field, index) from None
        with self._connection:
            if new_league is not None:
                league_id = leagues.insert_league(league_name, country_id)
            created = [self._insert_team(plan, league_id) for plan in plans]
        if new_league is not None:
            log_created(league_id, league_name)
        for plan, (team_id, copies) in zip(plans, created, strict=True):
            described = _describe(team_id, {"name": plan.name})
            activity.info(
                "%s: created as a copy of team %s in league %s",
                described,
                plan.template_id,
                league_id,
            )
            for key, value in plan.details.items():
                activity.info("%s: %s %r", described, key, value)
            for source_id, player_id in copies.items():
                activity.info(
                    "player %s: created as a copy of player %s in %s",
                    player_id,
                    source_id,
                    described,
                )
        return league_id, created

    def check_sheet(self, sheet: dict[str, str]) -> dict[str, str]:
        """Validate a whole team sheet (SHEET_KEYS, as get_team shows them),
        for a team to import. A ValidationError names the field."""
        for key in SHEET_KEYS:
            if key not in sheet:
                raise ValidationError("A team field is missing.", key)
        for key in sheet:
            if key not in SHEET_KEYS:
                raise ValidationError("Unknown or non-editable team field.", key)
        return self._checked(0, sheet, sheet)

    def insert_team(
        self,
        sheet: dict[str, str],
        league_id: int,
        country_id: int,
        annual_budget: float,
    ) -> int:
        """Insert a club from a checked sheet (see check_sheet), without
        players, roles, kits or logo. It does not commit: the caller's
        transaction."""
        columns = {
            "type": CLUB_TYPE,
            "league_id": league_id,
            "country_id": country_id,
            "annual_budget": annual_budget,
            **{key: _stored(FIELDS_BY_KEY[key], value) for key, value in sheet.items()},
            **dict.fromkeys(ROLE_COLUMNS, NO_ROLE),
        }
        team_id = self._connection.execute(
            f"INSERT INTO playerDBTeam ({', '.join(columns)}) "  # noqa: S608 - keys from TEAM_FIELDS
            f"VALUES ({', '.join('?' * len(columns))})",
            tuple(columns.values()),
        ).lastrowid
        self._connection.execute(
            "INSERT INTO playerDBTeamAverages (team_id, skill_average) VALUES (?, 0)",
            (team_id,),
        )
        return team_id

    def _plan(self, team: NewTeam, taken: set[str]) -> _Plan:
        """Check one new team; `taken` holds the names of the batch so far."""
        self.check_template(team.template_id)
        if team.name is None:
            name = self._free_name(NEW_TEAM_NAME, taken)
        else:
            name = self.check_new_name(team.name)
            if name.casefold() in taken:
                raise ValidationError("Another new team has this name.", "name")
        taken.add(name.casefold())
        try:
            abbrev = validation.validate_abbrev(
                suggest_abbrev(name) if team.abbrev is None else team.abbrev
            )
        except ValidationError as error:
            raise ValidationError(str(error), "abbrev") from None
        for key in team.details:
            if key not in DETAIL_KEYS:
                raise ValidationError("Unknown or non-editable team field.", key)
        template = self.get_team(team.template_id)
        details = self._checked(team.template_id, template, team.details)
        return _Plan(team.template_id, name, abbrev, team.with_players, details)

    def _insert_team(self, plan: _Plan, league_id: int) -> tuple[int, dict[int, int]]:
        """Write one checked team. It does not commit: the caller's transaction."""
        columns = ", ".join(
            f'"{column}"' for column in self._columns("playerDBTeam") if column != "id"
        )
        custom = ", ".join(
            f'"{column}"'
            for column in self._columns("TextureCustomization")
            if column not in {"ID", "TeamOutfitID"}
        )
        roles = ", ".join(f"{column} = 0" for column in ROLE_COLUMNS)
        team_id = self._connection.execute(
            f"INSERT INTO playerDBTeam ({columns}) "  # noqa: S608 - columns from the schema
            f"SELECT {columns} FROM playerDBTeam WHERE id = ?",
            (plan.template_id,),
        ).lastrowid
        # No players yet, so no roles; the game shows its "no logo" logo.
        self._connection.execute(
            f"UPDATE playerDBTeam SET name = ?, abbrev = ?, logoFile = NULL, "  # noqa: S608 - ROLE_COLUMNS
            f"league_id = ?, {roles} WHERE id = ?",
            (plan.name, plan.abbrev, league_id, team_id),
        )
        if plan.details:
            stored = {
                key: _stored(FIELDS_BY_KEY[key], value)
                for key, value in plan.details.items()
            }
            assignments = ", ".join(f"{key} = ?" for key in stored)
            self._connection.execute(
                f"UPDATE playerDBTeam SET {assignments} WHERE id = ?",  # noqa: S608 - keys from DETAIL_KEYS
                (*stored.values(), team_id),
            )
        outfits = self._connection.execute(
            "SELECT id, type, texture FROM playerDBTeamOutfit WHERE team_id = ? "
            "ORDER BY type, id",
            (plan.template_id,),
        ).fetchall()
        for outfit_id, outfit_type, texture in outfits:
            new_outfit = self._connection.execute(
                "INSERT INTO playerDBTeamOutfit (team_id, type, texture) "
                "VALUES (?, ?, ?)",
                (team_id, outfit_type, texture),
            ).lastrowid
            # Numbers and names printed on the shirt belong to the outfit.
            self._connection.execute(
                f"INSERT INTO TextureCustomization (TeamOutfitID, {custom}) "  # noqa: S608 - columns from the schema
                f"SELECT ?, {custom} FROM TextureCustomization "
                "WHERE TeamOutfitID = ? ORDER BY ID",
                (new_outfit, outfit_id),
            )
        self._connection.execute(
            "INSERT INTO playerDBTeamAverages (team_id, skill_average) VALUES (?, 0)",
            (team_id,),
        )
        copies: dict[int, int] = {}
        if plan.with_players:
            copies = self._copy_players(plan.template_id, team_id)
        return team_id, copies

    def _copy_players(self, template_id: int, team_id: int) -> dict[int, int]:
        """Copy the template's players into the team and give the copies the
        template's roles. Runs inside the caller's transaction."""
        players = PlayerRepository(self._connection)
        members = sorted(member.id for member in players.team_players(template_id))
        copies = players.copy_players(members, team_id)
        roles = self._connection.execute(
            f"SELECT {', '.join(ROLE_COLUMNS)} FROM playerDBTeam WHERE id = ?",  # noqa: S608 - ROLE_COLUMNS
            (template_id,),
        ).fetchone()
        assignments = ", ".join(f"{column} = ?" for column in ROLE_COLUMNS)
        self._connection.execute(
            f"UPDATE playerDBTeam SET {assignments} WHERE id = ?",  # noqa: S608 - ROLE_COLUMNS
            (*(copies.get(role, 0) for role in roles), team_id),
        )
        return copies

    def move_team(self, team_id: int, league_id: int, original_db: Path) -> None:
        """Put a team made with the editor into another editor league."""
        team = self.get_team(team_id)
        leagues = LeagueRepository(self._connection)
        if self.is_original(team_id, original_db):
            raise ValidationError("Original teams stay in their league.", "league_id")
        if not leagues.is_editor_league(league_id, original_db):
            raise ValidationError(
                "Teams made with the editor go into leagues made with the editor.",
                "league_id",
            )
        old = self.league_of(team_id)
        if old == league_id:
            return
        with self._connection:
            self._connection.execute(
                "UPDATE playerDBTeam SET league_id = ? WHERE id = ?",
                (league_id, team_id),
            )
        activity.info("%s: league %s -> %s", _describe(team_id, team), old, league_id)

    def delete_team(self, team_id: int, original_db: Path) -> None:
        """Delete a team made with the editor. Its players go to the pool."""
        team = self.get_team(team_id)
        if self.is_original(team_id, original_db):
            raise ValidationError("Only teams created with the editor can be deleted.")
        # One transaction: the players leave and the team goes, or nothing does.
        with self._connection:
            moved = PlayerRepository(self._connection).release_team(team_id)
            self._connection.execute(
                "DELETE FROM TextureCustomization WHERE TeamOutfitID IN "
                "(SELECT id FROM playerDBTeamOutfit WHERE team_id = ?)",
                (team_id,),
            )
            for table, column in (
                ("playerDBTeamOutfit", "team_id"),
                ("playerDBTeamAverages", "team_id"),
                ("playerDBPlayerTeam", "team_id"),
                ("playerDBTeam", "id"),
            ):
                self._connection.execute(
                    f"DELETE FROM {table} WHERE {column} = ?",  # noqa: S608 - constants
                    (team_id,),
                )
        for player_id in moved:
            activity.info(
                "player %s: moved to the pool from team %s", player_id, team_id
            )
        activity.info("%s: deleted", _describe(team_id, team))

    def logo_file(self, team_id: int) -> str | None:
        """The team's playerDBTeam.logoFile (None: the game's "no logo")."""
        row = self._connection.execute(
            "SELECT logoFile FROM playerDBTeam WHERE id = ?", (team_id,)
        ).fetchone()
        if row is None:
            raise ValidationError(f"Team {team_id} does not exist.")
        return row[0]

    def use_custom_logo(self, team_id: int) -> None:
        """Point the team to its custom logo (see app/portraits.py)."""
        team = self.get_team(team_id)
        with self._connection:
            self._connection.execute(
                "UPDATE playerDBTeam SET logoFile = ? WHERE id = ?",
                (portraits.logo_file(team_id), team_id),
            )
        activity.info("%s: custom logo", _describe(team_id, team))

    def restore_logo_file(self, team_id: int, original_db: Path) -> None:
        """The original logoFile again (none for a team made with the editor)."""
        team = self.get_team(team_id)
        with attached_original(self._connection, original_db), self._connection:
            self._connection.execute(
                "UPDATE main.playerDBTeam SET logoFile = "
                "(SELECT logoFile FROM original.playerDBTeam WHERE id = ?) "
                "WHERE id = ?",
                (team_id, team_id),
            )
        activity.info("%s: original logo", _describe(team_id, team))

    def _in_original(self, team_id: int) -> bool:
        row = self._connection.execute(
            "SELECT 1 FROM original.playerDBTeam WHERE id = ?", (team_id,)
        ).fetchone()
        return row is not None

    def _columns(self, table: str) -> list[str]:
        return [
            row[1]
            for row in self._connection.execute(f"PRAGMA main.table_info({table})")
            if '"' not in row[1]
        ]

    def _free_name(self, base: str, taken: set[str] = frozenset()) -> str:
        """A name free in the data and in `taken` (casefolded names of other
        new teams), from the base name (see unique_name)."""
        return unique_name(
            base,
            TEXT_LIMITS["name"],
            lambda name: self._name_taken(name) or name.casefold() in taken,
        )

    def _name_taken(self, name: str, team_id: int | None = None) -> bool:
        return name_taken(self._connection, "playerDBTeam", name, team_id)

    def _require_unique_name(self, name: str, team_id: int | None = None) -> None:
        if self._name_taken(name, team_id):
            raise ValidationError("Another team already has this name.", "name")

    def _checked(
        self, team_id: int, team: dict[str, str], changes: dict[str, str]
    ) -> dict[str, str]:
        """Validate changes to a team's fields. A ValidationError names the field."""
        values = {}
        for key, raw in changes.items():
            try:
                values[key] = self._validate(team_id, key, raw)
            except ValidationError as error:
                raise ValidationError(str(error), key) from None
        low = int(values.get("min_skill_average", team["min_skill_average"]) or 0)
        high = int(values.get("max_skill_average", team["max_skill_average"]) or 0)
        if low > high:
            raise ValidationError(
                "The lowest level cannot be above the highest.", "min_skill_average"
            )
        return values

    def _validate(self, team_id: int, key: str, raw: str) -> str:
        field = FIELDS_BY_KEY.get(key)
        if field is None:
            raise ValidationError("Unknown or non-editable team field.")
        if field.kind == TEXT:
            return validation.validate_label(
                raw, TEXT_LIMITS[key], key in REQUIRED_TEXTS
            )
        if field.kind == ABBREV:
            return validation.validate_abbrev(raw)
        if field.kind == RATING:
            return validation.validate_rating(raw)
        if field.kind == NUMBER:
            return validation.validate_int(key, raw)
        if field.kind == COLOUR:
            return validation.validate_colour(raw)
        ids = {option_id for option_id, _ in self.options(key, team_id)}
        return validation.validate_choice(raw, ids)


def unique_name(base: str, limit: int, is_taken: Callable[[str], bool]) -> str:
    """The base name, else "base (2)", "base (3)"...: the first one not taken,
    cut so it stays within `limit` characters."""
    base = base.strip()
    candidate, number = base, 1
    while is_taken(candidate):
        number += 1
        suffix = f" ({number})"
        candidate = base[: limit - len(suffix)].rstrip() + suffix
    return candidate


def suggest_abbrev(name: str) -> str:
    """Three letters for the scoreboard: "Friends FC" -> "FRI".

    The first word with at least 3 letters or digits, else all of them,
    without accents; "NEW" if the name has none.
    """
    plain = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    words = ["".join(c for c in word if c.isalnum()) for word in plain.split()]
    size = len(NEW_TEAM_ABBREV)
    word = next((w for w in words if len(w) >= size), "".join(words))
    return word[:size].upper() or NEW_TEAM_ABBREV


def _stored(field: TeamField, value: str) -> object:
    """The validated value with the column's type."""
    if field.kind in {TEXT, ABBREV}:
        return value
    if field.kind == RATING:
        return float(value)
    return int(value)


def _display(field: TeamField, value: object) -> str:
    """A stored value as the form shows it."""
    if value is None:
        return ""
    if field.kind == COLOUR:
        return f"{int(value):06X}" if value else ""
    if field.kind == RATING:
        return f"{float(value):.1f}"
    return str(value)


def _describe(team_id: int, team: dict[str, str]) -> str:
    return f"team {team_id} ({team['name']})"
