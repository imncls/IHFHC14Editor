"""Opening the editor's SQLite databases, and the queries they share.

The only place that opens a connection or attaches the original data:
- the working copy is opened with URI filenames on, so the original data can
  be attached read-only next to it;
- the original data is always attached read-only, as `original`, for one
  block: nothing can write to it by mistake.
"""

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

# Tables whose rows have a unique name (compared without case).
NAMED_TABLES = frozenset({"playerDBLeague", "playerDBTeam"})


def connect(path: Path) -> sqlite3.Connection:
    """A connection to a decrypted copy (the working copy, usually).

    URI filenames are on so that attached_original can attach read-only; a
    plain path is still a plain path.
    """
    return sqlite3.connect(path, uri=True)


def open_read_only(path: Path) -> sqlite3.Connection:
    """A connection that cannot write to the file."""
    return sqlite3.connect(read_only_uri(path), uri=True)


def read_only_uri(path: Path) -> str:
    return path.resolve().as_uri() + "?mode=ro"


@contextmanager
def attached_original(
    connection: sqlite3.Connection, original_db: Path
) -> Iterator[None]:
    """The original game data as the read-only `original` schema, for one
    block. The connection must come from connect() or open_read_only().
    It cannot be used inside a transaction: SQLite attaches and detaches
    only between them, so open the transaction inside the block."""
    connection.execute("ATTACH DATABASE ? AS original", (read_only_uri(original_db),))
    try:
        yield
    finally:
        connection.execute("DETACH DATABASE original")


def name_taken(
    connection: sqlite3.Connection,
    table: str,
    name: str,
    exclude_id: int | None = None,
) -> bool:
    """Whether another row of the table has this name, without case. The row
    `exclude_id` (the one being renamed) does not count."""
    if table not in NAMED_TABLES:
        raise ValueError(f"No unique names in {table}.")
    row = connection.execute(
        f"SELECT 1 FROM {table} WHERE name = ? COLLATE NOCASE AND id IS NOT ?",  # noqa: S608 - table from NAMED_TABLES
        (name, exclude_id),
    ).fetchone()
    return row is not None
