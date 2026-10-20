"""Read and write player data and skills on the decrypted copy."""

import json
import logging
import sqlite3
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from app import validation
from app.averages import AVERAGES, GOALKEEPER, SKILLS, player_averages, stored_float
from app.database import attached_original
from app.errors import ValidationError

# body, hair_colour, hair_style, eye_colour and skin_colour: unused by the game.
EXCLUDED_FIELD_IDS = range(13, 18)
SEARCH_LIMIT = 50
# Name and first name: the game showed 38 characters without trouble (the
# longest in its data is 19); from about 20 they overlap on the player card.
NAME_MAX_LENGTH = 40
NAME_LONG_ON_CARD = 20
# "Not assigned" (clubs) and "NOT ASSIGNABLE" (national teams).
CLUB_POOL = 194
NATIONAL_POOL = 319
POOL_TEAM_IDS = {CLUB_POOL, NATIONAL_POOL}
# Virtual player keys for team membership: team type, pool and pool label.
CLUB = "club"
NATIONAL_TEAM = "national_team"
TEAM_KINDS = {
    CLUB: ("LEAGUE", CLUB_POOL, "No club (pool)"),
    NATIONAL_TEAM: ("NATIONAL_TEAM", NATIONAL_POOL, "No national team (pool)"),
}
# playerDBTeam columns that point to a player (captain, shooter, ...).
ROLE_COLUMNS = (
    "sr_captain_player_id",
    "sr_shooter_player_id",
    "sr_defense_player_id",
    "sr_tricky_player_id",
    "sr_7m_shooter",
)
# Real rosters have 4 to 25 players; outside 14-25 the editor only warns.
ROSTER_RANGE = (14, 25)
UNUSED_TEAM_MARKERS = ("(Original)", "(Fake)", "(not in use)")
NATIONAL_TEAMS_GROUP = "National teams"
# Hand-set average of a goalkeeper, written to the three averages.
OVERALL = "overall"
# Values of a player made with "New player": valid and typical of the game's
# data. The jersey number and the country come from his team.
NEW_PLAYER = {
    "name": "Player",
    "first_name": "New",
    "dob": "1.1.2000",
    "main_pos": "24",  # centre back
    "sec_off_pos": "21",  # N/A
    "sec_def_pos": "33",  # N/A
    "throw_arm": "9",  # right
    "star_pot": "50",
    "prone_to_inj": "50",
    "weight": "90",
    "height": "190",
    "national_team_jersey_nr": "",
}
NEW_PLAYER_SKILL = 65  # the game's default skill value
DEFAULT_COUNTRY = 195  # Spain, for a player created without a team
JERSEY_NUMBERS = range(1, 100)
# Customize_table: the player's 3D look (copied by "Duplicate").
APPEARANCE_COLUMNS = (
    "muscularity",
    "skin_brightness",
    "skin_color",
    "hairstyle",
    "hairstyle_color",
    "body_hairstyle",
    "beard",
    "beard_color",
    "brow",
    "brow_color",
    "eye_color",
    "single_morphs",
    "multi_morphs",
)
_SKILL_COLUMNS = ", ".join(SKILLS)
_AVERAGE_COLUMNS = ", ".join(AVERAGES)
_APPEARANCE_COLUMNS = ", ".join(APPEARANCE_COLUMNS)

activity = logging.getLogger("app.activity")


@dataclass(frozen=True)
class Field:
    id: int
    key: str
    description: str
    type: str
    lookup_type_id: int | None


@dataclass(frozen=True)
class PlayerSummary:
    id: int
    first_name: str
    name: str


@dataclass(frozen=True)
class Team:
    id: int
    name: str
    group: str
    player_count: int

    @property
    def is_pool(self) -> bool:
        return self.id in POOL_TEAM_IDS

    @property
    def is_unused(self) -> bool:
        """Teams hidden by default: empty, test or retired teams."""
        if self.is_pool:
            return False
        return (
            self.player_count == 0
            or any(marker in self.name for marker in UNUSED_TEAM_MARKERS)
            or self.name.startswith("Random_")
        )


@dataclass(frozen=True)
class RosterEntry:
    id: int
    jersey: str
    first_name: str
    name: str
    position: str
    average: str
    club: str = ""


class PlayerRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def fields(self) -> dict[str, Field]:
        rows = self._connection.execute(
            "SELECT id, key, description, type, lookup_type_id FROM playerDBData "
            "ORDER BY id"
        )
        return {
            key: Field(field_id, key, description or key, field_type, lookup_id)
            for field_id, key, description, field_type, lookup_id in rows
            if field_id not in EXCLUDED_FIELD_IDS
        }

    def options(self, field: Field) -> list[tuple[int, str]]:
        """The allowed (id, label) pairs of a lookup or country field."""
        if field.type == "LOOKUP" and field.lookup_type_id:
            return self._connection.execute(
                "SELECT id, value FROM playerDBLookupTable "
                "WHERE lookup_type_id = ? ORDER BY id",
                (field.lookup_type_id,),
            ).fetchall()
        if field.type == "FOREIGN_KEY" and field.key == "country":
            return self._connection.execute(
                "SELECT ID, Name FROM Country ORDER BY Name"
            ).fetchall()
        return []

    def teams(self) -> list[Team]:
        rows = self._connection.execute(
            "SELECT t.id, t.name, t.type, COALESCE(l.name, ''), "
            "COUNT(pt.player_id) FROM playerDBTeam t "
            "LEFT JOIN playerDBLeague l ON l.id = t.league_id "
            "LEFT JOIN playerDBPlayerTeam pt ON pt.team_id = t.id "
            "GROUP BY t.id ORDER BY t.name"
        )
        return [
            Team(
                team_id,
                name,
                NATIONAL_TEAMS_GROUP if team_type == "NATIONAL_TEAM" else league,
                count,
            )
            for team_id, name, team_type, league, count in rows
        ]

    def team_players(self, team_id: int) -> list[RosterEntry]:
        return self._roster(
            "SELECT player_id FROM playerDBPlayerTeam WHERE team_id = ?",
            (team_id,),
            "CAST(j.value AS INTEGER), n.value",
        )

    def search_players(self, text: str) -> list[RosterEntry]:
        """Players whose first name and name contain every word of the text.

        Case and accents are ignored: "raul entrerrios" finds "Raúl Entrerríos".
        """
        words = _fold(text).split()
        if not words:
            return []
        names: dict[int, list[str]] = {}
        for player_id, value in self._connection.execute(
            "SELECT player_id, value FROM playerDBPlayerData WHERE data_id IN "
            "(SELECT id FROM playerDBData WHERE key IN ('first_name', 'name'))"
        ):
            names.setdefault(player_id, []).append(_fold(value))
        found = [
            player_id
            for player_id, parts in names.items()
            if all(word in " ".join(parts) for word in words)
        ][:SEARCH_LIMIT]
        return self._roster(
            "SELECT value FROM json_each(?)",
            (json.dumps(found),),
            "n.value, f.value",
        )

    def _roster(self, chosen: str, params: tuple, order: str) -> list[RosterEntry]:
        """Roster rows of the players the `chosen` query returns."""
        rows = self._connection.execute(
            f"WITH chosen(player_id) AS ({chosen}) "  # noqa: S608 - constant SQL
            "SELECT c.player_id, COALESCE(j.value, ''), COALESCE(f.value, ''), "
            "COALESCE(n.value, ''), COALESCE(l.value, ''), "
            "COALESCE(printf('%.2f', s.total_avg), ''), COALESCE(("
            "SELECT t.name FROM playerDBPlayerTeam pt "
            "JOIN playerDBTeam t ON t.id = pt.team_id "
            "WHERE pt.player_id = c.player_id AND t.type = 'LEAGUE' "
            f"ORDER BY t.id = {CLUB_POOL}, t.id LIMIT 1), '') "
            "FROM chosen c "
            "LEFT JOIN playerDBPlayer s ON s.id = c.player_id "
            "LEFT JOIN playerDBPlayerData n ON n.player_id = c.player_id "
            "AND n.data_id = (SELECT id FROM playerDBData WHERE key = 'name') "
            "LEFT JOIN playerDBPlayerData f ON f.player_id = c.player_id "
            "AND f.data_id = (SELECT id FROM playerDBData WHERE key = 'first_name') "
            "LEFT JOIN playerDBPlayerData j ON j.player_id = c.player_id "
            "AND j.data_id = (SELECT id FROM playerDBData WHERE key = 'jersey_nr') "
            "LEFT JOIN playerDBPlayerData p ON p.player_id = c.player_id "
            "AND p.data_id = (SELECT id FROM playerDBData WHERE key = 'main_pos') "
            "LEFT JOIN playerDBLookupTable l ON l.id = p.value "
            f"ORDER BY {order}",
            params,
        )
        return [RosterEntry(*row) for row in rows]

    def get_player(self, player_id: int) -> dict[str, str]:
        rows = self._connection.execute(
            "SELECT d.key, p.value FROM playerDBPlayerData p "
            "JOIN playerDBData d ON d.id = p.data_id "
            "WHERE p.player_id = ? ORDER BY d.id",
            (player_id,),
        ).fetchall()
        if not rows:
            raise ValidationError(f"Player {player_id} does not exist.")
        player = dict(rows)
        skills = self._connection.execute(
            f"SELECT {_SKILL_COLUMNS}, {_AVERAGE_COLUMNS} "  # noqa: S608 - constants
            "FROM playerDBPlayer WHERE id = ?",
            (player_id,),
        ).fetchone()
        if skills is not None:
            player.update(zip(SKILLS, map(str, skills[: len(SKILLS)]), strict=True))
            averages = [f"{value:.2f}" for value in skills[len(SKILLS) :]]
            player.update(zip(AVERAGES, averages, strict=True))
            player[OVERALL] = averages[0]
        for key, (team_type, pool, _label) in TEAM_KINDS.items():
            # A real team wins over the pool if the data has both.
            teams = sorted(
                self._player_teams(player_id, team_type), key=lambda t: (t == pool, t)
            )
            player[key] = str(teams[0] if teams else pool)
        return player

    def team_fields(self) -> list[Field]:
        """The team memberships, as fields the form can show."""
        return [
            Field(0, key, key.replace("_", " "), "TEAM", None) for key in TEAM_KINDS
        ]

    def team_options(self, kind: str) -> list[tuple[int, str]]:
        """Teams a player can belong to for this kind, the pool first."""
        team_type, pool, pool_label = TEAM_KINDS[kind]
        rows = self._connection.execute(
            "SELECT t.id, t.name, COUNT(pt.player_id) FROM playerDBTeam t "
            "LEFT JOIN playerDBPlayerTeam pt ON pt.team_id = t.id "
            "WHERE t.type = ? GROUP BY t.id ORDER BY t.name",
            (team_type,),
        ).fetchall()
        options = [(pool, pool_label)] if any(row[0] == pool for row in rows) else []
        for team_id, name, count in rows:
            if team_id == pool:
                continue
            unused = Team(team_id, name, "", count).is_unused
            options.append((team_id, f"{name} [unused]" if unused else name))
        return options

    def team_warnings(self, team_id: int, player_id: int) -> list[str]:
        """Unusual but allowed situations after a player joins a team."""
        if team_id in POOL_TEAM_IDS:
            return []
        name = self._team_name(team_id)
        (count,) = self._connection.execute(
            "SELECT COUNT(*) FROM playerDBPlayerTeam WHERE team_id = ?", (team_id,)
        ).fetchone()
        warnings = []
        low, high = ROSTER_RANGE
        if not low <= count <= high:
            warnings.append(
                f"{name} has {count} players (real rosters have {low} to {high})."
            )
        (same_number,) = self._connection.execute(
            "SELECT COUNT(*) FROM playerDBPlayerTeam pt "
            "JOIN playerDBPlayerData j ON j.player_id = pt.player_id "
            "AND j.data_id = (SELECT id FROM playerDBData WHERE key = 'jersey_nr') "
            "WHERE pt.team_id = ? AND pt.player_id <> ? AND j.value = "
            "(SELECT value FROM playerDBPlayerData WHERE player_id = ? "
            "AND data_id = (SELECT id FROM playerDBData WHERE key = 'jersey_nr'))",
            (team_id, player_id, player_id),
        ).fetchone()
        if same_number:
            warnings.append(f"Another player of {name} has the same jersey number.")
        return warnings

    def find_players(self, text: str) -> list[PlayerSummary]:
        """Id and names of the players `search_players` finds."""
        return [
            PlayerSummary(player.id, player.first_name, player.name)
            for player in self.search_players(text)
        ]

    def set_value(self, player_id: int, key: str, raw: str) -> str:
        """Validate and store one field. Returns the value as written."""
        return self.set_values(player_id, {key: raw})[key]

    def set_values(self, player_id: int, changes: dict[str, str]) -> dict[str, str]:
        """Validate every change, then store them all in one transaction.

        Returns the values as written. A ValidationError names the field.
        """
        player = self.get_player(player_id)
        values = self._checked(player, changes)
        with self._connection:
            self._store(player_id, player, values)
        for key, value in values.items():
            old, new = player.get(key, ""), value
            if key in TEAM_KINDS:
                old = f"{self._team_name(int(old))} ({old})"
                new = f"{self._team_name(int(value))} ({value})"
            activity.info(
                "%s: %s %r -> %r", _describe(player_id, player), key, old, new
            )
        return values

    def revert_player(self, player_id: int, original_db: Path) -> None:
        """Put back the player's data, skills and averages from the original."""
        player = self.get_player(player_id)
        with attached_original(self._connection, original_db):
            exists = self._connection.execute(
                "SELECT 1 FROM original.playerDBPlayerData WHERE player_id = ?",
                (player_id,),
            ).fetchone()
            if exists is None:
                raise ValidationError("This player is not in the original game data.")
            with self._connection:
                self._connection.execute(
                    "DELETE FROM main.playerDBPlayerData WHERE player_id = ?",
                    (player_id,),
                )
                self._connection.execute(
                    "INSERT INTO main.playerDBPlayerData (player_id, data_id, value) "
                    "SELECT player_id, data_id, value "
                    "FROM original.playerDBPlayerData WHERE player_id = ?",
                    (player_id,),
                )
                self._connection.execute(
                    "DELETE FROM main.playerDBPlayer WHERE id = ?", (player_id,)
                )
                self._connection.execute(
                    "INSERT INTO main.playerDBPlayer "
                    "SELECT * FROM original.playerDBPlayer WHERE id = ?",
                    (player_id,),
                )
                # The 3D look too (or none, if the original had none).
                self._connection.execute(
                    "DELETE FROM main.Customize_table WHERE player_id = ?",
                    (player_id,),
                )
                self._connection.execute(
                    "INSERT INTO main.Customize_table "
                    "SELECT * FROM original.Customize_table WHERE player_id = ?",
                    (player_id,),
                )
                teams = self._player_teams(player_id)
                self._restore_memberships(player_id)
                self._update_team_averages(teams | self._player_teams(player_id))
        activity.info("%s: reverted to original", _describe(player_id, player))

    def new_player_values(self, team_id: int | None = None) -> dict[str, str]:
        """What a new player in this team (or the pools) starts with."""
        teams = self._placement(team_id)
        values = {
            **NEW_PLAYER,
            "jersey_nr": str(self._free_jersey(team_id)),
            "country": str(self._team_country(team_id)),
            **{key: str(NEW_PLAYER_SKILL) for key in SKILLS},
        }
        # _placement swaps the pool of the team's kind for the team itself.
        for key, (_team_type, pool, _label) in TEAM_KINDS.items():
            values[key] = str(pool if pool in teams else team_id)
        return values

    def create_player(
        self, team_id: int | None = None, values: dict[str, str] | None = None
    ) -> int:
        """A new player in this team (or the pools): neutral values, plus the
        given ones (data, skills, club, national team). Everything is checked
        before anything is written, then the player is created in one
        transaction."""
        changes = self.check_new_player(team_id, values or {})
        with self._connection:
            player_id = self.insert_new_player(team_id, changes)
        activity.info(
            "%s: created in %s",
            _describe(player_id, self.get_player(player_id)),
            self._team_names(self._player_teams(player_id)),
        )
        return player_id

    def check_new_player(
        self, team_id: int | None, values: dict[str, str]
    ) -> dict[str, str]:
        """Validate the values of a new player in this team (or the pools)."""
        return self._checked(self.new_player_values(team_id), values)

    def insert_new_player(self, team_id: int | None, changes: dict[str, str]) -> int:
        """Insert a player with checked values (see check_new_player). It does
        not commit or log: the caller's transaction."""
        teams = self._placement(team_id)
        fields = self.fields()
        player_id = self._next_player_id()
        start = self.new_player_values(team_id)
        values = {key: value for key, value in start.items() if key in NEW_PLAYER}
        values |= {"jersey_nr": start["jersey_nr"], "country": start["country"]}
        skills = dict.fromkeys(SKILLS, NEW_PLAYER_SKILL)
        averages = player_averages(skills, int(values["main_pos"]))
        placeholders = ", ".join("?" * (1 + len(SKILLS) + len(AVERAGES)))
        self._connection.executemany(
            "INSERT INTO playerDBPlayerData (player_id, data_id, value) "
            "VALUES (?, ?, ?)",
            [
                (player_id, fields[key].id, value)
                for key, value in values.items()
                if key in fields
            ],
        )
        self._connection.execute(
            f"INSERT INTO playerDBPlayer (id, {_SKILL_COLUMNS}, "  # noqa: S608 - constants
            f"{_AVERAGE_COLUMNS}) VALUES ({placeholders})",
            (player_id, *skills.values(), *averages),
        )
        self._join(player_id, teams)
        if changes:
            self._store(player_id, self.get_player(player_id), changes)
        return player_id

    def duplicate_player(self, source_id: int, team_id: int | None = None) -> int:
        """A copy of a player (data, skills and 3D look) with a new id."""
        source = self.get_player(source_id)
        teams = self._placement(team_id)
        player_id = self._next_player_id()
        with self._connection:
            self._copy(source_id, player_id)
            self._join(player_id, teams)
        activity.info(
            "%s: created as a copy of player %s in %s",
            _describe(player_id, source),
            source_id,
            self._team_names(teams),
        )
        return player_id

    def copy_players(self, source_ids: list[int], team_id: int) -> dict[int, int]:
        """Copies of these players (like duplicate_player) in a team.

        It does not commit or log: the caller runs it inside its own
        transaction. Returns the new id of each source player.
        """
        teams = self._placement(team_id)
        copies: dict[int, int] = {}
        for source_id in source_ids:
            player_id = self._next_player_id()
            self._copy(source_id, player_id)
            self._connection.executemany(
                "INSERT INTO playerDBPlayerTeam (player_id, team_id) VALUES (?, ?)",
                [(player_id, team) for team in sorted(teams)],
            )
            copies[source_id] = player_id
        self._update_team_averages(teams)
        return copies

    def is_original(self, player_id: int, original_db: Path) -> bool:
        """Whether the player is in the original game data."""
        with attached_original(self._connection, original_db):
            row = self._connection.execute(
                "SELECT 1 FROM original.playerDBPlayerData WHERE player_id = ? "
                "UNION SELECT 1 FROM original.playerDBPlayer WHERE id = ?",
                (player_id, player_id),
            ).fetchone()
        return row is not None

    def delete_player(self, player_id: int, original_db: Path) -> None:
        """Delete a player created with the editor, with his teams and roles."""
        player = self.get_player(player_id)
        if self.is_original(player_id, original_db):
            raise ValidationError(
                "Only players created with the editor can be deleted."
            )
        teams = self._player_teams(player_id)
        with self._connection:
            for table, column in (
                ("playerDBPlayerData", "player_id"),
                ("playerDBPlayer", "id"),
                ("playerDBPlayerTeam", "player_id"),
                ("Customize_table", "player_id"),
            ):
                self._connection.execute(
                    f"DELETE FROM {table} WHERE {column} = ?",  # noqa: S608 - constants
                    (player_id,),
                )
            self._clear_roles(player_id)
            self._update_team_averages(teams)
        activity.info("%s: deleted", _describe(player_id, player))

    def _copy(self, source_id: int, player_id: int) -> None:
        """Copy data, skills and 3D look; teams are up to the caller."""
        # player_img names a file the game does not use (see app/portraits.py).
        self._connection.execute(
            "INSERT INTO playerDBPlayerData (player_id, data_id, value) "
            "SELECT ?, data_id, value FROM playerDBPlayerData "
            "WHERE player_id = ? AND data_id NOT IN "
            "(SELECT id FROM playerDBData WHERE key = 'player_img')",
            (player_id, source_id),
        )
        self._connection.execute(
            f"INSERT INTO playerDBPlayer (id, {_SKILL_COLUMNS}, "  # noqa: S608 - constants
            f"{_AVERAGE_COLUMNS}) SELECT ?, {_SKILL_COLUMNS}, "
            f"{_AVERAGE_COLUMNS} FROM playerDBPlayer WHERE id = ?",
            (player_id, source_id),
        )
        self._connection.execute(
            f"INSERT INTO Customize_table (player_id, {_APPEARANCE_COLUMNS}) "  # noqa: S608 - constants
            f"SELECT ?, {_APPEARANCE_COLUMNS} FROM Customize_table "
            "WHERE player_id = ?",
            (player_id, source_id),
        )

    def _next_player_id(self) -> int:
        (last,) = self._connection.execute(
            "SELECT MAX(id) FROM (SELECT MAX(id) AS id FROM playerDBPlayer "
            "UNION ALL SELECT MAX(player_id) FROM playerDBPlayerData "
            "UNION ALL SELECT MAX(player_id) FROM playerDBPlayerTeam "
            "UNION ALL SELECT MAX(player_id) FROM Customize_table)"
        ).fetchone()
        return (last or 0) + 1

    def _placement(self, team_id: int | None) -> set[int]:
        """A new player's teams: this one, and the pool of the other kind."""
        teams = {CLUB_POOL, NATIONAL_POOL}
        if team_id is None:
            return teams
        row = self._connection.execute(
            "SELECT type FROM playerDBTeam WHERE id = ?", (team_id,)
        ).fetchone()
        if row is None:
            raise ValidationError(f"Team {team_id} does not exist.")
        for team_type, pool, _label in TEAM_KINDS.values():
            if row[0] == team_type:
                teams = (teams - {pool}) | {team_id}
        return teams

    def _join(self, player_id: int, teams: set[int]) -> None:
        self._connection.executemany(
            "INSERT INTO playerDBPlayerTeam (player_id, team_id) VALUES (?, ?)",
            [(player_id, team_id) for team_id in sorted(teams)],
        )
        self._update_team_averages(teams)

    def _free_jersey(self, team_id: int | None) -> int:
        """The lowest jersey number nobody in the team wears, else 0."""
        rows = self._connection.execute(
            "SELECT j.value FROM playerDBPlayerTeam pt "
            "JOIN playerDBPlayerData j ON j.player_id = pt.player_id "
            "AND j.data_id = (SELECT id FROM playerDBData WHERE key = 'jersey_nr') "
            "WHERE pt.team_id = ?",
            (team_id,),
        )
        used = {value for (value,) in rows}
        return next((n for n in JERSEY_NUMBERS if str(n) not in used), 0)

    def _team_country(self, team_id: int | None) -> int:
        """The team's country; without a real team, Spain or the first one."""
        row = None
        if team_id not in POOL_TEAM_IDS:
            row = self._connection.execute(
                "SELECT c.ID FROM playerDBTeam t "
                "JOIN Country c ON c.ID = t.country_id WHERE t.id = ?",
                (team_id,),
            ).fetchone()
        if row is None:
            row = self._connection.execute(
                "SELECT ID FROM Country ORDER BY ID = ? DESC, ID LIMIT 1",
                (DEFAULT_COUNTRY,),
            ).fetchone()
        return row[0]

    def _team_names(self, teams: set[int]) -> str:
        return ", ".join(
            f"{self._team_name(team_id)} ({team_id})" for team_id in sorted(teams)
        )

    def _clear_roles(self, player_id: int, team_id: int | None = None) -> None:
        """Empty the roles that point to the player, in one team or in all."""
        for column in ROLE_COLUMNS:
            self._connection.execute(
                f"UPDATE playerDBTeam SET {column} = 0 "  # noqa: S608 - column from ROLE_COLUMNS
                f"WHERE {column} = ? AND (? IS NULL OR id = ?)",
                (player_id, team_id, team_id),
            )

    def _restore_memberships(self, player_id: int) -> None:
        """Original teams of the player, and the roles that were cleared.

        Needs the original data attached as `original` and runs inside the
        caller's transaction (see revert_player): it does not commit.
        """
        self._connection.execute(
            "DELETE FROM main.playerDBPlayerTeam WHERE player_id = ?", (player_id,)
        )
        self._connection.execute(
            "INSERT INTO main.playerDBPlayerTeam (player_id, team_id) "
            "SELECT player_id, team_id FROM original.playerDBPlayerTeam "
            "WHERE player_id = ?",
            (player_id,),
        )
        for column in ROLE_COLUMNS:
            self._connection.execute(
                f"UPDATE main.playerDBTeam SET {column} = ? WHERE {column} = 0 "  # noqa: S608 - column from ROLE_COLUMNS
                f"AND id IN (SELECT id FROM original.playerDBTeam WHERE {column} = ?)",
                (player_id, player_id),
            )

    def release_team(self, team_id: int) -> list[int]:
        """Send every member of a team to the pool of its kind.

        It does not commit: the caller runs it inside its own transaction, so
        moving the players and what follows succeed or fail together.
        Returns the players moved.
        """
        row = self._connection.execute(
            "SELECT type FROM playerDBTeam WHERE id = ?", (team_id,)
        ).fetchone()
        kinds = [
            (kind, pool)
            for kind, (kind_type, pool, _label) in TEAM_KINDS.items()
            if row and kind_type == row[0]
        ]
        if not kinds:
            return []
        kind, pool = kinds[0]
        members = [member.id for member in self.team_players(team_id)]
        touched: set[int] = set()
        for player_id in members:
            touched |= self._move(player_id, kind, pool)
        self._update_team_averages(touched)
        return members

    def _move(self, player_id: int, kind: str, team_id: int) -> set[int]:
        """Put the player in this team of this kind. Returns the teams touched."""
        team_type = TEAM_KINDS[kind][0]
        old = self._player_teams(player_id, team_type)
        if old == {team_id}:
            return set()
        self._connection.execute(
            "DELETE FROM playerDBPlayerTeam WHERE player_id = ? "
            "AND team_id IN (SELECT id FROM playerDBTeam WHERE type = ?)",
            (player_id, team_type),
        )
        self._connection.execute(
            "INSERT INTO playerDBPlayerTeam (player_id, team_id) VALUES (?, ?)",
            (player_id, team_id),
        )
        for old_team in old:
            self._clear_roles(player_id, old_team)
        return old | {team_id}

    def _player_teams(self, player_id: int, team_type: str | None = None) -> set[int]:
        rows = self._connection.execute(
            "SELECT pt.team_id FROM playerDBPlayerTeam pt "
            "JOIN playerDBTeam t ON t.id = pt.team_id "
            "WHERE pt.player_id = ? AND (? IS NULL OR t.type = ?)",
            (player_id, team_type, team_type),
        )
        return {team_id for (team_id,) in rows}

    def _team_name(self, team_id: int) -> str:
        row = self._connection.execute(
            "SELECT name FROM playerDBTeam WHERE id = ?", (team_id,)
        ).fetchone()
        return row[0] if row else f"team {team_id}"

    def _checked(
        self, player: dict[str, str], changes: dict[str, str]
    ) -> dict[str, str]:
        """Validate changes to this player. A ValidationError names the field."""
        fields = self.fields()
        values = {}
        for key, raw in changes.items():
            try:
                values[key] = self._validate_key(fields, key, raw)
            except ValidationError as error:
                raise ValidationError(str(error), key) from None
        skill_keys = [key for key in values if key in SKILLS or key == OVERALL]
        if skill_keys and SKILLS[0] not in player:
            raise ValidationError("This player has no skills.", skill_keys[0])
        main_pos = values.get("main_pos", player.get("main_pos"))
        if OVERALL in values and main_pos != str(GOALKEEPER):
            raise ValidationError("Only goalkeepers have a hand-set overall.", OVERALL)
        return values

    def _store(
        self, player_id: int, player: dict[str, str], values: dict[str, str]
    ) -> None:
        """Write checked values. It does not commit: the caller's transaction."""
        fields = self.fields()
        moved_teams: set[int] = set()
        for key, value in values.items():
            if key in SKILLS:
                self._connection.execute(
                    f"UPDATE playerDBPlayer SET {key} = ? WHERE id = ?",  # noqa: S608 - key is in SKILLS
                    (int(value), player_id),
                )
            elif key in TEAM_KINDS:
                moved_teams |= self._move(player_id, key, int(value))
            elif key != OVERALL:
                self._write(player_id, fields[key], value)
        if any(key in SKILLS or key in {OVERALL, "main_pos"} for key in values):
            main_pos = values.get("main_pos", player.get("main_pos"))
            self._update_averages(player_id, main_pos, values.get(OVERALL))
        self._update_team_averages(moved_teams)

    def _validate_key(self, fields: dict[str, Field], key: str, raw: str) -> str:
        if key in TEAM_KINDS:
            ids = {team_id for team_id, _ in self.team_options(key)}
            return validation.validate_choice(raw, ids)
        if key in SKILLS:
            return validation.validate_int(key, raw)
        if key == OVERALL:
            return validation.validate_average(raw)
        field = fields.get(key)
        if field is None:
            raise ValidationError("Unknown or non-editable field.")
        return self._validate(field, raw)

    def _update_averages(
        self, player_id: int, main_pos: str | None, overall: str | None
    ) -> None:
        row = self._connection.execute(
            f"SELECT {_SKILL_COLUMNS}, total_avg "  # noqa: S608 - constants
            "FROM playerDBPlayer WHERE id = ?",
            (player_id,),
        ).fetchone()
        if row is None:
            return
        skills = dict(zip(SKILLS, row[: len(SKILLS)], strict=True))
        position = int(main_pos) if main_pos and main_pos.isdigit() else None
        averages = player_averages(skills, position) if position else None
        if averages is None:
            # Goalkeeper or unknown position: one hand-set value for all three.
            value = stored_float(float(overall) if overall else row[-1])
            averages = (value, value, value)
        self._connection.execute(
            "UPDATE playerDBPlayer SET total_avg = ?, total_def_avg = ?, "
            "total_off_avg = ? WHERE id = ?",
            (*averages, player_id),
        )
        self._update_team_averages(self._player_teams(player_id))

    def _update_team_averages(self, team_ids: set[int]) -> None:
        """Mean total_avg of each team's players, for teams that have a row."""
        for team_id in sorted(team_ids):
            (average,) = self._connection.execute(
                "SELECT AVG(p.total_avg) FROM playerDBPlayerTeam pt "
                "JOIN playerDBPlayer p ON p.id = pt.player_id WHERE pt.team_id = ?",
                (team_id,),
            ).fetchone()
            # A team left without players gets 0, like teams with no data.
            self._connection.execute(
                "UPDATE playerDBTeamAverages SET skill_average = ? WHERE team_id = ?",
                (stored_float(average) if average is not None else 0, team_id),
            )

    def _write(self, player_id: int, field: Field, value: str) -> None:
        if field.key == "player_img" and not value:
            self._connection.execute(
                "DELETE FROM playerDBPlayerData WHERE player_id = ? AND data_id = ?",
                (player_id, field.id),
            )
        else:
            self._connection.execute(
                "INSERT INTO playerDBPlayerData (player_id, data_id, value) "
                "VALUES (?, ?, ?) ON CONFLICT (player_id, data_id) "
                "DO UPDATE SET value = excluded.value",
                (player_id, field.id, value),
            )

    def _validate(self, field: Field, raw: str) -> str:
        if field.key == "player_img":
            return validation.validate_image(raw)
        if field.type == "STRING":
            return validation.validate_text(raw, NAME_MAX_LENGTH)
        if field.type == "INT":
            return validation.validate_int(field.key, raw)
        if field.type == "DATE":
            return validation.validate_date(raw)
        if options := self.options(field):
            return validation.validate_choice(raw, {row_id for row_id, _ in options})
        raise ValidationError("This field cannot be edited.")


def name_warnings(values: dict[str, str]) -> list[str]:
    """Names the game shows, but that overlap the number on its player card."""
    if any(
        len(values.get(key, "")) > NAME_LONG_ON_CARD for key in ("name", "first_name")
    ):
        return [
            f"Names longer than {NAME_LONG_ON_CARD} characters overlap the jersey "
            "number on the game's player card."
        ]
    return []


def _describe(player_id: int, player: dict[str, str]) -> str:
    name = f"{player.get('first_name', '')} {player.get('name', '')}".strip()
    return f"player {player_id} ({name})"


def _fold(text: str) -> str:
    """Lower case without accents, for searching names."""
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c)).casefold()
