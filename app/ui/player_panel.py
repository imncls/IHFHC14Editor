"""One player's editor: portrait, 3D look, data, skills and the actions."""

import tkinter as tk
from collections.abc import Callable
from tkinter import messagebox, ttk
from typing import TYPE_CHECKING

from app.appearance import AppearanceRepository
from app.errors import ValidationError
from app.portrait_image import prepare_portrait
from app.repository import POOL_TEAM_IDS, TEAM_KINDS, name_warnings
from app.ui.appearance_dialog import AppearanceDialog
from app.ui.player_form import player_form
from app.ui.user_facing_error import show_error
from app.ui.widgets import ask_image, photo, set_enabled

if TYPE_CHECKING:
    from app.ui.main_window import MainWindow

APPLY_HINT = "Use 'Apply to game' to see it in the game."


class PlayerPanel(ttk.Frame):
    """Edits the player shown; `on_change(player_id)` follows every change."""

    def __init__(
        self,
        parent: tk.Misc,
        app: "MainWindow",
        on_change: Callable[[int | None], None],
    ) -> None:
        super().__init__(parent, padding=(8, 0))
        self.app = app
        self._on_change = on_change
        self.player_id: int | None = None
        self.title = tk.StringVar(self, "Choose a player")
        self._portrait: tk.PhotoImage | None = None
        ttk.Label(self, textvariable=self.title, font=("", 12, "bold")).pack(
            anchor="w", pady=(0, 6)
        )
        self._build_portrait_row()
        self.form = player_form(self, app.repository)
        self.form.pack(fill="x")
        buttons = ttk.Frame(self)
        buttons.pack(fill="x", pady=8)
        self.save_button = self._button(buttons, "Save", self.save, "right")
        self.revert_button = self._button(
            buttons, "Revert player", self.revert, "right"
        )
        self.duplicate_button = self._button(buttons, "Duplicate", self.duplicate)
        self.delete_button = self._button(buttons, "Delete player", self.delete)
        self.clear()

    # Showing

    def show(self, player_id: int) -> bool:
        """Load a player, after asking about unsaved changes. False: cancelled."""
        if player_id != self.player_id and not self.resolve_unsaved():
            return False
        self._load(player_id)
        return True

    def clear(self) -> None:
        self.player_id = None
        self.form.clear()
        self.title.set("Choose a player")
        self._show_portrait(None)
        self._update_buttons()

    def refresh(self) -> None:
        """New team names in the dropdowns; reload the player if not edited."""
        for key in TEAM_KINDS:
            self.form.update_options(key, self.app.repository.team_options(key))
        if self.player_id is not None and not self.form.is_dirty():
            self._load(self.player_id)

    def is_dirty(self) -> bool:
        return self.form.is_dirty()

    def resolve_unsaved(self) -> bool:
        """Ask what to do with unsaved changes. False means cancel."""
        if not self.form.is_dirty():
            return True
        answer = messagebox.askyesnocancel(
            "Unsaved changes",
            f"Save the changes to {self.title.get()}?",
            parent=self.app.root,
        )
        if answer is None:
            return False
        if answer:
            return self.save()
        self.clear()
        return True

    # Actions

    def save(self) -> bool:
        changes = self.form.changes()
        if self.player_id is None or not changes:
            self.app.set_status("No changes to save.")
            return True
        try:
            self.app.repository.set_values(self.player_id, changes)
        except ValidationError as error:
            show_error(self.app.root, error)
            self.form.focus(error.field)
            return False
        warnings = name_warnings(changes)
        for key in TEAM_KINDS:
            if key in changes:
                warnings += self.app.repository.team_warnings(
                    int(changes[key]), self.player_id
                )
        self._changed(f"Saved. {APPLY_HINT}", *warnings)
        return True

    def revert(self) -> None:
        if self.player_id is None:
            return
        if not messagebox.askyesno(
            "Revert player",
            f"Put back the original data, skills and look of {self.title.get()}?\n\n"
            "Every change to this player, saved or not, is lost.",
            parent=self.app.root,
        ):
            return
        self.app.repository.revert_player(self.player_id, self.app.original_db())
        if self.app.workspace.portraits:
            self.app.workspace.portraits.remove(self.player_id)
        self._changed(f"Player reverted. {APPLY_HINT}")

    def duplicate(self) -> None:
        source = self.player_id
        if source is None or not self.resolve_unsaved():
            return
        club = int(self.app.repository.get_player(source)["club"])
        team = None if club in POOL_TEAM_IDS else club
        player_id = self.app.repository.duplicate_player(source, team)
        if self.app.workspace.portraits:
            self.app.workspace.portraits.copy(source, player_id)
        self.player_id = player_id
        self._changed(f"Created player {player_id} as a copy. Rename him. {APPLY_HINT}")
        self.form.focus("name")

    def delete(self) -> None:
        player_id = self.player_id
        if player_id is None or not messagebox.askyesno(
            "Delete player",
            f"Delete {self.title.get()}?\n\n"
            "He is removed from his teams and his custom portrait is deleted.",
            icon="warning",
            parent=self.app.root,
        ):
            return
        self.app.repository.delete_player(player_id, self.app.original_db())
        if self.app.workspace.portraits:
            self.app.workspace.portraits.remove(player_id)
        self.clear()
        self.app.changed()
        self._on_change(None)
        self.app.set_status(f"Player deleted. {APPLY_HINT}")

    def change_portrait(self) -> None:
        portraits = self.app.workspace.portraits
        if self.player_id is None or portraits is None:
            return
        path = ask_image(self.app.root, "Choose a photo")
        if path is None:
            return
        png = prepare_portrait(path)
        previous = portraits.get(self.player_id)
        self._show_portrait(png)
        if not messagebox.askyesno(
            "Change portrait",
            f"Use this portrait for {self.title.get()}?",
            parent=self.app.root,
        ):
            self._show_portrait(previous)
            return
        portraits.set(self.player_id, png)
        self._changed(f"Portrait saved. {APPLY_HINT}")

    def remove_portrait(self) -> None:
        portraits = self.app.workspace.portraits
        if self.player_id is None or portraits is None:
            return
        if portraits.remove(self.player_id):
            self._changed(f"Custom portrait removed. {APPLY_HINT}")

    def edit_appearance(self) -> AppearanceDialog | None:
        options = self.app.appearance_options
        if self.player_id is None or options is None:
            return None
        return AppearanceDialog(
            self.app.root,
            AppearanceRepository(self.app.connection, options),
            self.player_id,
            self.title.get(),
            lambda: self._changed(f"Look saved. {APPLY_HINT}"),
        )

    # Helpers

    def _build_portrait_row(self) -> None:
        row = ttk.Frame(self)
        row.pack(anchor="w", pady=(0, 6))
        self.portrait_label = ttk.Label(row, text="No portrait", width=18)
        self.portrait_label.pack(side="left")
        actions = ttk.Frame(row)
        actions.pack(side="left", padx=8, anchor="n")
        self.portrait_button = self._button(
            actions, "Change portrait...", self.change_portrait, fill=True
        )
        self.remove_portrait_button = self._button(
            actions, "Remove custom portrait", self.remove_portrait, fill=True
        )
        self.appearance_button = self._button(
            actions, "Appearance...", self.edit_appearance, fill=True
        )
        self.appearance_note = ttk.Label(actions, foreground="gray")
        self.appearance_note.pack(anchor="w")

    def _button(
        self,
        parent: tk.Misc,
        text: str,
        action: Callable[[], object],
        side: str = "left",
        fill: bool = False,
    ) -> ttk.Button:
        button = ttk.Button(parent, text=text, command=self.app.guard(action))
        if fill:
            button.pack(fill="x", pady=(0, 4))
        else:
            button.pack(side=side, padx=(6, 0) if side == "right" else (0, 6))
        return button

    def _load(self, player_id: int) -> None:
        player = self.app.repository.get_player(player_id)
        self.player_id = player_id
        self.form.load(player)
        self.title.set(
            f"{player.get('first_name', '')} {player.get('name', '')} (#{player_id})"
        )
        portraits = self.app.workspace.portraits
        self._show_portrait(portraits.get(player_id) if portraits else None)
        self._update_buttons()

    def _changed(self, message: str, *warnings: str) -> None:
        """A saved change: reload, tell the window and the owner, report."""
        if self.player_id is not None:
            self._load(self.player_id)
        self.app.changed()
        self._on_change(self.player_id)
        self.app.set_status(" ".join([message, *warnings]))

    def _show_portrait(self, png: bytes | None) -> None:
        self._portrait = photo(self, png)
        if self._portrait is not None:
            self.portrait_label.configure(image=self._portrait, text="")
        else:
            text = "No portrait" if png is None else "Portrait cannot be shown"
            self.portrait_label.configure(image="", text=text)

    def _update_buttons(self) -> None:
        """Only the actions that can work for this player are enabled."""
        shown = self.player_id is not None
        original = shown and self.app.is_original_player(self.player_id)
        portraits = self.app.workspace.portraits
        custom = (
            shown and portraits is not None and self.player_id in portraits.custom_ids()
        )
        set_enabled(shown, self.save_button, self.duplicate_button)
        set_enabled(original, self.revert_button)
        set_enabled(shown and not original, self.delete_button)
        set_enabled(shown and portraits is not None, self.portrait_button)
        set_enabled(custom, self.remove_portrait_button)
        looks = self.app.appearance_options is not None
        set_enabled(shown and looks, self.appearance_button)
        self.appearance_note.configure(text="" if looks else "Needs the game's gfx.v")
