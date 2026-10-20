import sqlite3
from pathlib import Path

import pytest
from conftest import (
    APPEARANCE_XML,
    PLAYER_WITH_IMAGE,
    PLAYER_WITHOUT_IMAGE,
    SWATCH_DIR,
    SWATCH_ENTRIES,
    build_game_db,
    build_portrait_archive,
)

from app.appearance import (
    MAX_SWATCH_BYTES,
    SWATCH_ARCHIVE,
    AppearanceOptions,
    AppearanceRepository,
    load_options,
    load_swatches,
    parse_morphs,
    parse_options,
)
from app.errors import ConfigError, ValidationError
from app.portraits import PNG_SIGNATURE
from app.repository import PlayerRepository


@pytest.fixture
def looks(
    connection: sqlite3.Connection, appearance_options: AppearanceOptions
) -> AppearanceRepository:
    return AppearanceRepository(connection, appearance_options)


def row(connection: sqlite3.Connection, player_id: int) -> tuple | None:
    return connection.execute(
        "SELECT skin_color, beard, muscularity, single_morphs, multi_morphs "
        "FROM Customize_table WHERE player_id = ?",
        (player_id,),
    ).fetchone()


def test_parse_options(appearance_options: AppearanceOptions) -> None:
    options = appearance_options
    assert options.options("skin_color") == [
        ("Euro01", "Eurosouth"),
        ("African01", "African"),
    ]
    assert options.options("hairstyle")[:2] == [
        ("NONE", "None"),
        ("1061_Hair06", "Type 7"),
    ]
    assert options.options("shape_body") == [
        ("", "None"),
        ("005_overall_typ3", "Type 1"),
        ("006_overall_typ4", "Type 2"),
    ]
    # Lips belong to the mouth, even though the game also lists them under eyes.
    assert options.options("shape_eyes") == [
        ("", "None"),
        ("035_head_eyes_hang", "Hang"),
    ]
    assert ("024_head_lips_thin", "Thin") in options.options("shape_mouth")
    assert options.options("brow") == []  # not in this config
    keys = [key for key, _ in options.keys()]
    assert "muscularity" in keys and "shape_nose_amount" in keys


@pytest.mark.parametrize(
    "xml",
    [
        b"<a><b></a>",
        b'<!DOCTYPE a [<!ENTITY x "y">]><a>&x;</a>',
        b"<a>" + b" " * (1024 * 1024) + b"</a>",
    ],
    ids=["broken", "entities", "too large"],
)
def test_parse_options_refusals(xml: bytes) -> None:
    with pytest.raises(ConfigError):
        parse_options(xml)


def test_get(looks: AppearanceRepository) -> None:
    look = looks.get(PLAYER_WITHOUT_IMAGE)
    # Names stored as .tga or empty match the game's options.
    assert look["beard"] == "Bart02_diff.dds"
    assert look["eye_color"] == "augen_blau.dds"
    assert look["muscularity"] == "50"
    assert (look["shape_eyes"], look["shape_eyes_amount"]) == (
        "035_head_eyes_hang",
        "100",
    )
    assert (look["shape_head"] if "shape_head" in look else "") == ""
    assert looks.get(PLAYER_WITH_IMAGE) is None  # the game's default look


def test_set_changes_only_what_changed(
    looks: AppearanceRepository, connection: sqlite3.Connection
) -> None:
    looks.set(
        PLAYER_WITHOUT_IMAGE,
        {
            "skin_color": "african01",
            "muscularity": "80",
            "shape_nose": "022_head_nose_hooked",
        },
    )
    skin, beard, muscularity, single, multi = row(connection, PLAYER_WITHOUT_IMAGE)
    assert (skin, beard, muscularity) == ("African01", "Bart02_diff.tga", 0.8)
    assert parse_morphs(single) == [
        ("035_head_eyes_hang", 1.0),
        ("028_head_eggshape", 0.0),
        ("022_head_nose_hooked", 1.0),
    ]
    assert multi == "033_head_eyes_small|032_head_eyes_big|:0.5!1;"
    # One shape per category: removing the eye shape drops its entry.
    looks.set(PLAYER_WITHOUT_IMAGE, {"shape_eyes": ""})
    assert "035_head_eyes_hang" not in row(connection, PLAYER_WITHOUT_IMAGE)[3]
    looks.set(PLAYER_WITHOUT_IMAGE, {"shape_nose_amount": "40"})
    assert "022_head_nose_hooked|0.4;" in row(connection, PLAYER_WITHOUT_IMAGE)[3]


@pytest.mark.parametrize(
    "changes",
    [
        {"skin_color": "Martian"},
        {"muscularity": "101"},
        {"muscularity": "-1"},
        {"shape_nose": "035_head_eyes_hang"},  # an eye shape, not a nose
        {"brow": "NONE"},  # not in this config
        {"player_id": "3"},
        {"skin_color": "African01", "beard": "Bart99"},  # all or nothing
    ],
)
def test_invalid_changes_write_nothing(
    looks: AppearanceRepository, connection: sqlite3.Connection, changes: dict
) -> None:
    before = row(connection, PLAYER_WITHOUT_IMAGE)
    with pytest.raises(ValidationError) as error:
        looks.set(PLAYER_WITHOUT_IMAGE, changes)
    assert error.value.field in changes
    assert row(connection, PLAYER_WITHOUT_IMAGE) == before


def test_new_row_and_default_look(
    looks: AppearanceRepository, connection: sqlite3.Connection
) -> None:
    looks.set(PLAYER_WITH_IMAGE, {"hairstyle_color": "green_001.dds"})
    look = looks.get(PLAYER_WITH_IMAGE)
    assert look["hairstyle_color"] == "green_001.dds"
    assert look["hairstyle"] == "1061_Hair07"  # the usual value
    assert look["skin_color"] == "Euro01"
    assert looks.remove(PLAYER_WITH_IMAGE)
    assert looks.get(PLAYER_WITH_IMAGE) is None
    assert not looks.remove(PLAYER_WITH_IMAGE)


def test_activity_log(
    looks: AppearanceRepository, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level("INFO", logger="app.activity"):
        looks.set(PLAYER_WITH_IMAGE, {"skin_color": "African01"})
        looks.remove(PLAYER_WITH_IMAGE)
    assert caplog.messages == [
        f"player {PLAYER_WITH_IMAGE}: custom look created",
        f"player {PLAYER_WITH_IMAGE}: look skin_color 'Euro01' -> 'African01'",
        f"player {PLAYER_WITH_IMAGE}: custom look removed",
    ]


def test_revert_restores_the_look(
    looks: AppearanceRepository, connection: sqlite3.Connection, tmp_path: Path
) -> None:
    original = tmp_path / "original.db"
    build_game_db(original)
    before = row(connection, PLAYER_WITHOUT_IMAGE)
    looks.set(PLAYER_WITHOUT_IMAGE, {"skin_color": "African01"})
    looks.set(PLAYER_WITH_IMAGE, {"skin_color": "African01"})
    players = PlayerRepository(connection)
    players.revert_player(PLAYER_WITHOUT_IMAGE, original)
    players.revert_player(PLAYER_WITH_IMAGE, original)
    assert row(connection, PLAYER_WITHOUT_IMAGE) == before
    assert row(connection, PLAYER_WITH_IMAGE) is None  # it had no look


def test_check_writes_nothing(
    looks: AppearanceRepository, connection: sqlite3.Connection
) -> None:
    before = row(connection, PLAYER_WITH_IMAGE)
    assert looks.check({"skin_color": "African01"}) == {"skin_color": "African01"}
    with pytest.raises(ValidationError) as error:
        looks.check({"muscularity": "500"})
    assert error.value.field == "muscularity"
    assert row(connection, PLAYER_WITH_IMAGE) == before


def test_parse_options_keeps_the_swatch_textures(
    appearance_options: AppearanceOptions,
) -> None:
    assert appearance_options.textures["skin_color"] == {
        "Euro01": "x/Custom_Skin_Eurosouth.png",
        "African01": "x/Custom_Skin_African.png",
    }
    assert "hairstyle" not in appearance_options.textures  # types, no colour


def test_load_swatches(swatch_options: AppearanceOptions) -> None:
    assert set(swatch_options.swatches) == {
        "skin_color",
        "hairstyle_color",
        "eye_color",
    }
    assert (
        swatch_options.swatches["skin_color"]["African01"]
        == (SWATCH_ENTRIES[SWATCH_DIR + "Custom_Skin_African.png"])
    )


@pytest.mark.parametrize(
    "wrong",
    [
        {"Custom_Hair_Green.png": None},  # missing
        {"Custom_Hair_Green.png": b"not a picture"},
        {"Custom_Hair_Green.png": PNG_SIGNATURE + bytes(MAX_SWATCH_BYTES)},
    ],
    ids=["missing", "not a png", "too large"],
)
def test_a_key_without_every_swatch_gets_none(
    tmp_path: Path, appearance_options: AppearanceOptions, wrong: dict
) -> None:
    entries = dict(SWATCH_ENTRIES)
    for name, data in wrong.items():
        if data is None:
            del entries[SWATCH_DIR + name]
        else:
            entries[SWATCH_DIR + name] = data
    path = tmp_path / SWATCH_ARCHIVE
    build_portrait_archive(path, entries)
    swatches = load_swatches(path, appearance_options)
    assert "hairstyle_color" not in swatches
    assert "skin_color" in swatches


def test_no_swatch_archive_means_no_swatches(
    tmp_path: Path, appearance_options: AppearanceOptions
) -> None:
    assert load_swatches(tmp_path / "missing.v", appearance_options) == {}
    broken = tmp_path / SWATCH_ARCHIVE
    broken.write_bytes(b"not an archive")
    assert load_swatches(broken, appearance_options) == {}


def test_load_options_attaches_the_swatches(tmp_path: Path) -> None:
    game = tmp_path / "game"
    build_portrait_archive(
        game / "gfx.v", {"XMLData/CharacterCustomizationConfig.xml": APPEARANCE_XML}
    )
    build_portrait_archive(game / SWATCH_ARCHIVE, SWATCH_ENTRIES)
    options = load_options(game / "gfx.v")
    assert set(options.swatches) == {"skin_color", "hairstyle_color", "eye_color"}
