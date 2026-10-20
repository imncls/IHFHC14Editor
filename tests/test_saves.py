from pathlib import Path

import pytest

from app.saves import describe, save_games, saves_using

LEAGUE_SAVE = """<?xml version="1.0" encoding="utf-8" standalone="yes" ?>
<SaveGame id="2" name="Friends FC" playerTeamID="339" progress="0" leagueID="19"
    custom="false" genericLeague="true">
    <BuildTemplate>
        <MatchTemplate id="1" matchDay="1" homeTeam="339" guestTeam="340" />
        <MatchTemplate id="2" matchDay="1" homeTeam="341" guestTeam="342" />
    </BuildTemplate>
</SaveGame>
"""


@pytest.fixture
def folder(tmp_path: Path) -> Path:
    (tmp_path / "SGT_LEAGUE.2.sg").write_text(LEAGUE_SAVE, encoding="utf-8")
    (tmp_path / "SGT_LEAGUE.3.sg").write_text("<SaveGame", encoding="utf-8")
    (tmp_path / "SGT_LEAGUE.4.sg").write_text(
        '<!DOCTYPE a [<!ENTITY x "y">]><SaveGame name="&x;" />', encoding="utf-8"
    )
    (tmp_path / "other.xml").write_text(LEAGUE_SAVE, encoding="utf-8")
    return tmp_path


def test_reads_teams_and_league(folder: Path) -> None:
    # Broken and DTD files are skipped; only SGT_*.sg files count.
    (save,) = save_games(folder)
    assert save.name == "Friends FC"
    assert save.league_id == 19
    assert save.team_ids == {339, 340, 341, 342}
    assert describe([save]) == "Friends FC (SGT_LEAGUE.2.sg)"


def test_saves_using(folder: Path) -> None:
    assert [s.name for s in saves_using(folder, team_ids={342})] == ["Friends FC"]
    assert [s.name for s in saves_using(folder, league_ids={19})] == ["Friends FC"]
    assert saves_using(folder, team_ids={105}, league_ids={4}) == []
    assert saves_using(folder / "missing", team_ids={339}) == []
