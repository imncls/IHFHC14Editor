"""Leagues tab: the playable leagues; those made with the editor are editable."""

import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import TYPE_CHECKING

from app import league_package
from app.errors import ValidationError
from app.saves import saves_using
from app.ui.new_league_dialog import LeagueForm, NewLeagueDialog
from app.ui.new_team_dialog import NewTeamDialog
from app.ui.user_facing_error import show_error
from app.ui.widgets import scrolled_tree, selected_id, set_enabled

if TYPE_CHECKING:
    from app.ui.main_window import MainWindow

APPLY_HINT = "Use 'Apply to game' to see it in the game."
ORIGINAL_NOTE = (
    "A league of the game: read-only. Its fixtures are fixed, so it only takes "
    "its original teams."
)
EDITOR_NOTE = "Made with the editor: the game builds its fixtures from the teams in it."
PACKAGE_TYPES = [("League package", f"*{league_package.EXTENSION}")]


class LeaguesTab(ttk.Frame):
    def __init__(self, parent: tk.Misc, app: "MainWindow") -> None:
        super().__init__(parent, padding=6)
        self.app = app
        self.league_id: int | None = None
        self.title = tk.StringVar(self, "Choose a league")
        self.note = tk.StringVar(self)
        self._editor_ids: set[int] = set()

        panes = ttk.PanedWindow(self, orient="horizontal")
        panes.pack(fill="both", expand=True)
        left = ttk.Frame(panes)
        share = ttk.Frame(left)
        share.pack(side="bottom", fill="x", pady=(4, 0))
        ttk.Button(
            share, text="Import league...", command=app.guard(self.import_league)
        ).pack(side="left")
        self.export_button = ttk.Button(
            share, text="Export league...", command=app.guard(self.export_league)
        )
        self.export_button.pack(side="left", padx=(6, 0))
        buttons = ttk.Frame(left)
        buttons.pack(side="bottom", fill="x", pady=(4, 0))
        ttk.Button(buttons, text="New league", command=app.guard(self.new_league)).pack(
            side="left"
        )
        self.delete_button = ttk.Button(
            buttons, text="Delete league", command=app.guard(self.delete_league)
        )
        self.delete_button.pack(side="left", padx=(6, 0))
        self.list = scrolled_tree(left, ("teams",), show="tree headings")
        self.list.heading("#0", text="League")
        self.list.heading("teams", text="Teams")
        self.list.column("teams", width=60, stretch=False)
        self.list.bind("<<TreeviewSelect>>", app.guard(self._on_select))
        panes.add(left, weight=1)

        right = ttk.Frame(panes, padding=(8, 0))
        ttk.Label(right, textvariable=self.title, font=("", 12, "bold")).pack(
            anchor="w", pady=(0, 6)
        )
        self.form = LeagueForm(right, app.league_repository().countries())
        self.form.pack(anchor="w")
        save_row = ttk.Frame(right)
        save_row.pack(fill="x", pady=6)
        self.save_button = ttk.Button(
            save_row, text="Save", command=app.guard(self.save)
        )
        self.save_button.pack(side="left")
        ttk.Label(
            right, textvariable=self.note, foreground="gray", wraplength=520
        ).pack(anchor="w", pady=(0, 8))
        teams_bar = ttk.Frame(right)
        teams_bar.pack(fill="x")
        ttk.Label(teams_bar, text="Teams", font=("", 10, "bold")).pack(side="left")
        ttk.Label(teams_bar, text="(double-click to edit)", foreground="gray").pack(
            side="left", padx=6
        )
        self.add_team_button = ttk.Button(
            teams_bar, text="Add new team...", command=app.guard(self.add_team)
        )
        self.add_team_button.pack(side="right", pady=(0, 4))
        self.teams = scrolled_tree(right, ("players",), show="tree headings")
        self.teams.heading("#0", text="Team")
        self.teams.heading("players", text="Players")
        self.teams.column("players", width=70, stretch=False)
        self.teams.bind("<Double-1>", app.guard(self._open_team))
        self.teams.bind("<Return>", app.guard(self._open_team))
        panes.add(right, weight=2)
        self.refresh()

    # Showing

    def refresh(self) -> None:
        self._editor_ids = {league.id for league in self.app.editor_leagues()}
        self.list.delete(*self.list.get_children())
        for league in self.app.league_repository().playable_leagues():
            marker = " [editor]" if league.id in self._editor_ids else ""
            self.list.insert(
                "",
                "end",
                str(league.id),
                text=league.name + marker,
                values=(league.team_count,),
            )
        if self.league_id is not None and self.list.exists(str(self.league_id)):
            self.list.selection_set(str(self.league_id))
            if not self.form.is_dirty():
                self._load(self.league_id)
        else:
            self._clear()

    def show_league(self, league_id: int) -> None:
        self.refresh()
        if self.list.exists(str(league_id)):
            self.list.selection_set(str(league_id))
            self.list.see(str(league_id))
        self.select_league(league_id)

    def select_league(self, league_id: int) -> None:
        if league_id != self.league_id and not self.resolve_unsaved():
            return
        self._load(league_id)

    def reset(self) -> None:
        self.league_id = None
        self.refresh()

    def resolve_unsaved(self) -> bool:
        if not self.form.is_dirty():
            return True
        answer = messagebox.askyesnocancel(
            "Unsaved changes",
            f"Save the changes to {self.title.get()}?",
            parent=self.app.root,
        )
        if answer is None:
            return False
        return self.save() if answer else True

    # Actions

    def save(self) -> bool:
        changes = self.form.changes()
        if self.league_id is None or not changes:
            self.app.set_status("No changes to save.")
            return True
        try:
            written = self.app.league_repository().set_league_values(
                self.league_id, changes, self.app.original_db()
            )
        except ValidationError as error:
            show_error(self.app.root, error)
            self.form.focus(error.field)
            return False
        if not written:  # only spaces or case-free differences: nothing changed
            self._load(self.league_id)
            self.app.set_status("No changes to save.")
            return True
        self.app.changed()
        self.refresh()
        self.app.set_status(f"Saved. {APPLY_HINT}")
        return True

    def new_league(self) -> NewLeagueDialog:
        return NewLeagueDialog(self.app.root, self.app, self._league_created)

    def delete_league(self) -> None:
        league_id = self.league_id
        if league_id is None or league_id not in self._editor_ids:
            return
        leagues = self.app.league_repository()
        name = leagues.get_league(league_id).name
        using = saves_using(self.app.workspace.saves_dir, league_ids={league_id})
        if not messagebox.askyesno(
            "Delete league",
            f"Delete the league {name}?" + self.app.saves_warning(using),
            icon="warning" if using else "question",
            parent=self.app.root,
        ):
            return
        leagues.delete_league(league_id, self.app.original_db())
        self.league_id = None
        self.app.changed()
        self.refresh()
        self.app.set_status(f"{name} deleted. {APPLY_HINT}")

    def export_league(self) -> league_package.Summary | None:
        """Save the league shown, its teams and players, to a file to share."""
        league_id = self.league_id
        if league_id is None or league_id not in self._editor_ids:
            return None
        name = self.app.league_repository().get_league(league_id).name
        path = filedialog.asksaveasfilename(
            parent=self.app.root,
            title="Export league",
            defaultextension=league_package.EXTENSION,
            filetypes=PACKAGE_TYPES,
            initialfile=f"{name}{league_package.EXTENSION}",
        )
        if not path:
            return None
        summary = league_package.export_league(
            self.app.connection,
            league_id,
            self.app.original_db(),
            self.app.workspace.portraits,
            Path(path),
        )
        self.app.set_status(
            f"Exported {name} ({summary.teams} teams, {summary.players} players) to "
            f"{Path(path).name}. It includes the players' photos: share it only "
            "with people they would agree to."
        )
        return summary

    def import_league(self) -> int | None:
        """Add a league from a file as a new league made with the editor."""
        if not self.resolve_unsaved():
            return None
        path = filedialog.askopenfilename(
            parent=self.app.root, title="Import league", filetypes=PACKAGE_TYPES
        )
        if not path:
            return None
        package = league_package.read_package(Path(path))
        summary = package.summary
        name = league_package.import_name(self.app.connection, package)
        if not messagebox.askyesno(
            "Import league",
            f"Add {summary.league} as the new league {name}, with {summary.teams} "
            f"teams and {summary.players} players?\n\nIt is added as a league "
            "made with the editor; nothing else changes.",
            parent=self.app.root,
        ):
            return None
        league_id = league_package.import_league(
            self.app.connection,
            package,
            self.app.workspace.portraits,
            self.app.appearance_options,
            Path(path).name,
        )
        self.app.changed()
        self.show_league(league_id)
        warnings = self.app.league_repository().league_warnings(league_id)
        message = (
            f"Imported {name} ({summary.teams} teams, {summary.players} players). "
            f"{APPLY_HINT}"
        )
        self.app.set_status(" ".join([message, *warnings]))
        return league_id

    # Helpers

    def add_team(self) -> NewTeamDialog | None:
        """A new team in this league, copied from a club chosen in the dialog."""
        league_id = self.league_id
        if league_id is None or league_id not in self._editor_ids:
            return None
        return NewTeamDialog(
            self.app.root, self.app, self._team_created, league_id=league_id
        )

    def _team_created(self, team_id: int, league_id: int, copied: int) -> None:
        self.app.changed()
        self.refresh()
        if self.teams.exists(str(team_id)):
            self.teams.selection_set(str(team_id))
            self.teams.see(str(team_id))
        warnings = self.app.league_repository().league_warnings(league_id)
        players = f" with {copied} copied players" if copied else ""
        message = f"Created team {team_id}{players}. Double-click it to edit it" + (
            " and rename the players." if copied else " and add players."
        )
        self.app.set_status(" ".join([message, *warnings]))

    def _league_created(self, league_id: int) -> None:
        self.app.changed()
        self.show_league(league_id)
        leagues = self.app.league_repository()
        count = leagues.get_league(league_id).team_count
        message = (
            f"Created league {league_id} with {count} teams. {APPLY_HINT}"
            if count
            else f"Created league {league_id}. Add teams with 'Add new team...'."
        )
        self.app.set_status(" ".join([message, *leagues.league_warnings(league_id)]))

    def _load(self, league_id: int) -> None:
        leagues = self.app.league_repository()
        league = leagues.get_league(league_id)
        self.league_id = league_id
        editor = league_id in self._editor_ids
        self.title.set(f"{league.name} (league {league.id})")
        self.form.load({"name": league.name, "country_id": str(league.country_id)})
        self.form.set_enabled(editor)
        warnings = leagues.league_warnings(league_id) if editor else []
        self.note.set(" ".join([EDITOR_NOTE if editor else ORIGINAL_NOTE, *warnings]))
        self.teams.delete(*self.teams.get_children())
        for team in self.app.repository.teams():
            if team.group == league.name:
                self.teams.insert(
                    "", "end", str(team.id), text=team.name, values=(team.player_count,)
                )
        set_enabled(editor, self.save_button, self.add_team_button)
        set_enabled(editor and league.team_count > 0, self.export_button)
        set_enabled(editor and league.team_count == 0, self.delete_button)

    def _clear(self) -> None:
        self.league_id = None
        self.title.set("Choose a league")
        self.note.set("")
        self.form.clear()
        self.teams.delete(*self.teams.get_children())
        set_enabled(False, self.save_button, self.delete_button, self.export_button)

    def _on_select(self) -> None:
        league_id = selected_id(self.list)
        if league_id is not None and league_id != self.league_id:
            self.select_league(league_id)

    def _open_team(self, _event: object = None) -> None:
        team_id = selected_id(self.teams)
        if team_id is not None:
            self.app.show_team(team_id)
