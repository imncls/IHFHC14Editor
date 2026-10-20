"""The game's saved leagues and tournaments (SGT_*.sg, plain XML).

They store team and league ids only (see docs/game-data.md), so deleting a
team or league they use, or restoring the original data without it, can leave
a save pointing to something that no longer exists. This module finds them so
the editor can warn first.
"""

import re
import xml.etree.ElementTree as ElementTree
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

SAVE_PATTERN = "SGT_*.sg"
MAX_SAVE_BYTES = 20 * 1024 * 1024
# Attributes that hold a team id: playerTeamID, homeTeam, guestTeam...
_TEAM_ATTRIBUTE = re.compile(r"(?i)(team|teamid)$")


@dataclass(frozen=True)
class SaveGame:
    path: Path
    name: str
    league_id: int | None
    team_ids: frozenset[int]


def save_games(folder: Path) -> list[SaveGame]:
    """Every save in the game's folder that can be read; others are skipped."""
    if not folder.is_dir():
        return []
    saves = []
    for path in sorted(folder.glob(SAVE_PATTERN)):
        save = _read(path)
        if save is not None:
            saves.append(save)
    return saves


def saves_using(
    folder: Path, team_ids: Iterable[int] = (), league_ids: Iterable[int] = ()
) -> list[SaveGame]:
    """The saves that use any of these teams or leagues."""
    teams, leagues = set(team_ids), set(league_ids)
    return [
        save
        for save in save_games(folder)
        if save.team_ids & teams or save.league_id in leagues
    ]


def describe(saves: list[SaveGame]) -> str:
    """Save names for a message, for example 'Friends FC (SGT_LEAGUE.2.sg)'."""
    return ", ".join(f"{save.name} ({save.path.name})" for save in saves)


def _read(path: Path) -> SaveGame | None:
    try:
        if path.stat().st_size > MAX_SAVE_BYTES:
            return None
        data = path.read_bytes()
    except OSError:
        return None
    # Without a DTD there are no entities, so no entity expansion attacks.
    if b"<!DOCTYPE" in data.upper() or b"<!ENTITY" in data.upper():
        return None
    try:
        root = ElementTree.fromstring(data)  # noqa: S314 - no DTD, size-capped
    except ElementTree.ParseError:
        return None
    teams = set()
    for element in root.iter():
        for name, value in element.attrib.items():
            if _TEAM_ATTRIBUTE.search(name) and value.isdigit():
                teams.add(int(value))
    league = root.get("leagueID", "")
    return SaveGame(
        path=path,
        name=root.get("name", path.stem),
        league_id=int(league) if league.isdigit() else None,
        team_ids=frozenset(teams),
    )
