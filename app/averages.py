"""Player averages, derived from the game's data (see docs/game-data.md)."""

import struct

SKILLS = (
    "sk_fit",
    "sk_throw",
    "sk_acc",
    "sk_pass",
    "sk_speed",
    "sk_jump",
    "sk_power",
    "sk_mob",
    "sk_block",
    "sk_comp",
    "sk_react",
)
AVERAGES = ("total_avg", "total_def_avg", "total_off_avg")
GOALKEEPER = 28
# Integer weights per skill, in SKILLS order.
DEF_WEIGHTS = (2, 1, 1, 1, 1, 2, 5, 3, 5, 2, 3)
OFF_WEIGHTS = (3, 2, 3, 3, 4, 2, 2, 3, 1, 4, 1)
_WING = (5, 2, 5, 1, 5, 3, 1, 4, 1, 1, 1)
_BACK = (3, 5, 4, 2, 1, 2, 3, 1, 1, 3, 1)
TOTAL_WEIGHTS = {
    22: _WING,
    23: _BACK,
    24: (3, 3, 3, 5, 2, 1, 2, 3, 1, 5, 1),
    25: _BACK,
    26: _WING,
    27: (2, 1, 4, 1, 3, 2, 5, 3, 1, 3, 3),
}


def stored_float(value: float) -> float:
    """Round to 2 decimals and to float32, as the game stores averages."""
    return struct.unpack("f", struct.pack("f", round(value, 2)))[0]


def _weighted(weights: tuple[int, ...], skills: dict[str, int]) -> float:
    total = sum(
        weight * skills[key] for weight, key in zip(weights, SKILLS, strict=True)
    )
    return stored_float(total / sum(weights))


def player_averages(
    skills: dict[str, int], main_pos: int
) -> tuple[float, float, float] | None:
    """(total, defense, offense), or None when there is no known formula.

    Goalkeepers have no known formula: their averages are set by hand.
    """
    weights = TOTAL_WEIGHTS.get(main_pos)
    if weights is None:
        return None
    return (
        _weighted(weights, skills),
        _weighted(DEF_WEIGHTS, skills),
        _weighted(OFF_WEIGHTS, skills),
    )
