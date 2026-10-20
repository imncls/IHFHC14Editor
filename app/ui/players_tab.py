"""Players tab: find players or list a team, and edit the one chosen."""

import tkinter as tk
from tkinter import ttk
from typing import TYPE_CHECKING

from app.repository import POOL_TEAM_IDS, SEARCH_LIMIT, name_warnings
from app.ui.new_player_dialog import NewPlayerDialog
from app.ui.player_panel import APPLY_HINT, PlayerPanel
from app.ui.widgets import fill_roster, roster_tree, selected_id

if TYPE_CHECKING:
    from app.ui.main_window import MainWindow

ANY_TEAM = "(choose a team)"


class PlayersTab(ttk.Frame):
    def __init__(self, parent: tk.Misc, app: "MainWindow") -> None:
        super().__init__(parent, padding=6)
        self.app = app
        self.search = tk.StringVar(self)
        self.search_text = ""
        self.team_id: int | None = None
        self.team_choice = tk.StringVar(self, ANY_TEAM)
        self._team_ids: dict[str, int] = {}
        self.count = tk.StringVar(self)

        panes = ttk.PanedWindow(self, orient="horizontal")
        panes.pack(fill="both", expand=True)
        left = ttk.Frame(panes)
        find = ttk.Frame(left)
        find.pack(fill="x", pady=(0, 4))
        ttk.Label(find, text="Find:").pack(side="left")
        self.search_entry = ttk.Entry(find, textvariable=self.search)
        self.search_entry.pack(side="left", fill="x", expand=True, padx=4)
        self.search_entry.bind("<Return>", app.guard(self.find))
        self.search_entry.bind("<Escape>", app.guard(self.clear_search))
        ttk.Button(find, text="Find", command=app.guard(self.find)).pack(side="left")
        ttk.Button(find, text="Clear", command=app.guard(self.clear_search)).pack(
            side="left", padx=(4, 0)
        )
        team = ttk.Frame(left)
        team.pack(fill="x", pady=(0, 4))
        ttk.Label(team, text="Team:").pack(side="left")
        self.team_box = ttk.Combobox(
            team, textvariable=self.team_choice, state="readonly"
        )
        self.team_box.pack(side="left", fill="x", expand=True, padx=4)
        self.team_box.bind("<<ComboboxSelected>>", app.guard(self._on_team_chosen))
        bottom = ttk.Frame(left)
        bottom.pack(side="bottom", fill="x", pady=(4, 0))
        ttk.Button(bottom, text="New player", command=app.guard(self.new_player)).pack(
            side="left"
        )
        ttk.Label(bottom, textvariable=self.count, foreground="gray").pack(side="right")
        self.list = roster_tree(left)
        self.list.bind("<<TreeviewSelect>>", app.guard(self._on_select))
        panes.add(left, weight=2)
        self.panel = PlayerPanel(panes, app, self._player_changed)
        panes.add(self.panel, weight=3)
        self._load_teams()

    # Lists

    def find(self, text: str | None = None) -> None:
        if text is not None:
            self.search.set(text)
        text = self.search.get().strip()
        if not text:
            self.clear_search()
            return
        self.search_text = text
        self.team_choice.set(ANY_TEAM)
        found = self.app.repository.search_players(text)
        fill_roster(self.list, found)
        limit = f" (the first {SEARCH_LIMIT})" if len(found) == SEARCH_LIMIT else ""
        self.count.set(f"{len(found)} found{limit}")
        self.app.set_status(f"{len(found)} players found for '{text}'{limit}.")

    def clear_search(self) -> None:
        self.search.set("")
        self.search_text = ""
        self.refresh_list()

    def show_team(self, team_id: int) -> None:
        """List a team's players (this ends a search)."""
        self.search.set("")
        self.search_text = ""
        self.team_id = team_id
        label = next((k for k, v in self._team_ids.items() if v == team_id), ANY_TEAM)
        self.team_choice.set(label)
        self.refresh_list()

    def refresh_list(self) -> None:
        if self.search_text:
            players = self.app.repository.search_players(self.search_text)
        elif self.team_id is not None:
            players = self.app.repository.team_players(self.team_id)
        else:
            players = []
        fill_roster(self.list, players)
        self.count.set(f"{len(players)} players" if players else "")
        if self.panel.player_id is not None and self.list.exists(
            str(self.panel.player_id)
        ):
            self.list.selection_set(str(self.panel.player_id))
            self.list.see(str(self.panel.player_id))

    def show_player(self, player_id: int) -> None:
        """Open a player, listing his team if he is not in the list."""
        if not self.list.exists(str(player_id)):
            player = self.app.repository.get_player(player_id)
            teams = [int(player["club"]), int(player["national_team"])]
            real = [team for team in teams if team not in POOL_TEAM_IDS]
            self.show_team(real[0] if real else teams[0])
        if self.panel.show(player_id) and self.list.exists(str(player_id)):
            self.list.selection_set(str(player_id))
            self.list.see(str(player_id))

    # Actions

    def new_player(self, team_id: int | None = None) -> NewPlayerDialog | None:
        """The wizard for a new player in this team (default: the team listed,
        else the pools)."""
        if not self.panel.resolve_unsaved():
            return None
        team_id = team_id if team_id is not None else self.team_id
        return NewPlayerDialog(self.app.root, self.app, team_id, self._player_created)

    def save(self) -> bool:
        return self.panel.save()

    def _player_created(self, player_id: int) -> None:
        repository = self.app.repository
        self.app.changed()
        self._load_teams()  # the player counts in the team list changed
        player = repository.get_player(player_id)
        club = int(player["club"])
        self.show_player(player_id)  # lists his team if needed
        warnings = name_warnings(player)
        if club not in POOL_TEAM_IDS:
            warnings += repository.team_warnings(club, player_id)
        name = f"{player['first_name']} {player['name']}".strip()
        message = f"Created player {player_id} ({name}). {APPLY_HINT}"
        self.app.set_status(" ".join([message, *warnings]))

    def focus_search(self) -> None:
        self.search_entry.focus_set()
        self.search_entry.select_range(0, "end")

    # Window hooks

    def refresh(self) -> None:
        self._load_teams()
        self.panel.refresh()
        self.refresh_list()

    def reset(self) -> None:
        self.search.set("")
        self.search_text = ""
        self.team_id = None
        self.team_choice.set(ANY_TEAM)
        self.panel.clear()
        self._load_teams()
        self.refresh_list()

    def resolve_unsaved(self) -> bool:
        return self.panel.resolve_unsaved()

    # Helpers

    def _load_teams(self) -> None:
        teams = sorted(
            self.app.repository.teams(), key=lambda team: team.name.casefold()
        )
        self._team_ids = {
            f"{team.name} ({team.player_count})": team.id for team in teams
        }
        self.team_box.configure(values=list(self._team_ids))
        if self.team_id is not None:
            label = next(
                (k for k, v in self._team_ids.items() if v == self.team_id), ANY_TEAM
            )
            self.team_choice.set(label)

    def _on_team_chosen(self) -> None:
        team_id = self._team_ids.get(self.team_choice.get())
        if team_id is not None:
            self.show_team(team_id)

    def _on_select(self) -> None:
        player_id = selected_id(self.list)
        if player_id is not None and player_id != self.panel.player_id:
            if not self.panel.show(player_id) and self.panel.player_id is not None:
                self.list.selection_set(str(self.panel.player_id))  # cancelled

    def _player_changed(self, _player_id: int | None) -> None:
        # The list follows the data; the player stays open even if he left it.
        self.refresh_list()
