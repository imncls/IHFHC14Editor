"""Leagues made with the editor (playerDBLeague) on the decrypted copy.

The game's own leagues use fixed fixtures, so teams made with the editor only
go into leagues made with the editor, which the game generates fixtures for
(see docs/game-data.md). Original leagues are never edited.
"""

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from app import validation
from app.database import attached_original, name_taken
from app.errors import ValidationError
from app.repository import NATIONAL_TEAMS_GROUP, activity

LEAGUE_NAME_MAX = 40
# League mode only builds fixtures for an even number of teams (4, 6 and 16 checked).
MIN_LEAGUE_TEAMS = 4
LEAGUE_KEYS = ("name", "country_id")


@dataclass(frozen=True)
class League:
    id: int
    name: str
    country_id: int
    team_count: int


def log_created(league_id: int, name: str) -> None:
    activity.info("league %s (%s): created", league_id, name)


class LeagueRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def get_league(self, league_id: int) -> League:
        row = self._connection.execute(
            "SELECT l.id, l.name, l.country_id, "
            "(SELECT COUNT(*) FROM playerDBTeam t WHERE t.league_id = l.id) "
            "FROM playerDBLeague l WHERE l.id = ?",
            (league_id,),
        ).fetchone()
        if row is None:
            raise ValidationError(f"League {league_id} does not exist.")
        return League(*row)

    def playable_leagues(self) -> list[League]:
        """Every league the game lists (inFinalGame = 1), by name."""
        rows = self._connection.execute(
            "SELECT l.id, l.name, l.country_id, "
            "(SELECT COUNT(*) FROM playerDBTeam t WHERE t.league_id = l.id) "
            "FROM playerDBLeague l WHERE l.inFinalGame = 1 ORDER BY l.name"
        ).fetchall()
        return [League(*row) for row in rows]

    def editor_leagues(self, original_db: Path) -> list[League]:
        """Leagues made with the editor, by name."""
        with attached_original(self._connection, original_db):
            rows = self._connection.execute(
                "SELECT l.id, l.name, l.country_id, "
                "(SELECT COUNT(*) FROM main.playerDBTeam t WHERE t.league_id = l.id) "
                "FROM main.playerDBLeague l "
                "WHERE l.id NOT IN (SELECT id FROM original.playerDBLeague) "
                "ORDER BY l.name"
            ).fetchall()
        return [League(*row) for row in rows]

    def is_editor_league(self, league_id: int, original_db: Path) -> bool:
        return any(
            league.id == league_id for league in self.editor_leagues(original_db)
        )

    def countries(self) -> list[tuple[int, str]]:
        return self._connection.execute(
            "SELECT ID, Name FROM Country ORDER BY Name"
        ).fetchall()

    def create_league(self, name: str, country_id: int) -> int:
        """A new playable league; the game generates its fixtures."""
        name, country_id = self.check_new_league(name, country_id)
        with self._connection:
            league_id = self.insert_league(name, country_id)
        log_created(league_id, name)
        return league_id

    def check_new_league(self, name: str, country_id: int) -> tuple[str, int]:
        """The name and country of a new league, as they will be written."""
        try:
            name = self._valid_name(name)
        except ValidationError as error:
            raise ValidationError(str(error), "name") from None
        try:
            country_id = self._valid_country(str(country_id))
        except ValidationError as error:
            raise ValidationError(str(error), "country_id") from None
        return name, country_id

    def insert_league(self, name: str, country_id: int) -> int:
        """Insert checked values. It does not commit or log: the caller's
        transaction (see check_new_league and log_created)."""
        return self._connection.execute(
            "INSERT INTO playerDBLeague (name, country_id, min_skill_avg, "
            "max_skill_avg, logoFile, inFinalGame, parentID, teamCount) "
            "VALUES (?, ?, 0, 0, NULL, 1, 0, 0)",
            (name, country_id),
        ).lastrowid

    def set_league_values(
        self, league_id: int, changes: dict[str, str], original_db: Path
    ) -> dict[str, str]:
        """Change the name or country of a league made with the editor."""
        league = self._editor_league(league_id, original_db)
        values: dict[str, object] = {}
        for key, raw in changes.items():
            try:
                if key == "name":
                    values[key] = self._valid_name(raw, league_id)
                elif key == "country_id":
                    values[key] = self._valid_country(raw)
                else:
                    raise ValidationError("Unknown or non-editable league field.")
            except ValidationError as error:
                raise ValidationError(str(error), key) from None
        old = {"name": league.name, "country_id": league.country_id}
        # "Test " saved over "Test" is no change once validated.
        values = {key: value for key, value in values.items() if value != old[key]}
        if not values:
            return {}
        assignments = ", ".join(f"{key} = ?" for key in values)
        with self._connection:
            self._connection.execute(
                f"UPDATE playerDBLeague SET {assignments} WHERE id = ?",  # noqa: S608 - keys from LEAGUE_KEYS
                (*values.values(), league_id),
            )
        for key, value in values.items():
            activity.info(
                "league %s (%s): %s %r -> %r",
                league_id,
                league.name,
                key,
                str(old[key]),
                str(value),
            )
        return {key: str(value) for key, value in values.items()}

    def delete_league(self, league_id: int, original_db: Path) -> None:
        """Delete an empty league made with the editor."""
        league = self._editor_league(league_id, original_db)
        if league.team_count:
            raise ValidationError("Move or delete its teams first.")
        with self._connection:
            self._connection.execute(
                "DELETE FROM playerDBLeague WHERE id = ?", (league_id,)
            )
        activity.info("league %s (%s): deleted", league_id, league.name)

    def league_warnings(self, league_id: int) -> list[str]:
        """League mode rejects some team counts (5 failed in the game)."""
        league = self.get_league(league_id)
        count = league.team_count
        if count >= MIN_LEAGUE_TEAMS and count % 2 == 0:
            return []
        return [
            f"{league.name} has {count} teams; league mode needs an even number, "
            f"at least {MIN_LEAGUE_TEAMS}."
        ]

    def _editor_league(self, league_id: int, original_db: Path) -> League:
        league = self.get_league(league_id)
        if not self.is_editor_league(league_id, original_db):
            raise ValidationError("Only leagues made with the editor can be changed.")
        return league

    def _valid_name(self, raw: str, league_id: int | None = None) -> str:
        name = validation.validate_label(raw, LEAGUE_NAME_MAX)
        taken = name_taken(self._connection, "playerDBLeague", name, league_id)
        if taken or name.casefold() == NATIONAL_TEAMS_GROUP.casefold():
            raise ValidationError("Another league already has this name.")
        return name

    def _valid_country(self, raw: str) -> int:
        ids = {country_id for country_id, _ in self.countries()}
        return int(validation.validate_choice(raw, ids))
