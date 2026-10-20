"""The average formulas against the installed game data."""

import shutil
import time
from contextlib import closing
from pathlib import Path

import pytest

from app import database
from app.appearance import load_options
from app.averages import SKILLS, player_averages
from app.config import load_config, load_paths
from app.errors import ConfigError, ValidationError
from app.gamecrypto import GameCrypto
from app.keyfinder import extract_key, key_opens
from app.leagues import LeagueRepository
from app.portrait_image import logo_from_bytes, portrait_from_bytes
from app.portraits import (
    PortraitStore,
    changed_logos,
    changed_portraits,
    logo_files,
    open_archive,
    portrait_files,
    portrait_name,
    rebuild,
)
from app.teams import TeamRepository

pytestmark = pytest.mark.game_dll

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.local.json"
# Players whose stored averages do not follow the formula (ids 723, 1491, 4517).
KNOWN_OUTLIERS = 3


@pytest.fixture
def game_db(tmp_path: Path) -> Path:
    try:
        config = load_config(CONFIG_PATH)
    except ConfigError:
        pytest.skip("config.local.json, the game DLL or the save data is missing")
    encrypted = tmp_path / "encrypted.db"
    shutil.copyfile(config.dump_path, encrypted)
    clear = tmp_path / "clear.db"
    GameCrypto(config.dll_path, config.key).decrypt(encrypted, clear)
    return clear


def test_field_player_formulas(game_db: Path) -> None:
    with closing(database.connect(game_db)) as connection:
        rows = connection.execute(
            f"SELECT {', '.join(SKILLS)}, total_avg, total_def_avg, total_off_avg, "  # noqa: S608
            "CAST(d.value AS INTEGER) FROM playerDBPlayer p "
            "JOIN playerDBPlayerData d ON d.player_id = p.id AND d.data_id = 5"
        ).fetchall()
    checked = mismatches = 0
    for row in rows:
        skills = dict(zip(SKILLS, row[: len(SKILLS)], strict=True))
        expected = player_averages(skills, row[-1])
        if expected is None:
            continue
        checked += 1
        mismatches += expected != tuple(row[len(SKILLS) : -1])
    assert checked > 3000
    assert mismatches <= KNOWN_OUTLIERS


def test_real_portrait_archive_rebuilds(tmp_path: Path) -> None:
    """Rebuild the original archive in memory with one portrait replaced."""
    try:
        paths = load_paths(CONFIG_PATH)
    except ConfigError:
        pytest.skip("config.local.json or the game installation is missing")
    store = PortraitStore(paths.portraits_path, CONFIG_PATH.parent)
    if not store.has_original():
        pytest.skip("the original portraits are not backed up")
    original = store.original_bytes()
    replacement = portrait_from_bytes(_photo_bytes())
    rebuilt = rebuild(original, portrait_files({223: replacement}))
    before, after = open_archive(original), open_archive(rebuilt)
    assert len(after.infolist()) == len(before.infolist())
    assert after.read(portrait_name(223)) == replacement
    unchanged = [
        info for info in before.infolist() if info.filename != portrait_name(223)
    ]
    assert all(after.read(info.filename) == before.read(info) for info in unchanged)
    assert changed_portraits(rebuilt, original) == {223: replacement}


def _photo_bytes() -> bytes:
    from io import BytesIO

    from PIL import Image

    buffer = BytesIO()
    Image.new("RGB", (640, 480), "purple").save(buffer, "JPEG")
    return buffer.getvalue()


def test_real_portrait_archive_takes_a_custom_logo() -> None:
    try:
        paths = load_paths(CONFIG_PATH)
    except ConfigError:
        pytest.skip("config.local.json or the game installation is missing")
    store = PortraitStore(paths.portraits_path, CONFIG_PATH.parent)
    if not store.has_original():
        pytest.skip("the original portraits are not backed up")
    original = store.original_bytes()
    big, small = logo_from_bytes(_photo_bytes())
    rebuilt = rebuild(original, logo_files({400: (big, small)}))
    assert (
        len(open_archive(rebuilt).infolist())
        == len(open_archive(original).infolist()) + 2
    )
    assert changed_logos(rebuilt, original) == {400: (big, small)}
    assert changed_portraits(rebuilt, original) == {}


def test_real_team_copy(game_db: Path, tmp_path: Path) -> None:
    original = tmp_path / "original.db"
    shutil.copyfile(game_db, original)
    with closing(database.connect(game_db)) as connection:
        leagues = LeagueRepository(connection)
        assert leagues.editor_leagues(original) == []
        league_id = leagues.create_league("Friends League", 195)
        assert league_id == 19  # sqlite_sequence was 18
        teams = TeamRepository(connection)
        with pytest.raises(ValidationError):
            teams.create_team(150, 4, original)  # Liga ASOBAL is original
        team_id = teams.create_team(150, league_id, original, "Friends FC")
        assert team_id == 339  # sqlite_sequence was 338
        assert teams.league_of(team_id) == league_id
        kits = connection.execute(
            "SELECT o.type, o.texture, COUNT(t.ID) FROM playerDBTeamOutfit o "
            "LEFT JOIN TextureCustomization t ON t.TeamOutfitID = o.id "
            "WHERE o.team_id IN (150, ?) GROUP BY o.team_id, o.id "
            "ORDER BY o.team_id, o.type",
            (team_id,),
        ).fetchall()
        assert kits[:3] == kits[3:] and len(kits) == 6
        assert teams.get_team(team_id)["coach"] == teams.get_team(150)["coach"]


def test_real_appearance_list() -> None:
    try:
        paths = load_paths(CONFIG_PATH)
    except ConfigError:
        pytest.skip("config.local.json or the game installation is missing")
    options = load_options(paths.gfx_path)
    assert len(options.options("skin_color")) == 6
    assert len(options.options("hairstyle")) == 12
    assert len(options.options("hairstyle_color")) == 7
    assert len(options.options("eye_color")) == 6
    assert len(options.shapes) == 7
    assert "024_head_lips_thin" not in dict(options.options("shape_eyes"))


def test_key_found_in_the_installed_game() -> None:
    """The key read from HC14.exe opens the installed data (never printed)."""
    try:
        paths = load_paths(CONFIG_PATH)
    except ConfigError:
        pytest.skip("config.local.json or the game installation is missing")
    started = time.perf_counter()
    key = extract_key(paths)
    assert time.perf_counter() - started < 5
    assert key_opens(paths, key)
