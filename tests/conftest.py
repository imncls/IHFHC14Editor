import io
import sqlite3
import zipfile
from collections.abc import Iterator
from contextlib import closing
from pathlib import Path

import pytest

from app import database
from app.config import DUMP_NAME, PORTRAITS_ARCHIVE
from app.portraits import PNG_SIGNATURE, XOR_TABLE, PortraitStore
from app.workspace import Workspace

FIELDS = [
    (1, "name", "name", "STRING", 0),
    (2, "first name", "first_name", "STRING", 0),
    (3, "jersey number", "jersey_nr", "INT", 0),
    (4, "date of birth", "dob", "DATE", 0),
    (5, "main position", "main_pos", "LOOKUP", 7),
    (8, "throwing arm", "throw_arm", "LOOKUP", 6),
    (13, "body type", "body", "LOOKUP", 4),
    (18, "country", "country", "FOREIGN_KEY", 0),
    (19, "jersey number in national team", "national_team_jersey_nr", "INT", 0),
    (20, "image of player", "player_img", "STRING", 0),
]
PLAYER_WITHOUT_IMAGE = 1
PLAYER_WITH_IMAGE = 2
CLUB = 105
NATIONAL_TEAM = 324
EMPTY_ORIGINAL_TEAM = 115
FAKE_TEAM = 147
POOL = 319
CLUB_POOL = 194
OTHER_CLUB = 150
LEAGUES = [(4, "Liga ASOBAL"), (8, "Dukat Premijer liga"), (18, "Not in use")]
TEAMS = [
    (CLUB, "FC Barcelona Intersport", "LEAGUE", 4),
    (OTHER_CLUB, "Fraikin BM. Granollers", "LEAGUE", 4),
    (CLUB_POOL, "Not assigned", "LEAGUE", 8),
    (NATIONAL_TEAM, "Spain", "NATIONAL_TEAM", 0),
    (EMPTY_ORIGINAL_TEAM, "Spain (Original)", "NATIONAL_TEAM", 18),
    (FAKE_TEAM, "(Fake) Nîmes Gard (Fake)", "LEAGUE", 18),
    (POOL, "NOT ASSIGNABLE", "NATIONAL_TEAM", 18),
]
# Invented skills: a field player and a goalkeeper (whose overall is set by hand).
SKILLS_BY_PLAYER = {
    PLAYER_WITHOUT_IMAGE: (80, 77, 84, 79, 82, 76, 74, 81, 73, 85, 83),
    PLAYER_WITH_IMAGE: (86, 60, 62, 84, 64, 61, 66, 88, 63, 85, 90),
}
GOALKEEPER_AVERAGE = 87.25
BARCELONA_LOGO_FILE = "gui\\images_fullprice\\logos\\teams\\barcelona_logo_105.dds"
# (outfit id, team id, kit type): 0 home, 1 away, 2 goalkeeper.
OUTFITS = [(10, CLUB, 0), (11, CLUB, 1), (12, CLUB, 2), (20, OTHER_CLUB, 0)]
MEMBERSHIPS = [
    (PLAYER_WITHOUT_IMAGE, CLUB),
    (PLAYER_WITH_IMAGE, CLUB),
    (PLAYER_WITH_IMAGE, NATIONAL_TEAM),
    (PLAYER_WITHOUT_IMAGE, POOL),
    (PLAYER_WITH_IMAGE, POOL),
]


def build_game_db(path: Path) -> None:
    """A small synthetic copy of the game's player and team tables."""
    with closing(sqlite3.connect(path)) as connection:
        connection.executescript(
            """
            CREATE TABLE playerDBData (id INTEGER PRIMARY KEY, description VARCHAR,
                key VARCHAR NOT NULL, type VARCHAR NOT NULL, lookup_type_id INTEGER);
            CREATE TABLE playerDBPlayerData (player_id INTEGER NOT NULL,
                data_id INTEGER NOT NULL, value VARCHAR NOT NULL,
                PRIMARY KEY (player_id, data_id));
            CREATE TABLE playerDBLookupTable (id INTEGER PRIMARY KEY, value VARCHAR,
                description VARCHAR, lookup_type_id INTEGER);
            CREATE TABLE Country (ID INTEGER PRIMARY KEY, Name VARCHAR NOT NULL);
            CREATE TABLE playerDBLeague (id INTEGER PRIMARY KEY AUTOINCREMENT,
                name VARCHAR NOT NULL, country_id INTEGER NOT NULL DEFAULT 1,
                min_skill_avg DEFAULT 0, max_skill_avg DEFAULT 0,
                logoFile VARCHAR(256), inFinalGame BOOL DEFAULT 1,
                parentID INTEGER DEFAULT 0, teamCount INTEGER DEFAULT 0);
            CREATE TABLE playerDBTeam (id INTEGER PRIMARY KEY AUTOINCREMENT,
                name VARCHAR,
                city VARCHAR DEFAULT '', type VARCHAR, league_id INTEGER,
                country_id INTEGER DEFAULT 1, coach VARCHAR DEFAULT 'na',
                home_colour INTEGER DEFAULT 0, away_colour INTEGER DEFAULT 0,
                mascot VARCHAR DEFAULT '', home_arena INTEGER DEFAULT 10,
                spectators_average INTEGER DEFAULT 4,
                annual_budget FLOAT DEFAULT 0,
                min_skill_average INTEGER DEFAULT 80,
                max_skill_average INTEGER DEFAULT 85,
                sr_captain_player_id INTEGER DEFAULT 0,
                sr_shooter_player_id INTEGER DEFAULT 0,
                sr_defense_player_id INTEGER DEFAULT 0,
                sr_tricky_player_id INTEGER DEFAULT 0,
                sr_7m_shooter INTEGER DEFAULT 0, rating FLOAT DEFAULT 4.0,
                logoFile VARCHAR, floor_type_id INTEGER DEFAULT 1,
                abbrev VARCHAR DEFAULT '',
                off_team_speed INTEGER DEFAULT 50, off_team_1vs1 INTEGER DEFAULT 50,
                off_plays INTEGER DEFAULT 50, off_crossing INTEGER DEFAULT 50,
                off_passing INTEGER DEFAULT 50, off_tricks INTEGER DEFAULT 50,
                off_focus INTEGER DEFAULT 50, def_position INTEGER DEFAULT 50,
                def_helping INTEGER DEFAULT 50, def_aggressive INTEGER DEFAULT 50,
                def_holding INTEGER DEFAULT 50, def_intercept INTEGER DEFAULT 50);
            CREATE TABLE playerDBArena (id INTEGER PRIMARY KEY, name VARCHAR,
                country_id INTEGER, capacity INTEGER, local_db_arena_id INTEGER);
            CREATE TABLE playerDBFloorTypes (id INTEGER PRIMARY KEY, name VARCHAR);
            CREATE TABLE playerDBSpectatorAverage (id INTEGER PRIMARY KEY,
                min_average INTEGER, max_average INTEGER);
            CREATE TABLE playerDBPlayerTeam (player_id INTEGER, team_id INTEGER);
            CREATE TABLE playerDBPlayer (id INTEGER PRIMARY KEY,
                sk_fit INTEGER NOT NULL, sk_throw INTEGER NOT NULL,
                sk_acc INTEGER NOT NULL, sk_pass INTEGER NOT NULL,
                sk_speed INTEGER NOT NULL, sk_jump INTEGER NOT NULL,
                sk_power INTEGER NOT NULL, sk_mob INTEGER NOT NULL,
                sk_block INTEGER NOT NULL, sk_comp INTEGER NOT NULL,
                sk_react INTEGER NOT NULL, total_avg FLOAT, total_def_avg FLOAT,
                total_off_avg FLOAT, portraitFile VARCHAR(256));
            CREATE TABLE playerDBTeamAverages (team_id INTEGER PRIMARY KEY,
                skill_average FLOAT NOT NULL);
            CREATE TABLE Customize_table (player_id INT NOT NULL PRIMARY KEY,
                muscularity FLOAT DEFAULT (-1), skin_brightness FLOAT DEFAULT (-1),
                skin_color VARCHAR(128), hairstyle VARCHAR(128),
                hairstyle_color VARCHAR(128), body_hairstyle VARCHAR(128),
                beard VARCHAR(128), beard_color VARCHAR(128), brow VARCHAR(128),
                brow_color VARCHAR(128), eye_color VARCHAR(128),
                single_morphs VARCHAR(5000), multi_morphs VARCHAR(5000));
            CREATE TABLE playerDBTeamOutfit (id INTEGER PRIMARY KEY AUTOINCREMENT,
                team_id INTEGER NOT NULL, type INTEGER, texture VARCHAR(256));
            CREATE TABLE TextureCustomization (
                ID INTEGER PRIMARY KEY AUTOINCREMENT, BaseAsset INTEGER DEFAULT 0,
                TeamOutfitID INTEGER, Type INTEGER NOT NULL DEFAULT 1,
                U1 FLOAT, V1 FLOAT, U2 FLOAT, V2 FLOAT, ResourcePath0 VARCHAR(256),
                ResourcePath1 VARCHAR(256), ResourcePath2 VARCHAR(256),
                Color VARCHAR(16), ZOrder FLOAT DEFAULT 0);
            """
        )
        connection.executemany(
            "INSERT INTO playerDBData VALUES (?, ?, ?, ?, ?)", FIELDS
        )
        connection.executemany(
            "INSERT INTO playerDBLookupTable VALUES (?, ?, '', ?)",
            [
                (9, "Right", 6),
                (10, "Left", 6),
                (22, "Left wing", 7),
                (24, "Centre back", 7),
                (28, "Goalkeeper", 7),
            ],
        )
        connection.executemany(
            "INSERT INTO Country VALUES (?, ?)", [(1, "Spain"), (2, "France")]
        )
        connection.executemany(
            "INSERT INTO playerDBLeague (id, name) VALUES (?, ?)", LEAGUES
        )
        connection.executemany(
            "INSERT INTO playerDBTeam (id, name, type, league_id) VALUES (?, ?, ?, ?)",
            TEAMS,
        )
        # The club's captain is player 1, as roles point to players in the game.
        connection.execute(
            "UPDATE playerDBTeam SET sr_captain_player_id = ? WHERE id = ?",
            (PLAYER_WITHOUT_IMAGE, CLUB),
        )
        # Granollers is French here, to tell a team's country from the default.
        connection.execute(
            "UPDATE playerDBTeam SET country_id = 2 WHERE id = ?", (OTHER_CLUB,)
        )
        connection.execute(
            "UPDATE playerDBTeam SET city = 'Barcelona', "
            "coach = 'Xavier Pascual Fuertes', abbrev = 'BAR', rating = 5.0, "
            "home_arena = 24 WHERE id = ?",
            (CLUB,),
        )
        connection.executemany(
            "INSERT INTO playerDBArena VALUES (?, ?, 0, ?, 0)",
            [
                (9, "Small arena", 2000),
                (10, "Medium arena", 7000),
                (24, "Training gym", 100),
            ],
        )
        connection.executemany(
            "INSERT INTO playerDBFloorTypes VALUES (?, ?)",
            [(1, "wood 1"), (2, "wood 2")],
        )
        connection.executemany(
            "INSERT INTO playerDBSpectatorAverage VALUES (?, ?, ?)",
            [(1, 0, 500), (4, 2000, 5000)],
        )
        connection.execute(
            "UPDATE playerDBTeam SET logoFile = ? WHERE id = ?",
            (BARCELONA_LOGO_FILE, CLUB),
        )
        # Home, away and goalkeeper kits; numbers printed on the first two.
        for outfit_id, team_id, kit_type in OUTFITS:
            connection.execute(
                "INSERT INTO playerDBTeamOutfit VALUES (?, ?, ?, ?)",
                (outfit_id, team_id, kit_type, f"textures\\kit_{outfit_id}.dds"),
            )
            if kit_type != 2:
                connection.execute(
                    "INSERT INTO TextureCustomization (TeamOutfitID, U1, V1, U2, "
                    "V2, ResourcePath0, Color) VALUES (?, 0.5, 0.4, 0.6, 0.3, "
                    "'gui\\fonts\\Futura.fnt', '255,255,255,255')",
                    (outfit_id,),
                )
        connection.executemany(
            "INSERT INTO playerDBPlayerTeam VALUES (?, ?)", MEMBERSHIPS
        )
        # Only player 1 has a 3D look, as 908 players in the game have none.
        connection.execute(
            "INSERT INTO Customize_table (player_id, muscularity, skin_brightness, "
            "skin_color, hairstyle, hairstyle_color, body_hairstyle, beard, "
            "beard_color, brow, brow_color, eye_color, single_morphs, multi_morphs) "
            "VALUES (?, 0.5, 0, 'Euro01', '1061_Hair06', 'brown_001.dds', '', "
            "'Bart02_diff.tga', 'braun', 'braue01_diff.tga', 'braun', "
            "'augen_blau.tga', '035_head_eyes_hang|1;028_head_eggshape|0;', "
            "'033_head_eyes_small|032_head_eyes_big|:0.5!1;')",
            (PLAYER_WITHOUT_IMAGE,),
        )
        base = {
            1: "Morel",
            2: "Ana",
            3: "7",
            4: "31.8.1990",
            5: "22",
            8: "9",
            18: "1",
            19: "",
        }
        rows = [(PLAYER_WITHOUT_IMAGE, data_id, v) for data_id, v in base.items()]
        rows += [(PLAYER_WITH_IMAGE, data_id, v) for data_id, v in base.items()]
        rows.append((PLAYER_WITH_IMAGE, 20, "MOREL.jpg"))
        connection.executemany("INSERT INTO playerDBPlayerData VALUES (?, ?, ?)", rows)
        connection.execute(
            "UPDATE playerDBPlayerData SET value = '3' "
            "WHERE player_id = ? AND data_id = 3",
            (PLAYER_WITH_IMAGE,),
        )
        connection.execute(
            "UPDATE playerDBPlayerData SET value = '28' "
            "WHERE player_id = ? AND data_id = 5",
            (PLAYER_WITH_IMAGE,),
        )
        connection.execute(
            "INSERT INTO playerDBPlayer VALUES "
            "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 50, 50, 50, NULL)",
            (PLAYER_WITHOUT_IMAGE, *SKILLS_BY_PLAYER[PLAYER_WITHOUT_IMAGE]),
        )
        connection.execute(
            "INSERT INTO playerDBPlayer VALUES "
            "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)",
            (
                PLAYER_WITH_IMAGE,
                *SKILLS_BY_PLAYER[PLAYER_WITH_IMAGE],
                *[GOALKEEPER_AVERAGE] * 3,
            ),
        )
        connection.executemany(
            "INSERT INTO playerDBTeamAverages VALUES (?, 0)",
            [(CLUB,), (OTHER_CLUB,), (NATIONAL_TEAM,), (POOL,)],
        )
        connection.commit()


@pytest.fixture
def game_db(tmp_path: Path) -> Path:
    path = tmp_path / "game.db"
    build_game_db(path)
    return path


@pytest.fixture
def connection(game_db: Path) -> Iterator[sqlite3.Connection]:
    with closing(database.connect(game_db)) as connection:
        yield connection


ENCRYPTED_MARK = b"ENCRYPTED:"


class FakeCrypto:
    """'Encrypts' by prefixing a marker; verify checks the marker."""

    def verify(self, path: Path) -> bool:
        return path.read_bytes().startswith(ENCRYPTED_MARK)

    def decrypt(self, source: Path, target: Path) -> None:
        target.write_bytes(source.read_bytes().removeprefix(ENCRYPTED_MARK))

    def encrypt(self, source: Path, target: Path) -> None:
        target.write_bytes(ENCRYPTED_MARK + source.read_bytes())


class Game:
    running = False


@pytest.fixture
def game() -> Game:
    return Game()


@pytest.fixture
def dump(tmp_path: Path) -> Path:
    clear = tmp_path / "clear-source.db"
    build_game_db(clear)
    save_dir = tmp_path / "save"
    save_dir.mkdir()
    path = save_dir / DUMP_NAME
    path.write_bytes(ENCRYPTED_MARK + clear.read_bytes())
    return path


@pytest.fixture
def workspace(tmp_path: Path, dump: Path, game: Game) -> Workspace:
    root = tmp_path / "project"
    root.mkdir(exist_ok=True)
    return Workspace(dump, FakeCrypto(), root, is_game_running=lambda: game.running)


def fake_png(label: str) -> bytes:
    return PNG_SIGNATURE + label.encode()


# A small portrait archive: one other file, a normal portrait and one with
# the odd capitalisation some real entries have.
ARCHIVE_ENTRIES = {
    "gui/images_fullprice/logos/teams/team_105.png": fake_png("logo"),
    f"gui/images_fullprice/portraits/portrait_{PLAYER_WITHOUT_IMAGE}.png": fake_png(
        "original 1"
    ),
    f"gui/images_fullprice/portraits/Portrait_{PLAYER_WITH_IMAGE}.png": fake_png(
        "original 2"
    ),
}


def build_portrait_archive(path: Path, entries: dict[str, bytes]) -> None:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(output.getvalue().translate(XOR_TABLE))


@pytest.fixture
def portrait_archive(tmp_path: Path) -> Path:
    path = tmp_path / "game" / PORTRAITS_ARCHIVE
    build_portrait_archive(path, ARCHIVE_ENTRIES)
    return path


@pytest.fixture
def portraits(tmp_path: Path, portrait_archive: Path) -> PortraitStore:
    root = tmp_path / "project"
    root.mkdir(exist_ok=True)
    store = PortraitStore(portrait_archive, root)
    store.backup()
    return store


@pytest.fixture
def workspace_with_portraits(
    tmp_path: Path, dump: Path, game: Game, portraits: PortraitStore
) -> Workspace:
    return Workspace(
        dump,
        FakeCrypto(),
        tmp_path / "project",
        is_game_running=lambda: game.running,
        portraits=portraits,
    )


# A small config in the format of the game's CharacterCustomizationConfig.xml
# (only identifiers the editor writes; no content of the game), with its quirks:
# comments containing "--" and lips listed under the eye shapes too.
APPEARANCE_XML = b"""<CharacterCustomizationConfig>
<!-- Body ------------------------------------------------------ -->
  <BodyTypes>
    <CustomizeData internalID="005_overall_typ3" locaID="X_TYPE_1" texturePath="" />
    <CustomizeData internalID="006_overall_typ4" locaID="X_TYPE_2" texturePath="" />
  </BodyTypes>
  <SkinColors>
    <CustomizeData internalID="Euro01" texturePath="x/Custom_Skin_Eurosouth.png" />
    <CustomizeData internalID="African01" texturePath="x/Custom_Skin_African.png" />
  </SkinColors>
  <EyeShapes>
    <CustomizeData internalID="024_head_lips_thin" locaID="X_TYPE_1" />
    <CustomizeData internalID="035_head_eyes_hang" locaID="X_TYPE_2" />
  </EyeShapes>
  <NoseShapes>
    <CustomizeData internalID="022_head_nose_hooked" locaID="X_TYPE_1" />
  </NoseShapes>
  <MouthShapes>
    <CustomizeData internalID="024_head_lips_thin" locaID="X_TYPE_1" />
  </MouthShapes>
  <HairStyles>
    <CustomizeData internalID="NONE" locaID="X_TYPE_NONE" />
    <CustomizeData internalID="1061_Hair06" locaID="X_TYPE_7" />
    <CustomizeData internalID="1061_Hair07" locaID="X_TYPE_8" />
  </HairStyles>
  <HairStyleColors>
    <CustomizeData internalID="brown_001.dds" texturePath="x/Custom_Hair_Brown.png" />
    <CustomizeData internalID="green_001.dds" texturePath="x/Custom_Hair_Green.png" />
  </HairStyleColors>
  <Beards>
    <CustomizeData internalID="NONE" locaID="X_TYPE_NONE" />
    <CustomizeData internalID="Bart02_diff.dds" locaID="X_TYPE_2" />
  </Beards>
  <EyeColors>
    <CustomizeData internalID="augen_blau.dds" texturePath="x/Custom_Eyes_Blue.png" />
  </EyeColors>
<!-- Morphs ---------------------------------------------------- -->
</CharacterCustomizationConfig>
"""


@pytest.fixture
def appearance_options():
    from app.appearance import parse_options

    return parse_options(APPEARANCE_XML)


def swatch_png(colour: str) -> bytes:
    """A small real PNG, like the game's 41x26 swatches."""
    from PIL import Image

    output = io.BytesIO()
    Image.new("RGBA", (41, 26), colour).save(output, "PNG")
    return output.getvalue()


SWATCH_DIR = "gui/images_fullprice/customization/"
# The swatches of the fixture XML's colours, as in the game's guimisc.v.
SWATCH_ENTRIES = {
    SWATCH_DIR + "Custom_Skin_Eurosouth.png": swatch_png("peachpuff"),
    SWATCH_DIR + "Custom_Skin_African.png": swatch_png("saddlebrown"),
    SWATCH_DIR + "Custom_Hair_Brown.png": swatch_png("sienna"),
    SWATCH_DIR + "Custom_Hair_Green.png": swatch_png("green"),
    SWATCH_DIR + "Custom_Eyes_Blue.png": swatch_png("blue"),
}


@pytest.fixture
def swatch_options(tmp_path: Path, appearance_options):
    """The fixture XML's options with the swatches of a fake guimisc.v."""
    import dataclasses

    from app.appearance import SWATCH_ARCHIVE, load_swatches

    path = tmp_path / "game" / SWATCH_ARCHIVE
    build_portrait_archive(path, SWATCH_ENTRIES)
    return dataclasses.replace(
        appearance_options, swatches=load_swatches(path, appearance_options)
    )
