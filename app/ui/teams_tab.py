"""Teams tab: leagues and their teams, the team editor and its roster."""

import tkinter as tk
from tkinter import messagebox, ttk
from typing import TYPE_CHECKING

from app.errors import ValidationError
from app.repository import NATIONAL_TEAMS_GROUP, POOL_TEAM_IDS, Team
from app.saves import saves_using
from app.ui.new_team_dialog import NewTeamDialog
from app.ui.team_panel import APPLY_HINT, TeamPanel
from app.ui.widgets import fill_roster, roster_tree, scrolled_tree, set_enabled

if TYPE_CHECKING:
    from app.ui.main_window import MainWindow
    from app.ui.new_player_dialog import NewPlayerDialog

GROUP_PREFIX = "group:"


class TeamsTab(ttk.Frame):
    def __init__(self, parent: tk.Misc, app: "MainWindow") -> None:
        super().__init__(parent, padding=6)
        self.app = app
        self.show_unused = tk.BooleanVar(self, value=False)
        self._editor_leagues: dict[str, int] = {}
        self.team_id: int | None = None  # the team chosen, pools included

        panes = ttk.PanedWindow(self, orient="horizontal")
        panes.pack(fill="both", expand=True)
        left = ttk.Frame(panes)
        ttk.Checkbutton(
            left,
            text="Show unused teams",
            variable=self.show_unused,
            command=app.guard(self.refresh_tree),
        ).pack(anchor="w")
        buttons = ttk.Frame(left)
        buttons.pack(side="bottom", fill="x", pady=(4, 0))
        self.new_team_button = ttk.Button(
            buttons, text="New team (copy)", command=app.guard(self.new_team)
        )
        self.new_team_button.pack(side="left")
        self.delete_team_button = ttk.Button(
            buttons, text="Delete team", command=app.guard(self.delete_team)
        )
        self.delete_team_button.pack(side="left", padx=(6, 0))
        self.tree = scrolled_tree(left, show="tree")
        self.tree.bind("<<TreeviewSelect>>", app.guard(self._on_select))
        panes.add(left, weight=1)

        right = ttk.Frame(panes, padding=(8, 0))
        self.panel = TeamPanel(right, app, self._team_changed)
        self.panel.pack(fill="x")
        roster_bar = ttk.Frame(right)
        roster_bar.pack(fill="x", pady=(8, 4))
        ttk.Label(roster_bar, text="Players", font=("", 10, "bold")).pack(side="left")
        ttk.Label(roster_bar, text="(double-click to edit)", foreground="gray").pack(
            side="left", padx=6
        )
        self.new_player_button = ttk.Button(
            roster_bar,
            text="New player in this team",
            command=app.guard(self.new_player),
        )
        self.new_player_button.pack(side="right")
        self.roster = roster_tree(right)
        self.roster.bind("<Double-1>", app.guard(self._open_player))
        self.roster.bind("<Return>", app.guard(self._open_player))
        panes.add(right, weight=3)
        self.refresh_tree()
        self._update_buttons()

    # Tree

    def refresh_tree(self) -> None:
        selected = self.tree.selection()
        open_groups = {
            item for item in self.tree.get_children() if self.tree.item(item, "open")
        }
        self.tree.delete(*self.tree.get_children())
        teams = [
            team
            for team in self.app.repository.teams()
            if self.show_unused.get() or not team.is_unused
        ]
        # Leagues made with the editor show even while empty.
        self._editor_leagues = {
            league.name: league.id for league in self.app.editor_leagues()
        }
        groups = sorted(
            {team.group for team in teams} | set(self._editor_leagues),
            key=lambda group: (group != NATIONAL_TEAMS_GROUP, group.casefold()),
        )
        for group in groups:
            label = f"{group} [editor]" if group in self._editor_leagues else group
            item = GROUP_PREFIX + group
            self.tree.insert("", "end", item, text=label, open=item in open_groups)
        for team in sorted(teams, key=lambda t: (t.is_pool, t.name.casefold())):
            self.tree.insert(
                GROUP_PREFIX + team.group, "end", str(team.id), text=_team_label(team)
            )
        if selected and self.tree.exists(selected[0]):
            self.tree.selection_set(selected[0])
            self.tree.see(selected[0])

    def show_team(self, team_id: int) -> None:
        """Select a team in the tree (showing unused teams if needed)."""
        if not self.tree.exists(str(team_id)):
            self.show_unused.set(True)
            self.refresh_tree()
        if self.tree.exists(str(team_id)):
            self.tree.selection_set(str(team_id))
            self.tree.see(str(team_id))
        self.select_team(team_id)

    def select_team(self, team_id: int) -> None:
        if not self.panel.show(team_id):
            return
        self.team_id = team_id
        fill_roster(self.roster, self.app.repository.team_players(team_id))
        self._update_buttons()

    # Actions

    def new_team(self) -> NewTeamDialog | None:
        """Ask for the name and editor league of a copy of the selected club."""
        if self.team_id is None:
            self.app.set_status("Choose the club to copy in the list first.")
            return None
        teams = self.app.team_repository()
        try:
            teams.check_template(self.team_id)
        except ValidationError as error:
            self.app.set_status(str(error))
            return None
        if not self.panel.resolve_unsaved():
            return None
        return NewTeamDialog(
            self.app.root, self.app, self._team_created, template_id=self.team_id
        )

    def new_team_from_any_club(self) -> NewTeamDialog | None:
        """The new team wizard with the club and the league to choose."""
        if not self.panel.resolve_unsaved():
            return None
        return NewTeamDialog(self.app.root, self.app, self._team_created)

    def delete_team(self) -> None:
        team_id = self.team_id
        if team_id is None or self.app.is_original_team(team_id):
            return
        teams = self.app.team_repository()
        name = teams.get_team(team_id)["name"]
        using = saves_using(self.app.workspace.saves_dir, team_ids={team_id})
        if not messagebox.askyesno(
            "Delete team",
            f"Delete {name}?\n\nIts players go to the pool and its logo is deleted."
            + self.app.saves_warning(using),
            icon="warning",
            parent=self.app.root,
        ):
            return
        teams.delete_team(team_id, self.app.original_db())
        if self.app.workspace.portraits:
            self.app.workspace.portraits.remove_logo(team_id)
        self.team_id = None
        self.panel.clear()
        fill_roster(self.roster, [])
        self.app.changed()
        self.refresh_tree()
        self._update_buttons()
        self.app.set_status(f"{name} deleted. {APPLY_HINT}")

    def new_player(self) -> "NewPlayerDialog | None":
        if self.team_id is None:
            return None
        return self.app.new_player(self.team_id)

    def save(self) -> bool:
        return self.panel.save()

    # Window hooks

    def refresh(self) -> None:
        self.refresh_tree()
        team_id = self.team_id
        if team_id is not None and not self.panel.is_dirty():
            self.select_team(team_id)
        self._update_buttons()

    def reset(self) -> None:
        self.team_id = None
        self.panel.clear()
        fill_roster(self.roster, [])
        self.refresh_tree()
        self._update_buttons()

    def resolve_unsaved(self) -> bool:
        return self.panel.resolve_unsaved()

    # Helpers

    def _team_created(self, team_id: int, league_id: int, copied: int) -> None:
        self.app.changed()
        self.show_team(team_id)  # empty teams count as unused: shown anyway
        warnings = self.app.league_repository().league_warnings(league_id)
        if copied:
            message = (
                f"Created team {team_id} with {copied} copied players. Rename them "
                "from its roster."
            )
        else:
            message = (
                f"Created team {team_id}. Rename it if needed, then add players "
                "with 'New player in this team' or the players' Club dropdown."
            )
        self.app.set_status(" ".join([message, *warnings]))

    def _team_changed(self) -> None:
        self.refresh_tree()
        if self.team_id is not None:
            fill_roster(self.roster, self.app.repository.team_players(self.team_id))
            league_id = self.app.team_repository().league_of(self.team_id)
            if league_id in self._editor_leagues.values():
                warnings = self.app.league_repository().league_warnings(league_id)
                if warnings:
                    self.app.set_status(" ".join([self.app.status.get(), *warnings]))
        self._update_buttons()

    def _on_select(self) -> None:
        selection = self.tree.selection()
        if not selection or selection[0].startswith(GROUP_PREFIX):
            return
        team_id = int(selection[0])
        if team_id != self.team_id:
            self.select_team(team_id)

    def _open_player(self, _event: object = None) -> None:
        selection = self.roster.selection()
        if selection:
            self.app.show_player(int(selection[0]))

    def _update_buttons(self) -> None:
        team_id = self.team_id
        shown = team_id is not None
        can_copy = False
        if shown and team_id not in POOL_TEAM_IDS:
            try:
                self.app.team_repository().check_template(team_id)
                can_copy = True
            except ValidationError:
                can_copy = False
        set_enabled(can_copy, self.new_team_button)
        set_enabled(
            shown and not self.app.is_original_team(team_id), self.delete_team_button
        )
        set_enabled(shown, self.new_player_button)


def _team_label(team: Team) -> str:
    suffix = " [pool]" if team.is_pool else ""
    return f"{team.name}{suffix} ({team.player_count})"
