"""Home tab: where to start, what to create, and the changes waiting."""

import tkinter as tk
from tkinter import ttk
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.ui.main_window import MainWindow
    from app.ui.new_league_dialog import NewLeagueDialog
    from app.ui.new_player_dialog import NewPlayerDialog
    from app.ui.new_team_dialog import NewTeamDialog

HEADING = ("", 11, "bold")


class HomeTab(ttk.Frame):
    def __init__(self, parent: tk.Misc, app: "MainWindow") -> None:
        super().__init__(parent, padding=24)
        self.app = app
        self.search = tk.StringVar(self)
        ttk.Label(
            self, text="IHF Handball Challenge 14 Editor", font=("", 16, "bold")
        ).pack(anchor="w")
        ttk.Label(
            self,
            text="Edit the game's players, teams and leagues. Changes are saved in "
            "a working copy; 'Apply to game' installs them (the game must be closed).",
            foreground="gray",
        ).pack(anchor="w", pady=(2, 18))

        find = self._section("Find a player")
        entry = ttk.Entry(find, textvariable=self.search, width=40)
        entry.pack(side="left")
        entry.bind("<Return>", app.guard(self.find))
        ttk.Button(find, text="Find", command=app.guard(self.find)).pack(
            side="left", padx=6
        )

        create = self._section("Create")
        for text, action in (
            ("New player", self.new_player),
            ("New team", self.new_team),
            ("New league", self.new_league),
            ("Import league...", self.import_league),
        ):
            ttk.Button(create, text=text, width=16, command=app.guard(action)).pack(
                side="left", padx=(0, 8)
            )

        edit = self._section("Edit")
        for text, tab in (
            ("Players", app.players),
            ("Teams", app.teams),
            ("Leagues", app.leagues),
        ):
            ttk.Button(
                edit, text=text, width=16, command=lambda tab=tab: app.show_tab(tab)
            ).pack(side="left", padx=(0, 8))

        game = self._section("Game")
        ttk.Label(game, textvariable=app.changes_text).pack(anchor="w")
        row = ttk.Frame(game)
        row.pack(anchor="w", pady=(6, 0))
        self.apply_button = ttk.Button(
            row, text="Apply to game", width=16, command=app.guard(app.apply)
        )
        self.apply_button.pack(side="left", padx=(0, 8))
        ttk.Button(
            row, text="Reload from game", width=16, command=app.guard(app.reload)
        ).pack(side="left", padx=(0, 8))
        ttk.Button(
            row, text="Restore original...", command=app.guard(app.restore)
        ).pack(side="left", padx=(32, 0))

    def find(self) -> None:
        self.app.find_player(self.search.get())

    def new_player(self) -> "NewPlayerDialog | None":
        """The new player wizard, for a player in the pools."""
        return self.app.new_player(None)

    def new_team(self) -> "NewTeamDialog | None":
        """The new team wizard; the team then opens in the Teams tab."""
        self.app.show_tab(self.app.teams)
        return self.app.teams.new_team_from_any_club()

    def new_league(self) -> "NewLeagueDialog":
        self.app.show_tab(self.app.leagues)
        return self.app.leagues.new_league()

    def import_league(self) -> int | None:
        self.app.show_tab(self.app.leagues)
        return self.app.leagues.import_league()

    def _section(self, title: str) -> ttk.Frame:
        ttk.Label(self, text=title, font=HEADING).pack(anchor="w", pady=(0, 4))
        frame = ttk.Frame(self)
        frame.pack(anchor="w", fill="x", pady=(0, 18))
        return frame

    # Window hooks

    def refresh(self) -> None:
        pass

    def reset(self) -> None:
        self.search.set("")
