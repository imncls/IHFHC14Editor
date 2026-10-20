"""The "New player" wizard: data, skills, photo and 3D look in one window.

Nothing is written until Create: the look is checked first, then the player
is created in one transaction, then his look and photo are stored.
"""

import tkinter as tk
from collections.abc import Callable
from tkinter import ttk
from typing import TYPE_CHECKING

from app.appearance import AppearanceRepository
from app.averages import GOALKEEPER
from app.errors import ValidationError
from app.portrait_image import prepare_portrait
from app.repository import OVERALL, POOL_TEAM_IDS
from app.ui.appearance_dialog import AppearanceForm
from app.ui.player_form import ValueForm, player_form
from app.ui.user_facing_error import guarded, show_error
from app.ui.widgets import ask_image, make_modal, photo

if TYPE_CHECKING:
    from app.ui.main_window import MainWindow

NAME_KEYS = ("name", "first_name")  # in the order the form shows them
LOOK_NOTE = (
    "Leave it as it is for the game's default look. The editor cannot show the "
    "3D model: check it in the game (for example in the line-up presentation)."
)


class NewPlayerDialog(tk.Toplevel):
    def __init__(
        self,
        parent: tk.Misc,
        app: "MainWindow",
        team_id: int | None,
        on_created: Callable[[int], None],
    ) -> None:
        super().__init__(parent)
        self.app = app
        self.team_id = team_id
        self._on_created = on_created
        self.png: bytes | None = None
        self._portrait: tk.PhotoImage | None = None
        self.title("New player")

        self.tabs = ttk.Notebook(self)
        self.tabs.pack(fill="both", expand=True, padx=8, pady=(8, 0))
        self.player_tab = ttk.Frame(self.tabs, padding=8)
        self.tabs.add(self.player_tab, text="Player")
        self.form = player_form(self.player_tab, app.repository)
        self.form.grid(row=0, column=0, sticky="nw")
        self._build_photo(self.player_tab)

        self.look_tab = ttk.Frame(self.tabs, padding=8)
        self.tabs.add(self.look_tab, text="Look")
        options = app.appearance_options
        self.looks = AppearanceRepository(app.connection, options) if options else None
        self.look_form: AppearanceForm | None = None
        if self.looks is not None:
            self.look_form = AppearanceForm(self.look_tab, self.looks.options)
            self.look_form.pack(fill="both")
            self.look_form.load(self.looks.default())
            ttk.Label(
                self.look_tab, text=LOOK_NOTE, foreground="gray", wraplength=600
            ).pack(anchor="w", pady=(8, 0))
        else:
            self.tabs.tab(self.look_tab, state="disabled")

        buttons = ttk.Frame(self, padding=8)
        buttons.pack(fill="x")
        ttk.Button(buttons, text="Cancel", command=self.destroy).pack(side="right")
        ttk.Button(buttons, text="Create", command=guarded(self, self.create)).pack(
            side="right", padx=(0, 6)
        )

        defaults = app.repository.new_player_values(team_id)
        self._suggested_jersey = defaults["jersey_nr"]
        self.form.load({**defaults, **dict.fromkeys(NAME_KEYS, "")})
        self.form.averages.set("")  # known once the player exists
        self.form.on_change("club", self._follow_club)
        make_modal(self, parent)
        self.form.focus(NAME_KEYS[0])

    def create(self) -> int | None:
        """Check everything, then create the player. None on an error."""
        current = self.form.values()
        values = self.form.changes() | {key: current[key] for key in NAME_KEYS}
        if current.get("main_pos") != str(GOALKEEPER) or not current.get(OVERALL):
            values.pop(OVERALL, None)
        look = self.look_form.changes() if self.look_form else {}
        if look and self.looks is not None:
            try:
                self.looks.check(look)
            except ValidationError as error:
                return self._error(self.look_tab, self.look_form, error)
        try:
            player_id = self.app.repository.create_player(self.team_id, values)
        except ValidationError as error:
            return self._error(self.player_tab, self.form, error)
        if look and self.looks is not None:
            self.looks.set(player_id, look)
        portraits = self.app.workspace.portraits
        if self.png is not None and portraits is not None:
            portraits.set(player_id, self.png)
        self.destroy()
        self._on_created(player_id)
        return player_id

    def choose_photo(self) -> None:
        path = ask_image(self, "Choose a photo")
        if path is not None:
            self._show_photo(prepare_portrait(path))

    def remove_photo(self) -> None:
        self._show_photo(None)

    def _build_photo(self, parent: ttk.Frame) -> None:
        side = ttk.Frame(parent, padding=(16, 0, 0, 0))
        side.grid(row=0, column=1, sticky="n")
        self.photo_label = ttk.Label(side, text="No photo", width=18)
        self.photo_label.pack()
        self.photo_button = ttk.Button(
            side, text="Choose photo...", command=guarded(self, self.choose_photo)
        )
        self.photo_button.pack(fill="x", pady=(6, 0))
        self.remove_photo_button = ttk.Button(
            side, text="Remove photo", command=guarded(self, self.remove_photo)
        )
        self.remove_photo_button.pack(fill="x", pady=(4, 0))
        self.remove_photo_button.state(["disabled"])
        if self.app.workspace.portraits is None:
            self.photo_button.state(["disabled"])
            ttk.Label(side, text="Needs the game's portraits", foreground="gray").pack()

    def _show_photo(self, png: bytes | None) -> None:
        self.png = png
        self._portrait = photo(self, png)
        if self._portrait is not None:
            self.photo_label.configure(image=self._portrait, text="")
        else:
            text = "No photo" if png is None else "Photo cannot be shown"
            self.photo_label.configure(image="", text=text)
        self.remove_photo_button.state(["!disabled"] if png else ["disabled"])

    def _follow_club(self) -> None:
        """A suggested jersey number follows the club until it is typed."""
        club = self.form.values().get("club", "")
        if not club.isdigit() or self.form.values()["jersey_nr"] != (
            self._suggested_jersey
        ):
            return
        team = None if int(club) in POOL_TEAM_IDS else int(club)
        self._suggested_jersey = self.app.repository.new_player_values(team)[
            "jersey_nr"
        ]
        self.form.set("jersey_nr", self._suggested_jersey)

    def _error(
        self, tab: ttk.Frame, form: ValueForm | None, error: ValidationError
    ) -> None:
        self.tabs.select(tab)
        show_error(self, error)
        if form is not None:
            form.focus(error.field)
