"""The editor window on the synthetic database (window hidden)."""

import io
import json
import sqlite3
import tkinter as tk
from collections.abc import Iterator
from contextlib import closing
from tkinter import filedialog, messagebox, ttk

import pytest
from conftest import (
    CLUB,
    EMPTY_ORIGINAL_TEAM,
    NATIONAL_TEAM,
    OTHER_CLUB,
    PLAYER_WITH_IMAGE,
    PLAYER_WITHOUT_IMAGE,
    POOL,
)
from PIL import Image

from app import __version__
from app.appearance import AppearanceRepository
from app.config import DLL_NAME, Paths
from app.errors import FolderError
from app.leagues import LeagueRepository
from app.repository import OVERALL
from app.teams import NewTeam, TeamRepository
from app.ui import (
    main_window,
    new_league_dialog,
    new_player_dialog,
    new_team_dialog,
    settings_form,
    team_panel,
    user_facing_error,
)
from app.ui.appearance_dialog import SwatchPicker
from app.ui.main_window import TITLE, MainWindow
from app.ui.new_team_dialog import NEW_LEAGUE
from app.ui.settings_form import SetupWindow
from app.ui.teams_tab import GROUP_PREFIX
from app.ui.widgets import WINDOW_ICON_SIZES, icon_path, set_window_icon
from app.workspace import Workspace


def make_window(workspace: Workspace, options=None) -> Iterator[MainWindow]:
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("Tk cannot create a window here")
    root.withdraw()
    workspace.backup()
    workspace.start()
    window = MainWindow(root, workspace, options)
    yield window
    window._disconnect()
    root.destroy()


@pytest.fixture
def window(workspace: Workspace) -> Iterator[MainWindow]:
    yield from make_window(workspace)


@pytest.fixture
def portrait_window(workspace_with_portraits: Workspace) -> Iterator[MainWindow]:
    yield from make_window(workspace_with_portraits)


@pytest.fixture
def look_window(workspace: Workspace, appearance_options) -> Iterator[MainWindow]:
    yield from make_window(workspace, appearance_options)


@pytest.fixture
def yes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(messagebox, "askyesno", lambda *a, **k: True)


def enabled(button) -> bool:
    return not button.instate(["disabled"])


def open_player(window: MainWindow, player_id: int) -> None:
    window.players.show_team(CLUB)
    window.show_player(player_id)


# Window


def test_tabs_status_bar_and_menu(window: MainWindow) -> None:
    tabs = [window.notebook.tab(tab, "text").strip() for tab in window.notebook.tabs()]
    assert tabs == ["Home", "Players", "Teams", "Leagues", "Settings"]
    # Packed before the tabs, the status bar always keeps its place.
    slaves = window.root.pack_slaves()
    assert slaves.index(window.status_bar) < slaves.index(window.notebook)
    assert window.root.title() == TITLE


def test_apply_counter_and_reload(window: MainWindow, yes: None) -> None:
    open_player(window, PLAYER_WITHOUT_IMAGE)
    window.players.panel.form.set("name", "Pérez")
    assert window.players.save()
    assert window.change_count == 1
    assert window.apply_button.cget("text") == "Apply to game (1)"
    assert window.root.title().endswith("*")
    assert "1 saved change" in window.changes_text.get()
    window.reload()
    assert window.change_count == 0
    assert window.root.title() == TITLE
    assert window.players.panel.player_id is None


def test_ctrl_s_saves_the_current_tab(window: MainWindow) -> None:
    window.show_tab(window.players)
    open_player(window, PLAYER_WITHOUT_IMAGE)
    window.players.panel.form.set("name", "Pérez")
    window.save_current()
    assert not window.players.panel.is_dirty()
    assert window.unapplied


# Players


def test_edit_and_save(window: MainWindow, workspace: Workspace) -> None:
    open_player(window, PLAYER_WITHOUT_IMAGE)
    form = window.players.panel.form
    form.set("name", "Pérez")
    form.set("main_pos", "Goalkeeper")
    assert form.changes() == {"name": "Pérez", "main_pos": "28"}
    assert window.players.save()
    assert not form.is_dirty()
    with closing(sqlite3.connect(workspace.clear_db)) as connection:
        rows = dict(
            connection.execute(
                "SELECT data_id, value FROM playerDBPlayerData WHERE player_id = ?",
                (PLAYER_WITHOUT_IMAGE,),
            ).fetchall()
        )
    assert (rows[1], rows[5]) == ("Pérez", "28")
    assert "Pérez" in window.players.list.item(str(PLAYER_WITHOUT_IMAGE), "values")


def test_skill_change_updates_shown_averages(window: MainWindow) -> None:
    open_player(window, PLAYER_WITHOUT_IMAGE)
    form = window.players.panel.form
    assert "total 50.00" in form.averages.get()
    assert str(form._widgets["overall"].cget("state")) == "disabled"
    form.set("sk_throw", "99")
    assert window.players.save()
    assert "total 50.00" not in form.averages.get()


def test_goalkeeper_overall_is_editable(window: MainWindow) -> None:
    open_player(window, PLAYER_WITH_IMAGE)
    form = window.players.panel.form
    assert str(form._widgets["overall"].cget("state")) == "normal"
    form.set("overall", "88.5")
    assert window.players.save()
    assert "total 88.50" in form.averages.get()


def test_player_buttons_follow_the_player(window: MainWindow) -> None:
    panel = window.players.panel
    assert not enabled(panel.save_button)  # nobody chosen
    open_player(window, PLAYER_WITHOUT_IMAGE)
    assert enabled(panel.revert_button)  # an original player
    assert not enabled(panel.delete_button)
    assert not enabled(panel.appearance_button)  # no list of looks here
    panel.duplicate()
    assert not enabled(panel.revert_button)  # made with the editor
    assert enabled(panel.delete_button)


def test_revert_player(window: MainWindow, yes: None) -> None:
    open_player(window, PLAYER_WITHOUT_IMAGE)
    panel = window.players.panel
    panel.form.set("name", "Pérez")
    assert panel.save()
    panel.revert()
    assert panel.form._variables["name"].get() == "Morel"
    assert window.change_count == 2


def test_player_img_is_not_in_the_form(window: MainWindow) -> None:
    assert "player_img" not in window.players.panel.form._variables


def test_change_and_remove_portrait(
    portrait_window: MainWindow, tmp_path, monkeypatch: pytest.MonkeyPatch, yes: None
) -> None:
    photo = tmp_path / "friend.png"
    Image.new("RGB", (300, 400), "orange").save(photo)
    monkeypatch.setattr(filedialog, "askopenfilename", lambda **kwargs: str(photo))
    window = portrait_window
    portraits = window.workspace.portraits
    panel = window.players.panel
    open_player(window, PLAYER_WITH_IMAGE)
    assert panel._portrait is None  # the fake original is not a real PNG
    assert not enabled(panel.remove_portrait_button)
    panel.change_portrait()
    assert portraits.custom_ids() == [PLAYER_WITH_IMAGE]
    assert panel._portrait.width() == 130
    assert enabled(panel.remove_portrait_button)
    panel.remove_portrait()
    assert portraits.custom_ids() == []


def test_declined_portrait_is_not_saved(
    portrait_window: MainWindow, tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    photo = tmp_path / "friend.jpg"
    Image.new("RGB", (300, 400), "orange").save(photo)
    monkeypatch.setattr(filedialog, "askopenfilename", lambda **kwargs: str(photo))
    monkeypatch.setattr(messagebox, "askyesno", lambda *a, **k: False)
    open_player(portrait_window, PLAYER_WITHOUT_IMAGE)
    portrait_window.players.panel.change_portrait()
    assert portrait_window.workspace.portraits.custom_ids() == []


def test_revert_player_removes_the_custom_portrait(
    portrait_window: MainWindow, yes: None
) -> None:
    portraits = portrait_window.workspace.portraits
    open_player(portrait_window, PLAYER_WITHOUT_IMAGE)
    buffer = io.BytesIO()
    Image.new("RGBA", (130, 158), "blue").save(buffer, "PNG")
    portraits.set(PLAYER_WITHOUT_IMAGE, buffer.getvalue())
    portrait_window.players.panel.revert()
    assert portraits.custom_ids() == []


def test_transfer_from_the_form(window: MainWindow) -> None:
    open_player(window, PLAYER_WITHOUT_IMAGE)
    form = window.players.panel.form
    assert form._variables["club"].get() == "FC Barcelona Intersport"
    form.set("club", "Fraikin BM. Granollers [unused]")
    assert form.changes() == {"club": str(OTHER_CLUB)}
    assert window.players.save()
    # He left the listed roster but stays open; the warning shows.
    assert not window.players.list.exists(str(PLAYER_WITHOUT_IMAGE))
    assert window.players.panel.player_id == PLAYER_WITHOUT_IMAGE
    assert "Fraikin BM. Granollers has 1 players" in window.status.get()
    assert form.changes() == {}
    # The Teams tab refreshes when shown: Granollers now has a player.
    window.show_tab(window.teams)
    assert window.teams.tree.exists(str(OTHER_CLUB))


@pytest.fixture
def full_window(
    workspace_with_portraits: Workspace, appearance_options
) -> Iterator[MainWindow]:
    yield from make_window(workspace_with_portraits, appearance_options)


def looks(window: MainWindow) -> int:
    (count,) = window.connection.execute(
        "SELECT COUNT(*) FROM Customize_table"
    ).fetchone()
    return count


def name(wizard, first_name: str, last_name: str) -> None:
    wizard.form.set("first_name", first_name)
    wizard.form.set("name", last_name)


def test_new_player_wizard_from_the_teams_tab(window: MainWindow) -> None:
    window.show_team(CLUB)
    wizard = window.teams.new_player()
    assert window.notebook.select() == str(window.players)
    values = wizard.form.values()
    assert (values["first_name"], values["name"]) == ("", "")
    assert values["club"] == str(CLUB)
    assert values["jersey_nr"] == window.repository.new_player_values(CLUB)["jersey_nr"]
    assert str(wizard.tabs.tab(wizard.look_tab, "state")) == "disabled"  # no gfx.v
    name(wizard, "Sam", "Taylor")
    wizard.form.set("sk_speed", "80")
    before = looks(window)
    player_id = wizard.create()
    player = window.repository.get_player(player_id)
    assert (player["first_name"], player["name"], player["sk_speed"]) == (
        "Sam",
        "Taylor",
        "80",
    )
    assert player["club"] == str(CLUB)
    assert looks(window) == before  # untouched look: the game's default
    assert window.players.panel.player_id == player_id
    assert window.players.list.exists(str(player_id))
    assert f"Created player {player_id} (Sam Taylor)" in window.status.get()
    assert window.unapplied


def test_new_player_wizard_from_home_goes_to_the_pools(window: MainWindow) -> None:
    wizard = window.home.new_player()
    name(wizard, "Ana", "Pool")
    player = window.repository.get_player(wizard.create())
    assert (player["club"], player["national_team"]) == ("194", str(POOL))


def test_new_player_wizard_with_photo_and_look(
    full_window: MainWindow, tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    window = full_window
    image = tmp_path / "friend.png"
    Image.new("RGB", (300, 400), "red").save(image)
    monkeypatch.setattr(filedialog, "askopenfilename", lambda **kwargs: str(image))
    wizard = window.players.new_player()
    name(wizard, "Sam", "Keeper")
    wizard.form.set("main_pos", "Goalkeeper")
    wizard.form.set(OVERALL, "88")
    assert enabled(wizard.remove_photo_button) is False
    wizard.choose_photo()
    assert wizard.png is not None
    assert enabled(wizard.remove_photo_button)
    wizard.look_form.set("skin_color", "African")
    player_id = wizard.create()
    assert window.workspace.portraits.custom_ids() == [player_id]
    look = AppearanceRepository(window.connection, window.appearance_options).get(
        player_id
    )
    assert look["skin_color"] == "African01"
    assert window.repository.get_player(player_id)["total_avg"] == "88.00"


def test_new_player_wizard_refuses_bad_values(
    look_window: MainWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    errors = []
    monkeypatch.setattr(new_player_dialog, "show_error", lambda _p, e: errors.append(e))
    window = look_window
    count = window.connection.execute("SELECT COUNT(*) FROM playerDBPlayer").fetchone()
    wizard = window.players.new_player()
    assert wizard.create() is None  # no name
    assert errors[-1].field == "name"
    name(wizard, "Sam", "Taylor")
    wizard.form.set("sk_speed", "150")
    assert wizard.create() is None
    assert errors[-1].field == "sk_speed"
    wizard.form.set("sk_speed", "80")
    wizard.look_form.set("muscularity", "500")
    wizard.tabs.select(wizard.player_tab)
    assert wizard.create() is None
    assert errors[-1].field == "muscularity"
    assert wizard.tabs.select() == str(wizard.look_tab)
    wizard.destroy()  # Cancel
    assert (
        window.connection.execute("SELECT COUNT(*) FROM playerDBPlayer").fetchone()
        == count
    )
    assert not window.unapplied


def test_new_player_wizard_jersey_follows_the_club(window: MainWindow) -> None:
    wizard = window.home.new_player()
    wizard.form.set("club", "FC Barcelona Intersport")
    suggested = window.repository.new_player_values(CLUB)["jersey_nr"]
    assert wizard.form.values()["jersey_nr"] == suggested
    wizard.form.set("jersey_nr", "77")
    wizard.form.set("club", "Fraikin BM. Granollers")
    assert wizard.form.values()["jersey_nr"] == "77"  # typed: kept
    wizard.destroy()


def test_duplicate_copies_the_portrait(portrait_window: MainWindow) -> None:
    portraits = portrait_window.workspace.portraits
    open_player(portrait_window, PLAYER_WITH_IMAGE)
    portrait_window.players.panel.duplicate()
    player_id = portrait_window.players.panel.player_id
    assert player_id != PLAYER_WITH_IMAGE
    assert portraits.custom_ids() == [player_id]
    assert portrait_window.players.list.exists(str(player_id))  # same club


def test_delete_player(portrait_window: MainWindow, yes: None) -> None:
    window = portrait_window
    panel = window.players.panel
    open_player(window, PLAYER_WITH_IMAGE)
    panel.duplicate()
    player_id = panel.player_id
    panel.delete()
    assert panel.player_id is None
    assert not window.players.list.exists(str(player_id))
    assert window.workspace.portraits.custom_ids() == []


def test_find_players(window: MainWindow) -> None:
    window.home.search.set("morel")
    window.home.find()
    assert window.notebook.select() == str(window.players)
    assert set(window.players.list.get_children()) == {
        str(PLAYER_WITHOUT_IMAGE),
        str(PLAYER_WITH_IMAGE),
    }
    assert "2 players found" in window.status.get()
    window.show_player(PLAYER_WITHOUT_IMAGE)
    window.players.panel.form.set("name", "Morelos")
    assert window.players.save()
    assert window.players.list.exists(str(PLAYER_WITHOUT_IMAGE))  # still found
    window.players.clear_search()
    assert window.players.list.get_children() == ()


def test_appearance_dialog(look_window: MainWindow) -> None:
    window = look_window
    open_player(window, PLAYER_WITHOUT_IMAGE)
    assert enabled(window.players.panel.appearance_button)
    dialog = window.players.panel.edit_appearance()
    try:
        assert dialog.has_look
        assert dialog.form._variables["skin_color"].get() == "Eurosouth"
        dialog.form.set("skin_color", "African")
        dialog.form.set("muscularity", "90")
        assert dialog.save()
        look = dialog.looks.get(PLAYER_WITHOUT_IMAGE)
        assert (look["skin_color"], look["muscularity"]) == ("African01", "90")
        assert window.unapplied
        dialog.default_look()
        assert dialog.looks.get(PLAYER_WITHOUT_IMAGE) is None
        assert dialog.save()  # saving with no look creates one
        assert dialog.looks.get(PLAYER_WITHOUT_IMAGE)["skin_color"] == "Euro01"
    finally:
        dialog.destroy()


# Teams


def test_unused_teams_only_shown_with_toggle(window: MainWindow) -> None:
    tree = window.teams.tree
    assert tree.exists(str(CLUB))
    assert tree.exists(str(POOL))
    assert not tree.exists(str(EMPTY_ORIGINAL_TEAM))
    window.teams.show_unused.set(True)
    window.teams.refresh_tree()
    assert tree.exists(str(EMPTY_ORIGINAL_TEAM))
    assert "[pool]" in tree.item(str(POOL), "text")


def test_edit_team(window: MainWindow, yes: None) -> None:
    open_player(window, PLAYER_WITHOUT_IMAGE)
    window.show_team(CLUB)
    panel = window.teams.panel
    assert panel.form._variables["coach"].get() == "Xavier Pascual Fuertes"
    panel.form.set("name", "Barça Test")
    panel.form.set("rating", "2.5")
    assert window.teams.save()
    assert window.teams.tree.item(str(CLUB), "text").startswith("Barça Test")
    # The player form shows the new club name once Players is shown again.
    window.show_tab(window.players)
    assert window.players.panel.form._variables["club"].get() == "Barça Test"
    assert window.players.panel.form.changes() == {}
    window.show_tab(window.teams)
    panel.revert()
    assert panel.form._variables["name"].get() == "FC Barcelona Intersport"


def test_edit_team_shows_errors(
    window: MainWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    errors = []
    monkeypatch.setattr(team_panel, "show_error", lambda _p, e: errors.append(e))
    window.show_team(CLUB)
    window.teams.panel.form.set("abbrev", "TOOLONG")
    assert not window.teams.save()
    assert errors and errors[0].field == "abbrev"


def test_team_buttons_follow_the_team(window: MainWindow) -> None:
    teams = window.teams
    window.show_team(POOL)
    assert teams.panel.form is None  # pools are not edited
    assert "Pools" in teams.panel.title.get()
    assert not enabled(teams.new_team_button)
    window.show_team(NATIONAL_TEAM)
    assert not enabled(teams.new_team_button)  # only clubs are copied
    window.show_team(CLUB)
    assert enabled(teams.new_team_button)
    assert not enabled(teams.delete_team_button)  # an original team
    assert "league_id" not in teams.panel.form._variables  # stays in its league


def test_roster_double_click_opens_the_player(window: MainWindow) -> None:
    window.show_team(CLUB)
    window.teams.roster.selection_set(str(PLAYER_WITH_IMAGE))
    window.teams._open_player()
    assert window.notebook.select() == str(window.players)
    assert window.players.panel.player_id == PLAYER_WITH_IMAGE


def test_new_team_in_a_new_league_with_logo_and_delete(
    portrait_window: MainWindow, tmp_path, monkeypatch: pytest.MonkeyPatch, yes: None
) -> None:
    image = tmp_path / "logo.png"
    Image.new("RGB", (200, 100), "green").save(image)
    monkeypatch.setattr(filedialog, "askopenfilename", lambda **kwargs: str(image))
    window = portrait_window
    portraits = window.workspace.portraits
    window.show_team(CLUB)
    new_team = window.teams.new_team()
    assert new_team.league.get() == NEW_LEAGUE  # no editor league yet
    new_team.name.set("Friends FC")
    new_team.league_name.set("Friends League")
    team_id = new_team.create()
    teams = window.teams
    assert teams.team_id == team_id
    assert teams.tree.exists(str(team_id))  # shown although empty
    assert teams.tree.item(GROUP_PREFIX + "Friends League", "text") == (
        "Friends League [editor]"
    )
    assert "Friends League has 1 teams" in window.status.get()
    assert teams.panel.form._variables["league_id"].get() == "Friends League"
    assert enabled(teams.delete_team_button)
    teams.panel.form.set("coach", "Test")
    assert teams.save()
    teams.panel.change_logo()
    assert portraits.custom_logo_ids() == [team_id]
    assert "custom_logo" in window.team_repository().logo_file(team_id)
    teams.delete_team()
    assert not teams.tree.exists(str(team_id))
    assert portraits.custom_logo_ids() == []
    # The league stays, empty, until it is deleted in the Leagues tab.
    league_id = window.editor_leagues()[0].id
    window.show_league(league_id)
    assert enabled(window.leagues.delete_button)
    window.leagues.delete_league()
    assert window.editor_leagues() == []


def test_new_team_in_an_existing_editor_league(window: MainWindow) -> None:
    league_id = LeagueRepository(window.connection).create_league("Second League", 1)
    window.show_team(CLUB)
    new_team = window.teams.new_team()
    assert new_team.league.get() == "Second League"
    team_id = new_team.create()
    assert TeamRepository(window.connection).league_of(team_id) == league_id


def test_new_team_with_players(portrait_window: MainWindow) -> None:
    window = portrait_window
    portraits = window.workspace.portraits
    window.show_team(CLUB)
    new_team = window.teams.new_team()
    assert new_team.players_text.get() == "Copy the club's players too (2)"
    assert not new_team.with_players.get()  # empty teams stay the default
    new_team.with_players.set(True)
    new_team.league_name.set("Friends League")
    team_id = new_team.create()
    copies = window.repository.team_players(team_id)
    assert sorted(player.name for player in copies) == sorted(
        player.name for player in window.repository.team_players(CLUB)
    )
    assert "with 2 copied players" in window.status.get()
    assert window.teams.roster.get_children()  # the roster shows the copies
    # Portraits are copied like Duplicate does; copies follow the source ids.
    sources = sorted(player.id for player in window.repository.team_players(CLUB))
    for source, copy in zip(sources, sorted(p.id for p in copies), strict=True):
        assert portraits.get(copy) == portraits.get(source)


def test_new_team_abbreviation_follows_the_name(window: MainWindow) -> None:
    window.show_team(CLUB)
    new_team = window.teams.new_team()
    assert new_team.abbrev.get() == "NEW"
    new_team.name.set("Friends FC")
    assert new_team.abbrev.get() == "FRI"
    new_team.abbrev.set("AFC")  # edited by hand: kept
    new_team.name.set("Friends Club")
    assert new_team.abbrev.get() == "AFC"
    new_team.league_name.set("Friends League")
    team_id = new_team.create()
    assert window.team_repository().get_team(team_id)["abbrev"] == "AFC"


def test_new_team_bad_abbreviation_makes_no_league(
    window: MainWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    errors = []
    monkeypatch.setattr(new_team_dialog, "show_error", lambda _p, e: errors.append(e))
    window.show_team(CLUB)
    new_team = window.teams.new_team()
    new_team.abbrev.set("TOO LONG")
    new_team.league_name.set("Friends League")
    assert new_team.create() is None
    assert errors[-1].field == "abbrev"
    assert window.editor_leagues() == []
    new_team.destroy()


def test_new_team_needs_a_league_name(
    window: MainWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    errors = []
    monkeypatch.setattr(new_team_dialog, "show_error", lambda _p, e: errors.append(e))
    window.show_team(CLUB)
    new_team = window.teams.new_team()
    new_team.league_name.set("")
    assert new_team.create() is None
    assert errors
    new_team.destroy()


def test_deleting_what_a_saved_league_uses_warns(
    window: MainWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    questions = []
    monkeypatch.setattr(
        messagebox,
        "askyesno",
        lambda _title, text, **_kwargs: questions.append(text) or False,
    )
    LeagueRepository(window.connection).create_league("Second League", 1)
    window.show_team(CLUB)
    team_id = window.teams.new_team().create()
    save = window.workspace.saves_dir / "SGT_LEAGUE.2.sg"
    save.write_text(
        f'<SaveGame name="Friends" playerTeamID="{team_id}" leagueID="4" />',
        encoding="utf-8",
    )
    window.teams.delete_team()
    assert "Friends (SGT_LEAGUE.2.sg)" in questions[-1]
    assert window.teams.tree.exists(str(team_id))  # declined: nothing deleted
    window.restore()
    assert "Friends (SGT_LEAGUE.2.sg)" in questions[-1]


# Leagues


def test_leagues_tab(window: MainWindow) -> None:
    leagues = window.leagues
    window.show_league(4)  # Liga ASOBAL, an original league
    assert not enabled(leagues.save_button)
    assert "read-only" in leagues.note.get()
    assert leagues.teams.exists(str(CLUB))
    league_id = LeagueRepository(window.connection).create_league("Second League", 1)
    window.show_league(league_id)
    assert leagues.list.item(str(league_id), "text") == "Second League [editor]"
    assert enabled(leagues.save_button)
    assert "Second League has 0 teams" in leagues.note.get()
    leagues.form.set("name", "Third League")
    assert leagues.save()
    assert leagues.list.item(str(league_id), "text") == "Third League [editor]"


def test_new_league_from_home(window: MainWindow) -> None:
    window.home.new_league()
    dialog = window.root.winfo_children()[-1]
    assert isinstance(dialog, new_league_dialog.NewLeagueDialog)
    dialog.form.set("name", "Fourth League")
    dialog.form.set("country_id", "Spain")
    league_id = dialog.create()
    assert window.leagues.league_id == league_id
    assert window.notebook.select() == str(window.leagues)


def test_add_new_team_from_the_leagues_tab(
    window: MainWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    errors = []
    monkeypatch.setattr(new_team_dialog, "show_error", lambda _p, e: errors.append(e))
    leagues = window.leagues
    window.show_league(4)
    assert not enabled(leagues.add_team_button)  # the game's leagues take no new teams
    league_id = LeagueRepository(window.connection).create_league("Second League", 1)
    window.show_league(league_id)
    assert enabled(leagues.add_team_button)
    dialog = leagues.add_team()
    assert dialog.league.get() == "Second League"
    assert "Fraikin BM. Granollers [unused]" in dialog._clubs
    assert all(team_id not in (194, POOL) for team_id in dialog._clubs.values())
    assert dialog.create() is None  # no club chosen yet
    assert "Choose the club" in str(errors[-1])
    assert dialog.players_check.instate(["disabled"])
    dialog.template.set("FC Barcelona Intersport")
    assert dialog.players_text.get() == "Copy the club's players too (2)"
    assert dialog.players_check.instate(["!disabled"])
    dialog.name.set("Friends FC")
    team_id = dialog.create()
    assert leagues.teams.exists(str(team_id))
    assert TeamRepository(window.connection).league_of(team_id) == league_id
    assert "Second League has 1 teams" in window.status.get()


# Wizards: new team and new league


def logo_image(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    image = tmp_path / "logo.png"
    Image.new("RGB", (200, 200), "green").save(image)
    monkeypatch.setattr(filedialog, "askopenfilename", lambda **kwargs: str(image))


def test_team_wizard_with_details_and_logo(
    portrait_window: MainWindow, tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    logo_image(tmp_path, monkeypatch)
    window = portrait_window
    window.show_team(CLUB)
    wizard = window.teams.new_team()
    assert wizard.details.values()["coach"] == "Xavier Pascual Fuertes"
    wizard.name.set("Friends FC")
    wizard.league_name.set("Friends League")
    wizard.details.set("coach", "Joe")
    wizard.details.set("home_colour", "00ff00")
    wizard.choose_logo()
    assert wizard.logo is not None
    assert enabled(wizard.remove_logo_button)
    team_id = wizard.create()
    team = window.team_repository().get_team(team_id)
    assert (team["name"], team["abbrev"], team["coach"], team["home_colour"]) == (
        "Friends FC",
        "FRI",
        "Joe",
        "00FF00",
    )
    assert window.workspace.portraits.custom_logo_ids() == [team_id]
    assert "custom_logo" in window.team_repository().logo_file(team_id)
    assert window.teams.team_id == team_id


def test_team_wizard_from_home_keeps_sheet_edits(window: MainWindow) -> None:
    LeagueRepository(window.connection).create_league("Second League", 1)
    wizard = window.home.new_team()
    assert window.notebook.select() == str(window.teams)
    assert wizard.league.get() == "Second League"
    assert wizard.details.values()["coach"] == ""  # no club chosen yet
    wizard.template.set("FC Barcelona Intersport")
    assert wizard.details.values()["city"] == "Barcelona"
    wizard.details.set("coach", "Joe")
    wizard.template.set("Fraikin BM. Granollers [unused]")
    values = wizard.details.values()
    assert values["coach"] == "Joe"  # edited: kept
    assert values["city"] != "Barcelona"  # untouched: follows the club
    team_id = wizard.create()
    assert window.team_repository().get_team(team_id)["coach"] == "Joe"


def test_team_wizard_bad_detail_creates_nothing(
    window: MainWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    errors = []
    monkeypatch.setattr(new_team_dialog, "show_error", lambda _p, e: errors.append(e))
    window.show_team(CLUB)
    wizard = window.teams.new_team()
    wizard.league_name.set("Friends League")
    wizard.details.set("coach", "")
    assert wizard.create() is None
    assert errors[-1].field == "coach"
    assert wizard.tabs.select() == str(wizard.details_tab)
    wizard.details.set("coach", "Joe")
    wizard.league_name.set("")
    assert wizard.create() is None
    assert wizard.tabs.select() == str(wizard.team_tab)
    assert window.editor_leagues() == []  # no league either
    wizard.destroy()


def test_league_wizard_with_teams(portrait_window: MainWindow) -> None:
    window = portrait_window
    wizard = window.leagues.new_league()
    assert len(wizard.rows) == 4
    assert wizard.count.get().startswith("0 teams")
    wizard.form.set("name", "Friends League")
    wizard.form.set("country_id", "Spain")
    for row, (club, name) in zip(
        wizard.rows,
        [
            ("FC Barcelona Intersport", "Friends FC"),
            ("Fraikin BM. Granollers [unused]", "Mates"),
            ("FC Barcelona Intersport", "Neighbours"),
            ("Fraikin BM. Granollers [unused]", "Cousins"),
        ],
        strict=True,
    ):
        row.club.set(club)
        row.name.set(name)
    wizard.rows[0].with_players.set(True)
    assert wizard.rows[1].abbrev.get() == "MAT"
    assert wizard.count.get() == "4 teams: ready for league mode."
    extra = wizard.add_row()  # left empty: skipped
    assert wizard.count.get() == "4 teams: ready for league mode."
    extra.name.set("Sobra")
    assert wizard.count.get().startswith("5 teams: league mode needs")
    wizard.remove_row(extra)
    league_id = wizard.create()
    league = window.league_repository().get_league(league_id)
    assert (league.name, league.team_count) == ("Friends League", 4)
    assert window.leagues.league_id == league_id
    assert "with 4 teams" in window.status.get()
    names = [
        window.leagues.teams.item(item, "text")
        for item in window.leagues.teams.get_children()
    ]
    assert sorted(names) == ["Cousins", "Friends FC", "Mates", "Neighbours"]
    portraits = window.workspace.portraits
    assert len(portraits.custom_ids()) == 2  # copies of both Barcelona players


def test_league_wizard_refusals_create_nothing(
    window: MainWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    errors = []
    monkeypatch.setattr(new_league_dialog, "show_error", lambda _p, e: errors.append(e))
    before = window.connection.execute("SELECT COUNT(*) FROM playerDBTeam").fetchone()
    wizard = window.leagues.new_league()
    wizard.form.set("name", "Friends League")
    wizard.form.set("country_id", "Spain")
    wizard.rows[0].club.set("FC Barcelona Intersport")
    wizard.rows[0].name.set("Friends")
    wizard.rows[1].club.set("FC Barcelona Intersport")
    wizard.rows[1].name.set("friends")
    assert wizard.create() is None
    assert (errors[-1].field, errors[-1].team) == ("name", 1)
    wizard.rows[1].name.set("Mates")
    wizard.rows[2].name.set("No club")
    assert wizard.create() is None
    assert (errors[-1].field, errors[-1].team) == ("template", 2)
    assert window.editor_leagues() == []
    assert (
        window.connection.execute("SELECT COUNT(*) FROM playerDBTeam").fetchone()
        == before
    )
    wizard.destroy()


# Colour swatches


@pytest.fixture
def swatch_window(workspace: Workspace, swatch_options) -> Iterator[MainWindow]:
    yield from make_window(workspace, swatch_options)


def test_appearance_swatches(swatch_window: MainWindow) -> None:
    window = swatch_window
    open_player(window, PLAYER_WITHOUT_IMAGE)
    dialog = window.players.panel.edit_appearance()
    try:
        skin = dialog.form._widgets["skin_color"]
        assert isinstance(skin, SwatchPicker)
        assert list(skin.swatches) == ["Eurosouth", "African"]
        assert isinstance(dialog.form._widgets["hairstyle"], ttk.Combobox)
        assert skin.selected() == "Eurosouth"  # the loaded look is framed
        assert skin.name.cget("text") == "Eurosouth"
        skin.choose("African")
        assert skin.selected() == "African"
        assert dialog.form.changes() == {"skin_color": "African01"}
        assert dialog.save()
        assert dialog.looks.get(PLAYER_WITHOUT_IMAGE)["skin_color"] == "African01"
        # The keyboard: arrows move along the row, without going past its ends.
        assert skin._row.bind("<Left>") and skin._row.bind("<End>")
        assert str(skin._row.cget("takefocus")) == "1"
        skin.step(-1)
        assert skin.selected() == "Eurosouth"
        skin.step(-5)
        assert skin.selected() == "Eurosouth"
        skin.step(1)
        assert skin.selected() == "African"
        dialog.form.set_enabled(False)
        skin.choose("Eurosouth")  # disabled: ignored
        skin.step(-1)
        assert skin.selected() == "African"
        assert str(skin._row.cget("takefocus")) == "0"
    finally:
        dialog.destroy()


def test_new_player_wizard_look_swatches(swatch_window: MainWindow) -> None:
    window = swatch_window
    wizard = window.players.new_player()
    name(wizard, "Sam", "Taylor")
    hair = wizard.look_form._widgets["hairstyle_color"]
    assert isinstance(hair, SwatchPicker)
    assert hair.selected() == "Brown"  # the default look
    hair.choose("Green")
    player_id = wizard.create()
    look = AppearanceRepository(window.connection, window.appearance_options).get(
        player_id
    )
    assert look["hairstyle_color"] == "green_001.dds"


# Sharing a league


def test_export_and_import_a_league(
    full_window: MainWindow, tmp_path, monkeypatch: pytest.MonkeyPatch, yes: None
) -> None:
    window = full_window
    leagues = window.leagues
    league_id, _created = window.team_repository().create_teams(
        [NewTeam(CLUB, "Friends FC", with_players=True), NewTeam(OTHER_CLUB, "Mates")],
        window.original_db(),
        new_league=("Friends League", 1),
    )
    window.show_league(4)
    assert not enabled(leagues.export_button)  # the game's leagues stay
    window.show_league(league_id)
    assert enabled(leagues.export_button)
    path = tmp_path / "liga.hc14league"
    monkeypatch.setattr(filedialog, "asksaveasfilename", lambda **kwargs: str(path))
    summary = leagues.export_league()
    assert (summary.teams, summary.players) == (2, 2)
    assert "includes the players' photos" in window.status.get()
    questions = []
    monkeypatch.setattr(filedialog, "askopenfilename", lambda **kwargs: str(path))
    monkeypatch.setattr(
        messagebox, "askyesno", lambda _t, text, **_k: questions.append(text) or True
    )
    imported = window.home.import_league()
    assert "Friends League (2)" in questions[-1]
    assert window.notebook.select() == str(window.leagues)
    assert leagues.league_id == imported
    assert window.league_repository().get_league(imported).team_count == 2
    assert "Imported Friends League (2) (2 teams, 2 players)" in window.status.get()
    assert window.unapplied


def test_importing_a_bad_file_changes_nothing(
    window: MainWindow, tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    errors = []
    path = tmp_path / "bad.hc14league"
    path.write_bytes(b"not a league")
    monkeypatch.setattr(filedialog, "askopenfilename", lambda **kwargs: str(path))
    monkeypatch.setattr(user_facing_error, "show_error", lambda _p, e: errors.append(e))
    before = len(window.league_repository().playable_leagues())
    window.guard(window.leagues.import_league)()
    assert errors
    assert len(window.league_repository().playable_leagues()) == before


def test_version_and_author(
    window: MainWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert window.root.title() == f"IHF Handball Challenge 14 Editor {__version__}"
    shown = []
    monkeypatch.setattr(
        messagebox, "showinfo", lambda title, text, **_kwargs: shown.append(text)
    )
    window.about()
    assert f"Version {__version__}, made by imncls" in shown[0]
    assert "unofficial" in shown[0]
    labels = [
        child.cget("text")
        for child in window.home.winfo_children()
        if isinstance(child, ttk.Label)
    ]
    assert not any("made by" in text for text in labels)  # only in Help > About


def test_window_icon(tmp_path) -> None:
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("Tk cannot create a window here")
    root.withdraw()
    try:
        assert icon_path().is_file()  # icons/icon.png, shipped with the program
        assert set_window_icon(root)
        assert len(root.window_icon_images) == len(WINDOW_ICON_SIZES)
        # Without the picture the editor still starts, with the default icon.
        assert not set_window_icon(root, tmp_path / "missing.png")
        broken = tmp_path / "broken.png"
        broken.write_bytes(b"not a picture")
        assert not set_window_icon(root, broken)
    finally:
        root.destroy()


# Settings


@pytest.fixture
def game_folders(tmp_path, workspace: Workspace) -> Paths:
    game_dir = tmp_path / "game folder"
    game_dir.mkdir()
    (game_dir / DLL_NAME).write_bytes(b"")
    return Paths(game_dir=game_dir.resolve(), save_dir=workspace.dump_path.parent)


@pytest.fixture
def settings_window(
    tmp_path, workspace: Workspace, game_folders: Paths
) -> Iterator[MainWindow]:
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("Tk cannot create a window here")
    root.withdraw()
    workspace.backup()
    workspace.start()
    config_path = tmp_path / "config.local.json"
    config_path.write_text('{"key_hex": "AA BB"}', encoding="utf-8")
    window = MainWindow(root, workspace, None, config_path, game_folders)
    yield window
    window._disconnect()
    try:
        root.destroy()
    except tk.TclError:
        pass  # already closed by a restart


def test_settings_tab_checks_folders(settings_window: MainWindow, tmp_path) -> None:
    form = settings_window.settings.form
    assert form.variables["game_dir"].get() == str(
        settings_window.settings._paths.game_dir
    )
    assert form.checks["game_dir"].cget("text").startswith("✓")
    form.variables["game_dir"].set(str(tmp_path / "nowhere"))
    assert "does not exist" in form.checks["game_dir"].cget("text")
    form.variables["game_dir"].set(str(tmp_path))  # a folder, but not the game's
    assert DLL_NAME in form.checks["game_dir"].cget("text")
    assert form.check() is None
    settings_window.settings.save()
    assert not settings_window.restart_requested


def test_settings_unchanged_does_nothing(settings_window: MainWindow) -> None:
    settings_window.settings.save()
    assert "No changes" in settings_window.status.get()
    assert not settings_window.restart_requested


def test_settings_change_asks_about_unapplied_changes(
    settings_window: MainWindow,
    tmp_path,
    game_folders: Paths,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window = settings_window
    other = tmp_path / "other game"
    other.mkdir()
    (other / DLL_NAME).write_bytes(b"")
    window.repository.set_value(PLAYER_WITHOUT_IMAGE, "name", "Newman")
    window.changed()
    answers = iter([None, False])  # Cancel, then No (discard)
    monkeypatch.setattr(messagebox, "askyesnocancel", lambda *a, **k: next(answers))
    window.settings.form.variables["game_dir"].set(str(other))
    window.settings.save()  # cancelled: nothing stored
    assert not window.restart_requested
    assert "game_dir" not in json.loads(window.config_path.read_text("utf-8"))
    window.settings.save()  # discard and switch
    assert window.restart_requested
    stored = json.loads(window.config_path.read_text("utf-8"))
    assert stored["game_dir"] == str(other.resolve())
    assert stored["save_dir"] == str(game_folders.save_dir)
    assert stored["key_hex"] == "AA BB"  # kept


def test_setup_window_stores_the_folders(
    tmp_path, game_folders: Paths, monkeypatch: pytest.MonkeyPatch
) -> None:
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("Tk cannot create a window here")
    root.withdraw()
    monkeypatch.setattr(
        settings_form, "detected_folders", lambda: (None, game_folders.save_dir)
    )
    config_path = tmp_path / "config.local.json"
    try:
        setup = SetupWindow(root, config_path, "The game was not found.")
        assert setup.form.variables["game_dir"].get() == ""  # not found
        assert setup.form.variables["save_dir"].get() == str(game_folders.save_dir)
        assert not setup.continue_()  # the game folder is missing
        assert not config_path.exists()
        setup.form.variables["game_dir"].set(str(game_folders.game_dir))
        assert setup.continue_()
        assert setup.saved
        stored = json.loads(config_path.read_text("utf-8"))
        assert stored["game_dir"] == str(game_folders.game_dir)
    finally:
        root.destroy()


def test_start_asks_for_the_folders_when_missing(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = []

    def fake_load(path):
        calls.append(path)
        if len(calls) == 1:
            raise FolderError("The game was not found.", "game_dir")
        return "config"

    monkeypatch.setattr(main_window, "load_config", fake_load)
    monkeypatch.setattr(main_window, "SetupWindow", FakeSetup)
    FakeSetup.answer = True
    assert main_window.load_or_set_up(None, tmp_path / "c.json") == "config"
    calls.clear()
    FakeSetup.answer = False  # Quit
    assert main_window.load_or_set_up(None, tmp_path / "c.json") is None


class FakeSetup:
    answer = True

    def __init__(self, _parent, _path, _reason) -> None:
        pass

    def run(self) -> bool:
        return self.answer
