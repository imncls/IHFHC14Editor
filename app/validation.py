"""Validation of values typed by a person: their format only.

Pure functions, without the database: lengths, characters, ranges, dates,
a choice among given ids. Checks that need the game data (a name already
used, a country or a team that exists) live in the repositories, which call
these functions for the format part.
"""

import re
from datetime import date, datetime
from pathlib import PurePath

from app.averages import SKILLS
from app.errors import ValidationError

# Observed ranges from docs/game-data.md.
INT_RANGES = {
    "jersey_nr": (0, 100),
    "national_team_jersey_nr": (0, 100),
    "star_pot": (1, 100),
    "prone_to_inj": (1, 100),
    "weight": (0, 179),
    "height": (0, 212),
    **{skill: (0, 100) for skill in SKILLS},
    # Team tactics and level (playerDBTeam).
    **{
        key: (0, 100)
        for key in (
            "off_team_speed",
            "off_team_1vs1",
            "off_plays",
            "off_crossing",
            "off_passing",
            "off_tricks",
            "off_focus",
            "def_position",
            "def_helping",
            "def_aggressive",
            "def_holding",
            "def_intercept",
            "min_skill_average",
            "max_skill_average",
        )
    },
}
OPTIONAL_INT_FIELDS = {"national_team_jersey_nr"}
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".bmp"}
IMAGE_MAX_LENGTH = 100
# The game's data uses the backtick as an apostrophe too.
NAME_PUNCTUATION = set(" -'’`.()")
# Team names also use digits and a few more signs ("Füchse Berlin!", "Team_1").
LABEL_PUNCTUATION = NAME_PUNCTUATION | set("!&_")
MIN_BIRTH_YEAR = 1900
AVERAGE_PATTERN = re.compile(r"\d{1,3}(\.\d{1,2})?", re.ASCII)
ABBREV_PATTERN = re.compile(r"[A-Z0-9]{1,3}", re.ASCII)
RATING_PATTERN = re.compile(r"\d(\.[05])?", re.ASCII)
COLOUR_PATTERN = re.compile(r"#?[0-9A-Fa-f]{6}", re.ASCII)
MAX_RATING = 5


def validate_text(raw: str, max_length: int) -> str:
    value = raw.strip()
    if not value:
        raise ValidationError("The value cannot be empty.")
    if len(value) > max_length:
        raise ValidationError(f"The value is longer than {max_length} characters.")
    if not all(char.isalpha() or char in NAME_PUNCTUATION for char in value):
        raise ValidationError(
            "Only letters, spaces, hyphens, apostrophes, periods "
            "and parentheses are allowed."
        )
    return value


def validate_label(raw: str, max_length: int, required: bool = True) -> str:
    """A team text: like a name, but digits and !&_ are allowed too."""
    value = raw.strip()
    if not value:
        if required:
            raise ValidationError("The value cannot be empty.")
        return value
    if len(value) > max_length:
        raise ValidationError(f"The value is longer than {max_length} characters.")
    if not all(char.isalnum() or char in LABEL_PUNCTUATION for char in value):
        raise ValidationError(
            "Only letters, digits, spaces and - ' . ( ) ! & _ are allowed."
        )
    return value


def validate_abbrev(raw: str) -> str:
    value = raw.strip().upper()
    if not ABBREV_PATTERN.fullmatch(value):
        raise ValidationError("The abbreviation must be 1 to 3 letters or digits.")
    return value


def validate_rating(raw: str) -> str:
    """Stars: 0.5 to 5 in steps of 0.5."""
    value = raw.strip()
    if not RATING_PATTERN.fullmatch(value) or not 0.5 <= float(value) <= MAX_RATING:
        raise ValidationError("The stars must be 0.5 to 5, in steps of 0.5.")
    return f"{float(value):.1f}"


def validate_colour(raw: str) -> str:
    """A colour as RRGGBB (hexadecimal), stored as an integer. Empty is 0."""
    value = raw.strip()
    if not value:
        return "0"
    if not COLOUR_PATTERN.fullmatch(value):
        raise ValidationError("The colour must be 6 hexadecimal digits, like FF8800.")
    return str(int(value.removeprefix("#"), 16))


def validate_image(raw: str) -> str:
    value = raw.strip()
    if not value:
        return value
    if (
        len(value) > IMAGE_MAX_LENGTH
        or not value.isprintable()
        or any(separator in value for separator in "/\\:")
        or ".." in value
        or PurePath(value).name != value
    ):
        raise ValidationError("The image must be a plain file name.")
    if PurePath(value).suffix.lower() not in IMAGE_EXTENSIONS:
        raise ValidationError("The image must be a .jpg, .jpeg, .png, .gif or .bmp.")
    return value


def validate_int(key: str, raw: str) -> str:
    value = raw.strip()
    if not value and key in OPTIONAL_INT_FIELDS:
        return value
    if key not in INT_RANGES:
        raise ValidationError("This number field has no known range.")
    low, high = INT_RANGES[key]
    if not value.isascii() or not value.isdigit():
        raise ValidationError("The value must be a whole number.")
    number = int(value)
    if not low <= number <= high:
        raise ValidationError(f"The value must be between {low} and {high}.")
    return str(number)


def validate_date(raw: str, today: date | None = None) -> str:
    try:
        parsed = datetime.strptime(raw.strip(), "%d.%m.%Y").date()
    except ValueError:
        raise ValidationError("The date must be a real date as d.m.yyyy.") from None
    if not MIN_BIRTH_YEAR <= parsed.year or parsed > (today or date.today()):
        raise ValidationError(f"The date must be between {MIN_BIRTH_YEAR} and today.")
    # Most dates in the game's data have no leading zeros.
    return f"{parsed.day}.{parsed.month}.{parsed.year}"


def validate_choice(raw: str, allowed_ids: set[int]) -> str:
    value = raw.strip()
    if not value.isascii() or not value.isdigit() or int(value) not in allowed_ids:
        raise ValidationError("The value is not one of the allowed options.")
    return str(int(value))


def validate_average(raw: str) -> str:
    """A hand-set average: 0 to 100 with at most 2 decimals."""
    value = raw.strip()
    if not AVERAGE_PATTERN.fullmatch(value) or not 0 <= float(value) <= 100:
        raise ValidationError("The value must be between 0 and 100, like 85.5.")
    return f"{float(value):.2f}"
