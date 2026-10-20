"""Readable differences between two decrypted copies of the game data."""

import sqlite3
from collections.abc import Callable
from contextlib import closing
from pathlib import Path

from app.averages import AVERAGES, SKILLS
from app.database import attached_original, open_read_only

_HANDLED_TABLES = {
    "playerDBPlayerData",
    "playerDBPlayer",
    "playerDBPlayerTeam",
    "playerDBLeague",
    "playerDBTeam",
    "Customize_table",
    "playerDBTeamAverages",
}
_SCHEMAS = ("main", "original")


def diff_databases(current: Path, original: Path) -> list[str]:
    """One line per change from `original` to `current`, with field names."""
    with (
        closing(open_read_only(current)) as connection,
        attached_original(connection, original),
    ):
        return [
            *_player_data(connection),
            *_player_skills(connection),
            *_memberships(connection),
            *_rows(connection, "playerDBLeague", _league),
            *_rows(connection, "playerDBTeam", _team),
            *_rows(connection, "Customize_table", _look, "player_id"),
            *_team_averages(connection),
            *_other_tables(connection),
        ]


def _player_ids(connection: sqlite3.Connection, schema: str) -> set[int]:
    rows = connection.execute(
        f"SELECT DISTINCT player_id FROM {schema}.playerDBPlayerData"  # noqa: S608 - schema is a constant
    )
    return {player_id for (player_id,) in rows}


def _player_data(connection: sqlite3.Connection) -> list[str]:
    current_ids = _player_ids(connection, "main")
    original_ids = _player_ids(connection, "original")
    lines = [
        f"{_player(connection, player_id)}: added"
        for player_id in sorted(current_ids - original_ids)
    ]
    lines += [
        f"{_player(connection, player_id)}: removed"
        for player_id in sorted(original_ids - current_ids)
    ]
    rows = connection.execute(
        "SELECT COALESCE(c.player_id, o.player_id) AS player_id, "
        "COALESCE(d.key, CAST(COALESCE(c.data_id, o.data_id) AS TEXT)), "
        "o.value, c.value "
        "FROM main.playerDBPlayerData c "
        "FULL JOIN original.playerDBPlayerData o "
        "ON o.player_id = c.player_id AND o.data_id = c.data_id "
        "LEFT JOIN main.playerDBData d ON d.id = COALESCE(c.data_id, o.data_id) "
        "WHERE c.value IS NOT o.value "
        "ORDER BY player_id, COALESCE(c.data_id, o.data_id)"
    )
    for player_id, key, old, new in rows:
        if player_id in current_ids and player_id in original_ids:
            lines.append(
                f"{_player(connection, player_id)}: {key} {_show(old)} -> {_show(new)}"
            )
    return lines


def _player_skills(connection: sqlite3.Connection) -> list[str]:
    columns = (*SKILLS, *AVERAGES)
    selected = ", ".join(f"c.{column}, o.{column}" for column in columns)
    differs = " OR ".join(f"c.{column} IS NOT o.{column}" for column in columns)
    rows = connection.execute(
        f"SELECT c.id, {selected} FROM main.playerDBPlayer c "  # noqa: S608 - columns are constants
        f"JOIN original.playerDBPlayer o ON o.id = c.id WHERE {differs} ORDER BY c.id"
    )
    lines = []
    for player_id, *values in rows:
        for index, column in enumerate(columns):
            new, old = values[2 * index], values[2 * index + 1]
            if new != old:
                lines.append(
                    f"{_player(connection, player_id)}: {column} "
                    f"{_show(old)} -> {_show(new)}"
                )
    return lines


def _memberships(connection: sqlite3.Connection) -> list[str]:
    rows = connection.execute(
        "SELECT player_id, team_id, 'joined' FROM (SELECT player_id, team_id "
        "FROM main.playerDBPlayerTeam EXCEPT SELECT player_id, team_id "
        "FROM original.playerDBPlayerTeam) "
        "UNION ALL SELECT player_id, team_id, 'left' FROM (SELECT player_id, "
        "team_id FROM original.playerDBPlayerTeam EXCEPT SELECT player_id, "
        "team_id FROM main.playerDBPlayerTeam) ORDER BY 1, 3, 2"
    )
    return [
        f"{_player(connection, player_id)}: {change} {_team(connection, team_id)}"
        for player_id, team_id, change in rows
    ]


def _rows(
    connection: sqlite3.Connection,
    table: str,
    describe: Callable[[sqlite3.Connection, int], str],
    key: str = "id",
) -> list[str]:
    """Rows added or removed by key, and each changed column of the others."""
    ids = {
        schema: {
            row_id
            for (row_id,) in connection.execute(
                f"SELECT {key} FROM {schema}.{table}"  # noqa: S608 - schema, table and key are constants
            )
        }
        for schema in _SCHEMAS
    }
    lines = [
        f"{describe(connection, row_id)}: added"
        for row_id in sorted(ids["main"] - ids["original"])
    ]
    lines += [
        f"{describe(connection, row_id)}: removed"
        for row_id in sorted(ids["original"] - ids["main"])
    ]
    columns = {
        schema: [
            row[1] for row in connection.execute(f"PRAGMA {schema}.table_info({table})")
        ]
        for schema in _SCHEMAS
    }
    common = [
        column
        for column in columns["main"]
        if column in columns["original"] and column != key and '"' not in column
    ]
    if not common:
        return lines
    selected = ", ".join(f'c."{column}", o."{column}"' for column in common)
    differs = " OR ".join(f'c."{column}" IS NOT o."{column}"' for column in common)
    rows = connection.execute(
        f"SELECT c.{key}, {selected} FROM main.{table} c "  # noqa: S608 - columns from the schema
        f"JOIN original.{table} o ON o.{key} = c.{key} WHERE {differs} "
        f"ORDER BY c.{key}"
    )
    for row_id, *values in rows:
        for index, column in enumerate(common):
            new, old = values[2 * index], values[2 * index + 1]
            if new != old:
                lines.append(
                    f"{describe(connection, row_id)}: {column} "
                    f"{_show(old)} -> {_show(new)}"
                )
    return lines


def _team_averages(connection: sqlite3.Connection) -> list[str]:
    rows = connection.execute(
        "SELECT c.team_id, o.skill_average, c.skill_average "
        "FROM main.playerDBTeamAverages c "
        "JOIN original.playerDBTeamAverages o ON o.team_id = c.team_id "
        "WHERE c.skill_average IS NOT o.skill_average ORDER BY c.team_id"
    )
    return [
        f"{_team(connection, team_id)}: skill_average {_show(old)} -> {_show(new)}"
        for team_id, old, new in rows
    ]


def _other_tables(connection: sqlite3.Connection) -> list[str]:
    tables = {
        schema: {
            name
            for (name,) in connection.execute(
                f"SELECT name FROM {schema}.sqlite_master "  # noqa: S608 - schema is a constant
                "WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            )
        }
        for schema in _SCHEMAS
    }
    lines = [
        f"table {name}: only in the current data"
        for name in sorted(tables["main"] - tables["original"])
    ]
    lines += [
        f"table {name}: only in the original"
        for name in sorted(tables["original"] - tables["main"])
    ]
    for name in sorted((tables["main"] & tables["original"]) - _HANDLED_TABLES):
        if '"' in name:
            continue
        query = (
            f'SELECT (SELECT COUNT(*) FROM (SELECT * FROM main."{name}" '  # noqa: S608 - name from sqlite_master
            f'EXCEPT SELECT * FROM original."{name}")), (SELECT COUNT(*) FROM '
            f'(SELECT * FROM original."{name}" EXCEPT SELECT * FROM main."{name}"))'
        )
        try:
            new_rows, old_rows = connection.execute(query).fetchone()
        except sqlite3.OperationalError:
            lines.append(f"table {name}: structure changed")
            continue
        # A changed row shows up once on each side.
        if new_rows or old_rows:
            lines.append(
                f"table {name}: {new_rows} rows new or changed, "
                f"{old_rows} original rows changed or removed"
            )
    return lines


def _player(connection: sqlite3.Connection, player_id: int) -> str:
    for schema in _SCHEMAS:
        names = dict(
            connection.execute(
                f"SELECT d.key, p.value FROM {schema}.playerDBPlayerData p "  # noqa: S608 - schema is a constant
                f"JOIN {schema}.playerDBData d ON d.id = p.data_id "
                "WHERE p.player_id = ? AND d.key IN ('first_name', 'name')",
                (player_id,),
            ).fetchall()
        )
        if names:
            full = f"{names.get('first_name', '')} {names.get('name', '')}".strip()
            return f"player {player_id} ({full})"
    return f"player {player_id}"


def _team(connection: sqlite3.Connection, team_id: int) -> str:
    for schema in _SCHEMAS:
        row = connection.execute(
            f"SELECT name FROM {schema}.playerDBTeam WHERE id = ?",  # noqa: S608 - schema is a constant
            (team_id,),
        ).fetchone()
        if row:
            return f"team {team_id} ({row[0]})"
    return f"team {team_id}"


def _show(value: object) -> str:
    if value is None:
        return "(none)"
    if isinstance(value, float):
        return f"{value:.2f}"
    return repr(value)


def _league(connection: sqlite3.Connection, league_id: int) -> str:
    for schema in _SCHEMAS:
        row = connection.execute(
            f"SELECT name FROM {schema}.playerDBLeague WHERE id = ?",  # noqa: S608 - schema is a constant
            (league_id,),
        ).fetchone()
        if row:
            return f"league {league_id} ({row[0]})"
    return f"league {league_id}"


def _look(connection: sqlite3.Connection, player_id: int) -> str:
    return f"{_player(connection, player_id)} look"
