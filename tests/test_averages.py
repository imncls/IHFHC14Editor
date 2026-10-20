import pytest

from app.averages import GOALKEEPER, SKILLS, player_averages, stored_float

# Invented players: skills, main position and the averages. The formula was
# derived from, and checked against, the game's own data (docs/game-data.md);
# no player of the game is copied here.
CASES = [
    # centre back
    ((80, 77, 84, 79, 82, 76, 74, 81, 73, 85, 83), 24, (80.34, 78.12, 80.36)),
    # pivot
    ((72, 88, 79, 70, 75, 80, 94, 77, 89, 74, 81), 27, (80.64, 82.81, 78.0)),
    # left back
    ((85, 91, 88, 83, 79, 86, 90, 84, 78, 87, 82), 23, (86.69, 84.42, 84.93)),
    # left wing
    ((70, 68, 83, 66, 88, 85, 65, 80, 60, 74, 79), 22, (77.93, 71.73, 75.71)),
]


@pytest.mark.parametrize("skills, main_pos, expected", CASES)
def test_matches_the_game(skills, main_pos, expected) -> None:
    result = player_averages(dict(zip(SKILLS, skills, strict=True)), main_pos)
    assert result == tuple(stored_float(value) for value in expected)


def test_goalkeeper_and_unknown_position_have_no_formula() -> None:
    skills = dict.fromkeys(SKILLS, 70)
    assert player_averages(skills, GOALKEEPER) is None
    assert player_averages(skills, 21) is None


def test_stored_float_is_float32() -> None:
    assert stored_float(80.28) == 80.27999877929688
