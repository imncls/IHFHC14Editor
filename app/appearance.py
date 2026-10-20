"""Players' 3D look (Customize_table), with the values the game itself allows.

The valid values come from the game's XMLData/CharacterCustomizationConfig.xml
inside gfx.v (the list its own "my player" screen uses), and the colour
swatches of that screen from guimisc.v; both are read at runtime so no game
data is copied into this repository. See docs/game-data.md.
"""

import dataclasses
import logging
import re
import sqlite3
import xml.etree.ElementTree as ElementTree
from dataclasses import dataclass, field
from pathlib import Path

from app.errors import AppError, ConfigError, ValidationError
from app.portraits import PNG_SIGNATURE, open_archive

CONFIG_ENTRY = "xmldata/charactercustomizationconfig.xml"
MAX_CONFIG_BYTES = 1024 * 1024
# The game archive with the swatch images, next to gfx.v.
SWATCH_ARCHIVE = "guimisc.v"
MAX_SWATCH_BYTES = 64 * 1024
NONE = "NONE"
NO_SHAPE = ""
# Customize_table text columns and the config list of their values.
CHOICE_LISTS = {
    "skin_color": "SkinColors",
    "hairstyle": "HairStyles",
    "hairstyle_color": "HairStyleColors",
    "beard": "Beards",
    "beard_color": "BeardColors",
    "brow": "EyeBrows",
    "brow_color": "EyeBrowColors",
    "eye_color": "EyeColors",
    "body_hairstyle": "BodyHairStyles",
}
# 0 to 1 in the table, 0 to 100 in the editor.
SLIDERS = ("muscularity", "skin_brightness")
# Shape categories of single_morphs: config list and the morph name parts
# that belong to it (the config also lists lips under eyes and ears).
SHAPES = {
    "body": ("BodyTypes", ("overall",)),
    "head": ("HeadShapes", ("shape",)),
    "eyes": ("EyeShapes", ("eyes",)),
    "nose": ("NoseShapes", ("nose",)),
    "chin": ("ChinShapes", ("chin", "cheeks")),
    "ears": ("EarShapes", ("ears",)),
    "mouth": ("MouthShapes", ("lips", "mouth")),
}
# A new row starts with common values of the game's data.
DEFAULT_LOOK = {
    "muscularity": 0.5,
    "skin_brightness": 0.0,
    "skin_color": "Euro01",
    "hairstyle": "1061_Hair07",
    "hairstyle_color": "brown_001.dds",
    "beard": NONE,
    "beard_color": "dunkel_braun",
    "brow": "braue03_diff.dds",
    "brow_color": "braun",
    "eye_color": "augen_blau.dds",
    "body_hairstyle": NONE,
    "single_morphs": "",
    "multi_morphs": "",
}
_TYPE = re.compile(r"_TYPE_(\d+)$")
_COMMENT = re.compile(rb"<!--.*?-->", re.DOTALL)
_LABELS = {
    "skin_color": "Skin",
    "skin_brightness": "Skin tone (0-100)",
    "muscularity": "Muscularity (0-100)",
    "hairstyle": "Hair",
    "hairstyle_color": "Hair colour",
    "beard": "Beard",
    "beard_color": "Beard colour",
    "brow": "Eyebrows",
    "brow_color": "Eyebrow colour",
    "eye_color": "Eye colour",
    "body_hairstyle": "Body hair",
}

activity = logging.getLogger("app.activity")
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AppearanceOptions:
    """Allowed values: column or shape key -> [(value, label)].

    `textures` names the swatch image of a value (key -> value -> path in the
    game's archives); `swatches` holds those images (key -> value -> PNG) for
    the keys whose every value has one.
    """

    choices: dict[str, list[tuple[str, str]]]
    shapes: dict[str, list[tuple[str, str]]] = field(default_factory=dict)
    textures: dict[str, dict[str, str]] = field(default_factory=dict)
    swatches: dict[str, dict[str, bytes]] = field(default_factory=dict)

    def keys(self) -> list[tuple[str, str]]:
        """(key, label) of every editable field, in form order."""
        keys = [(key, _LABELS[key]) for key in CHOICE_LISTS if key in self.choices]
        keys += [(key, _LABELS[key]) for key in SLIDERS]
        for name in self.shapes:
            keys.append((shape_key(name), f"{name.capitalize()} shape"))
            keys.append((amount_key(name), f"{name.capitalize()} amount (0-100)"))
        return keys

    def options(self, key: str) -> list[tuple[str, str]]:
        if key in self.choices:
            return self.choices[key]
        for name, shapes in self.shapes.items():
            if key == shape_key(name):
                return [(NO_SHAPE, "None"), *shapes]
        return []


def shape_key(name: str) -> str:
    return f"shape_{name}"


def amount_key(name: str) -> str:
    return f"shape_{name}_amount"


def parse_options(xml: bytes) -> AppearanceOptions:
    """The editable values from the game's customization config."""
    if len(xml) > MAX_CONFIG_BYTES:
        raise ConfigError("The game's appearance list is too large.")
    # Without a DTD there are no entities, so no entity expansion attacks.
    if b"<!DOCTYPE" in xml.upper() or b"<!ENTITY" in xml.upper():
        raise ConfigError("The game's appearance list cannot be read.")
    # The game's comments contain "--", which strict XML parsers refuse.
    xml = _COMMENT.sub(b"", xml)
    try:
        root = ElementTree.fromstring(xml)  # noqa: S314 - no DTD, size-capped
    except ElementTree.ParseError:
        raise ConfigError("The game's appearance list cannot be read.") from None
    choices = {
        key: [_choice(item) for item in root.iterfind(f"{tag}/CustomizeData")]
        for key, tag in CHOICE_LISTS.items()
    }
    shapes = {}
    for name, (tag, parts) in SHAPES.items():
        shapes[name] = [
            (item.get("internalID", ""), _shape_label(item, name))
            for item in root.iterfind(f"{tag}/CustomizeData")
            if any(part in item.get("internalID", "") for part in parts)
        ]
    textures = {
        key: {
            item.get("internalID", ""): item.get("texturePath", "")
            for item in root.iterfind(f"{tag}/CustomizeData")
            if item.get("texturePath")
        }
        for key, tag in CHOICE_LISTS.items()
    }
    return AppearanceOptions(
        {key: values for key, values in choices.items() if values},
        {name: values for name, values in shapes.items() if values},
        {key: paths for key, paths in textures.items() if paths},
    )


def load_options(gfx_path: Path) -> AppearanceOptions:
    """Read the allowed values from the game's gfx.v."""
    if not gfx_path.is_file():
        raise ConfigError("The game's gfx.v was not found.")
    archive = open_archive(gfx_path.read_bytes())
    for info in archive.infolist():
        if info.filename.casefold() == CONFIG_ENTRY:
            if info.file_size > MAX_CONFIG_BYTES:
                raise ConfigError("The game's appearance list is too large.")
            options = parse_options(archive.read(info))
            swatches = load_swatches(gfx_path.parent / SWATCH_ARCHIVE, options)
            return dataclasses.replace(options, swatches=swatches)
    raise ConfigError("The game's appearance list was not found in gfx.v.")


def load_swatches(
    archive_path: Path, options: AppearanceOptions
) -> dict[str, dict[str, bytes]]:
    """The swatch images of the colour choices, from the game's archive.

    A key gets swatches only if every one of its values has one, so a palette
    never misses an option. Without the archive there are none.
    """
    try:
        archive = open_archive(archive_path.read_bytes())
    except (OSError, AppError) as error:
        logger.warning("Colour swatches unavailable: %s", error)
        return {}
    # Paths use "\\" in the config and "/" in the archive: match file names.
    entries = {
        info.filename.replace("\\", "/").rsplit("/", 1)[-1].casefold(): info
        for info in archive.infolist()
    }
    swatches = {}
    for key, values in options.choices.items():
        paths = options.textures.get(key, {})
        images = {}
        for value, _label in values:
            name = paths.get(value, "").replace("\\", "/").rsplit("/", 1)[-1]
            info = entries.get(name.casefold()) if name else None
            if info is None or info.file_size > MAX_SWATCH_BYTES:
                break
            png = archive.read(info)
            if not png.startswith(PNG_SIGNATURE):
                break
            images[value] = png
        else:
            swatches[key] = images
    return swatches


def _choice(item: ElementTree.Element) -> tuple[str, str]:
    value = item.get("internalID", "")
    texture = item.get("texturePath", "")
    if texture:
        # gui\...\Custom_Hair_Darkbrown.png -> Darkbrown
        label = Path(texture.replace("\\", "/")).stem.rsplit("_", 1)[-1]
    else:
        label = _type_label(item)
    return value, label


def _shape_label(item: ElementTree.Element, name: str) -> str:
    if name == "body":
        return _type_label(item)
    # 022_head_nose_hooked -> Hooked
    return item.get("internalID", "").rsplit("_", 1)[-1].capitalize()


def _type_label(item: ElementTree.Element) -> str:
    match = _TYPE.search(item.get("locaID", ""))
    return f"Type {match.group(1)}" if match else "None"


def allowed_value(options: AppearanceOptions, key: str, value: str) -> bool:
    """Whether a stored look value is one the game allows. The game's data
    mixes .tga and .dds names for the same texture, so the extension is
    ignored; NONE and an empty value are allowed too, and so is anything for
    a list the game's config does not have."""
    choices = options.options(key)
    if value in {"", NONE} or not choices:
        return True
    return any(_stem(allowed) == _stem(value) for allowed, _ in choices)


def _stem(value: str) -> str:
    return value.rsplit(".", 1)[0].casefold() if "." in value else value.casefold()


def parse_morphs(text: str | None) -> list[tuple[str, float]]:
    """single_morphs as [(name, value)]: 'name|value;...'."""
    morphs = []
    for part in (text or "").split(";"):
        name, _, value = part.partition("|")
        if not name:
            continue
        try:
            morphs.append((name, float(value)))
        except ValueError:
            morphs.append((name, 0.0))
    return morphs


def format_morphs(morphs: list[tuple[str, float]]) -> str:
    return "".join(f"{name}|{value:g};" for name, value in morphs)


class AppearanceRepository:
    def __init__(self, connection: sqlite3.Connection, options: AppearanceOptions):
        self._connection = connection
        self.options = options

    def get(self, player_id: int) -> dict[str, str] | None:
        """The look as the editor shows it, or None for the game's default."""
        row = self._row(player_id)
        return None if row is None else self._display(row)

    def default(self) -> dict[str, str]:
        """What a new row starts with."""
        return self._display(self._default_row())

    def set(self, player_id: int, changes: dict[str, str]) -> dict[str, str]:
        """Validate every change, then store them in one transaction.

        A player without a row gets one, from the default look plus changes.
        """
        values = self.check(changes)
        current = self._row(player_id)
        created = current is None
        row = self._default_row() if created else current
        before = self._display(row)
        for key, value in values.items():
            if key in SLIDERS:
                row[key] = int(value) / 100
            elif key in CHOICE_LISTS:
                row[key] = value
        changed_shapes = [
            name
            for name in SHAPES
            if shape_key(name) in values or amount_key(name) in values
        ]
        if changed_shapes:
            morphs = parse_morphs(str(row["single_morphs"] or ""))
            for name in changed_shapes:
                morphs = self._set_shape(morphs, name, values, before)
            row["single_morphs"] = format_morphs(morphs)
        columns = list(row)
        with self._connection:
            self._connection.execute(
                "DELETE FROM Customize_table WHERE player_id = ?", (player_id,)
            )
            self._connection.execute(
                f"INSERT INTO Customize_table (player_id, {', '.join(columns)}) "  # noqa: S608 - columns from DEFAULT_LOOK
                f"VALUES (?{', ?' * len(columns)})",
                (player_id, *row.values()),
            )
        if created:
            activity.info("player %s: custom look created", player_id)
        for key, value in values.items():
            activity.info(
                "player %s: look %s %r -> %r", player_id, key, before.get(key), value
            )
        return values

    def check(self, changes: dict[str, str]) -> dict[str, str]:
        """Validate changes without writing. A ValidationError names the field."""
        values = {}
        for key, raw in changes.items():
            try:
                values[key] = self._validate(key, raw)
            except ValidationError as error:
                raise ValidationError(str(error), key) from None
        return values

    def remove(self, player_id: int) -> bool:
        """Delete the row: the game then uses its default look."""
        with self._connection:
            removed = self._connection.execute(
                "DELETE FROM Customize_table WHERE player_id = ?", (player_id,)
            ).rowcount
        if removed:
            activity.info("player %s: custom look removed", player_id)
        return bool(removed)

    def _row(self, player_id: int) -> dict[str, object] | None:
        columns = list(DEFAULT_LOOK)
        row = self._connection.execute(
            f"SELECT {', '.join(columns)} FROM Customize_table WHERE player_id = ?",  # noqa: S608 - columns from DEFAULT_LOOK
            (player_id,),
        ).fetchone()
        return None if row is None else dict(zip(columns, row, strict=True))

    def _default_row(self) -> dict[str, object]:
        row = dict(DEFAULT_LOOK)
        for key in CHOICE_LISTS:
            ids = [value for value, _ in self.options.options(key)]
            if ids and row[key] not in ids:
                row[key] = ids[0]
        return row

    def _display(self, row: dict[str, object]) -> dict[str, str]:
        shown = {}
        for key in CHOICE_LISTS:
            shown[key] = self._match(key, str(row[key] or ""))
        for key in SLIDERS:
            shown[key] = str(round(float(row[key] or 0) * 100))
        morphs = parse_morphs(str(row["single_morphs"] or ""))
        for name, shapes in self.options.shapes.items():
            names = {value for value, _ in shapes}
            found = [(value, morph) for morph, value in morphs if morph in names]
            value, morph = max(found, default=(0.0, NO_SHAPE))
            shown[shape_key(name)] = morph if value > 0 else NO_SHAPE
            shown[amount_key(name)] = str(round(value * 100)) if value > 0 else "0"
        return shown

    def _match(self, key: str, stored: str) -> str:
        """The option for a stored value: same name, any extension or case."""
        options = [value for value, _ in self.options.options(key)]
        if not stored and NONE in options:
            return NONE
        return next(
            (value for value in options if _stem(value) == _stem(stored)), stored
        )

    def _set_shape(
        self,
        morphs: list[tuple[str, float]],
        name: str,
        values: dict[str, str],
        before: dict[str, str],
    ) -> list[tuple[str, float]]:
        """One shape per category: its entries are replaced, others kept."""
        names = {value for value, _ in self.options.shapes.get(name, [])}
        shape = values.get(shape_key(name), before.get(shape_key(name), NO_SHAPE))
        amount = int(values.get(amount_key(name), before.get(amount_key(name), "0")))
        if amount == 0 and amount_key(name) not in values:
            amount = 100  # a shape just chosen, with no amount yet, shows fully
        kept = [(morph, value) for morph, value in morphs if morph not in names]
        if shape == NO_SHAPE or amount == 0:
            return kept
        return [*kept, (shape, amount / 100)]

    def _validate(self, key: str, raw: str) -> str:
        value = raw.strip()
        if key in SLIDERS or key.endswith("_amount"):
            if key not in SLIDERS and key not in {
                amount_key(name) for name in self.options.shapes
            }:
                raise ValidationError("Unknown appearance field.")
            if not value.isascii() or not value.isdigit() or int(value) > 100:
                raise ValidationError("The value must be a whole number, 0 to 100.")
            return str(int(value))
        options = self.options.options(key)
        if not options:
            raise ValidationError("Unknown appearance field.")
        allowed = [option for option, _ in options]
        for option in allowed:
            if option.casefold() == value.casefold():
                return option
        listed = ", ".join(option or "(none)" for option in allowed)
        raise ValidationError(f"Not a value the game allows. Allowed: {listed}.")
