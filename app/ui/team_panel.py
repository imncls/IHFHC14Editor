"""One team's editor: logo, name, stars, roles, tactics and venue."""

import tkinter as tk
from collections.abc import Callable
from tkinter import messagebox, ttk
from typing import TYPE_CHECKING

from app.errors import ValidationError
from app.portrait_image import logo_preview, prepare_logo
from app.portraits import entry_name
from app.repository import POOL_TEAM_IDS
from app.teams import (
    CHOICE,
    NUMBER,
    RATING,
    RATING_RANGE,
    ROLE,
    SECTIONS,
    TEAM,
    TEAM_FIELDS,
    TeamField,
)
from app.ui.player_form import ValueForm
from app.ui.user_facing_error import show_error
from app.ui.widgets import ask_image, photo, set_enabled
from app.validation import INT_RANGES

if TYPE_CHECKING:
    from app.ui.main_window import MainWindow

LEAGUE = "league_id"
APPLY_HINT = "Use 'Apply to game' to see it in the game."


class TeamForm(ValueForm):
    """The team fields in one column per section."""

    def __init__(
        self,
        parent: tk.Misc,
        fields: tuple[TeamField, ...],
        options: dict[str, list[tuple[int, str]]],
    ) -> None:
        super().__init__(parent)
        rows = dict.fromkeys(SECTIONS, 1)
        for index, section in enumerate(SECTIONS):
            ttk.Label(self, text=section, font=("", 10, "bold")).grid(
                row=0, column=2 * index, sticky="w", padx=(16 if index else 0, 8)
            )
        for field in fields:
            column = 2 * SECTIONS.index(field.section)
            if field.kind in {CHOICE, ROLE}:
                widget = self._choice(field.key, options.get(field.key, []))
            elif field.kind == NUMBER:
                low, high = INT_RANGES[field.key]
                widget = ttk.Spinbox(self, from_=low, to=high, width=6)
            elif field.kind == RATING:
                low, high = RATING_RANGE
                widget = ttk.Spinbox(self, from_=low, to=high, increment=0.5, width=6)
            else:
                widget = ttk.Entry(self, width=24)
            self._add_row(field.key, field.label, widget, rows[field.section], column)
            rows[field.section] += 1
        self.clear()


class TeamPanel(ttk.Frame):
    """Edits the team shown; `on_change()` follows every saved change."""

    def __init__(
        self, parent: tk.Misc, app: "MainWindow", on_change: Callable[[], None]
    ) -> None:
        super().__init__(parent)
        self.app = app
        self._on_change = on_change
        self.team_id: int | None = None
        self.editor_team = False
        self.title = tk.StringVar(self, "Choose a team")
        self._logo: tk.PhotoImage | None = None
        ttk.Label(self, textvariable=self.title, font=("", 12, "bold")).pack(
            anchor="w", pady=(0, 6)
        )
        self._build_logo_row()
        self.form: TeamForm | None = None
        self._form_holder = ttk.Frame(self)
        self._form_holder.pack(fill="x")
        buttons = ttk.Frame(self)
        buttons.pack(fill="x", pady=6)
        self.save_button = ttk.Button(
            buttons, text="Save", command=app.guard(self.save)
        )
        self.save_button.pack(side="right")
        self.revert_button = ttk.Button(
            buttons, text="Revert team", command=app.guard(self.revert)
        )
        self.revert_button.pack(side="right", padx=(0, 6))
        self.clear()

    # Showing

    def show(self, team_id: int) -> bool:
        """Load a team, after asking about unsaved changes. False: cancelled."""
        if team_id != self.team_id and not self.resolve_unsaved():
            return False
        if team_id in POOL_TEAM_IDS:
            self.clear()
            self.title.set("Pools are not real teams and cannot be edited")
            return True
        self.team_id = team_id
        teams = self.app.team_repository()
        # Only teams made with the editor change league, and only between
        # leagues made with the editor (the game's leagues have fixed fixtures).
        self.editor_team = not self.app.is_original_team(team_id)
        options = {
            field.key: teams.options(field.key, team_id)
            for field in TEAM_FIELDS
            if field.kind in {CHOICE, ROLE}
        }
        fields = TEAM_FIELDS
        if self.editor_team:
            leagues = self.app.editor_leagues()
            options[LEAGUE] = [(league.id, league.name) for league in leagues]
            fields = (TeamField(LEAGUE, "League", CHOICE, TEAM), *TEAM_FIELDS)
        # Roles list this team's players, so the form is built per team.
        if self.form is not None:
            self.form.destroy()
        self.form = TeamForm(self._form_holder, fields, options)
        self.form.pack(fill="x")
        self._load()
        return True

    def clear(self) -> None:
        self.team_id = None
        if self.form is not None:
            self.form.destroy()
            self.form = None
        self.title.set("Choose a team")
        self._show_logo(None)
        self._update_buttons()

    def is_dirty(self) -> bool:
        return self.form is not None and self.form.is_dirty()

    def resolve_unsaved(self) -> bool:
        if not self.is_dirty():
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
        if self.form is None or self.team_id is None:
            return True
        changes = self.form.changes()
        if not changes:
            self.app.set_status("No changes to save.")
            return True
        league = changes.pop(LEAGUE, None)
        teams = self.app.team_repository()
        try:
            if changes:
                teams.set_team_values(self.team_id, changes)
            if league is not None:
                teams.move_team(self.team_id, int(league), self.app.original_db())
        except ValidationError as error:
            show_error(self.app.root, error)
            self.form.focus(error.field)
            return False
        self._changed(f"Saved. {APPLY_HINT}")
        return True

    def revert(self) -> None:
        if self.team_id is None or not messagebox.askyesno(
            "Revert team",
            f"Put back the original data and logo of {self.title.get()}?\n\n"
            "Every change to this team's data, saved or not, is lost.",
            parent=self.app.root,
        ):
            return
        self.app.team_repository().revert_team(self.team_id, self.app.original_db())
        if self.app.workspace.portraits:
            self.app.workspace.portraits.remove_logo(self.team_id)
        self._changed(f"Team reverted. {APPLY_HINT}")

    def change_logo(self) -> None:
        portraits = self.app.workspace.portraits
        if portraits is None or self.team_id is None:
            return
        path = ask_image(self.app.root, "Choose an image for the logo")
        if path is None:
            return
        big, small = prepare_logo(path)
        self._show_logo(logo_preview(big))
        if not messagebox.askyesno(
            "Change logo",
            f"Use this logo for {self.title.get()}?",
            parent=self.app.root,
        ):
            self._show_current_logo()
            return
        portraits.set_logo(self.team_id, big, small)
        self.app.team_repository().use_custom_logo(self.team_id)
        self._changed(f"Logo saved. {APPLY_HINT}")

    def remove_logo(self) -> None:
        portraits = self.app.workspace.portraits
        if portraits is None or self.team_id is None:
            return
        if portraits.remove_logo(self.team_id):
            self.app.team_repository().restore_logo_file(
                self.team_id, self.app.original_db()
            )
            self._changed(f"Custom logo removed. {APPLY_HINT}")

    # Helpers

    def _build_logo_row(self) -> None:
        row = ttk.Frame(self)
        row.pack(fill="x", pady=(0, 6))
        self.logo_label = ttk.Label(row, text="No logo", width=14)
        self.logo_label.pack(side="left")
        actions = ttk.Frame(row)
        actions.pack(side="left", padx=8, anchor="n")
        self.logo_button = ttk.Button(
            actions, text="Change logo...", command=self.app.guard(self.change_logo)
        )
        self.logo_button.pack(fill="x", pady=(0, 4))
        self.remove_logo_button = ttk.Button(
            actions,
            text="Remove custom logo",
            command=self.app.guard(self.remove_logo),
        )
        self.remove_logo_button.pack(fill="x")

    def _load(self) -> None:
        teams = self.app.team_repository()
        team = teams.get_team(self.team_id)
        if self.editor_team:
            team[LEAGUE] = str(teams.league_of(self.team_id))
        self.title.set(f"{team['name']} (#{self.team_id})")
        self.form.load(team)
        self._show_current_logo()
        self._update_buttons()

    def _changed(self, message: str) -> None:
        if self.team_id is not None:
            self.show(self.team_id)  # roles and league lists may have changed
        self.app.changed()
        self._on_change()
        self.app.set_status(message)

    def _show_current_logo(self) -> None:
        """The custom logo, else the one the game data points to."""
        portraits = self.app.workspace.portraits
        dds = None
        if portraits and self.team_id is not None:
            custom = portraits.get_logo(self.team_id)
            logo_file = self.app.team_repository().logo_file(self.team_id)
            if custom:
                dds = custom[0]
            elif logo_file:
                dds = portraits.read_original(entry_name(logo_file))
        self._show_logo(logo_preview(dds) if dds else None)

    def _show_logo(self, png: bytes | None) -> None:
        self._logo = photo(self, png)
        if self._logo is not None:
            self.logo_label.configure(image=self._logo, text="")
        else:
            self.logo_label.configure(image="", text="No logo")

    def _update_buttons(self) -> None:
        shown = self.team_id is not None
        portraits = self.app.workspace.portraits
        custom = (
            shown
            and portraits is not None
            and self.team_id in portraits.custom_logo_ids()
        )
        original = shown and not self.editor_team
        set_enabled(shown, self.save_button)
        set_enabled(original, self.revert_button)
        set_enabled(shown and portraits is not None, self.logo_button)
        set_enabled(custom, self.remove_logo_button)
