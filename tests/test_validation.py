from datetime import date

import pytest

from app.errors import ValidationError
from app.validation import (
    validate_abbrev,
    validate_choice,
    validate_colour,
    validate_date,
    validate_image,
    validate_int,
    validate_label,
    validate_rating,
    validate_text,
)


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("  Ana  ", "Ana"),
        ("José-María", "José-María"),
        ("O'Neil", "O'Neil"),
        ("D`Souza", "D`Souza"),
        ("Jr. (Fake)", "Jr. (Fake)"),
    ],
)
def test_text_accepts_names(raw: str, expected: str) -> None:
    assert validate_text(raw, 20) == expected


@pytest.mark.parametrize(
    "raw", ["", "   ", "Ana\x00", "Ana\tB", "R2D2", "a<b>", "x" * 21]
)
def test_text_rejects_invalid(raw: str) -> None:
    with pytest.raises(ValidationError):
        validate_text(raw, 20)


@pytest.mark.parametrize(
    "raw", ["", "MOREL.jpg", "a b.JPEG", "x.png", "x.gif", "x.bmp"]
)
def test_image_accepts_file_names(raw: str) -> None:
    assert validate_image(raw) == raw


@pytest.mark.parametrize(
    "raw",
    ["../x.jpg", "a/x.jpg", "a\\x.jpg", "C:x.jpg", "x.exe", "x", "x.jpg\x00", "..jpg"],
)
def test_image_rejects_paths_and_other_types(raw: str) -> None:
    with pytest.raises(ValidationError):
        validate_image(raw)


def test_int_in_range() -> None:
    assert validate_int("height", " 212 ") == "212"
    assert validate_int("star_pot", "1") == "1"


@pytest.mark.parametrize(
    "key, raw",
    [
        ("height", "213"),
        ("star_pot", "0"),
        ("weight", "-1"),
        ("weight", "7.5"),
        ("weight", "١٢"),
        ("weight", ""),
        ("unknown", "1"),
    ],
)
def test_int_rejects_invalid(key: str, raw: str) -> None:
    with pytest.raises(ValidationError):
        validate_int(key, raw)


def test_optional_int_allows_empty() -> None:
    assert validate_int("national_team_jersey_nr", "") == ""


@pytest.mark.parametrize("raw", ["31.8.1990", "31.08.1990", " 31.08.1990 "])
def test_date_is_written_without_leading_zeros(raw: str) -> None:
    assert validate_date(raw) == "31.8.1990"


@pytest.mark.parametrize(
    "raw", ["31.2.1990", "1990-08-31", "", "1.1.1899", "1.1.2031", "a.b.c"]
)
def test_date_rejects_invalid(raw: str) -> None:
    with pytest.raises(ValidationError):
        validate_date(raw, today=date(2030, 1, 1))


def test_choice() -> None:
    assert validate_choice(" 22 ", {22, 28}) == "22"
    for raw in ("23", "", "x", "22.0"):
        with pytest.raises(ValidationError):
            validate_choice(raw, {22, 28})


@pytest.mark.parametrize(
    ("raw", "expected"),
    [(" Füchse Berlin ", "Füchse Berlin"), ("Team_1 & Co!", "Team_1 & Co!")],
)
def test_label_accepts_team_names(raw: str, expected: str) -> None:
    assert validate_label(raw, 32) == expected


@pytest.mark.parametrize("raw", ["", "A" * 33, "Team <1>", "a;b", "x\ny"])
def test_label_rejects_invalid(raw: str) -> None:
    with pytest.raises(ValidationError):
        validate_label(raw, 32)


def test_optional_label_allows_empty() -> None:
    assert validate_label("  ", 30, required=False) == ""


def test_abbrev() -> None:
    assert validate_abbrev(" grt ") == "GRT"
    assert validate_abbrev("B1") == "B1"
    for raw in ("", "ABCD", "A-B", "ÑAB"):
        with pytest.raises(ValidationError):
            validate_abbrev(raw)


def test_rating() -> None:
    assert validate_rating("2.5") == "2.5"
    assert validate_rating("5") == "5.0"
    for raw in ("0", "5.5", "2.3", "2.50", "x", ""):
        with pytest.raises(ValidationError):
            validate_rating(raw)


def test_colour() -> None:
    assert validate_colour("FF8800") == str(0xFF8800)
    assert validate_colour("#0a0B0c") == str(0x0A0B0C)
    assert validate_colour("") == "0"
    for raw in ("FF88", "GG8800", "FF88001"):
        with pytest.raises(ValidationError):
            validate_colour(raw)


def test_tactics_have_ranges() -> None:
    assert validate_int("off_team_speed", "90") == "90"
    with pytest.raises(ValidationError):
        validate_int("def_aggressive", "101")
