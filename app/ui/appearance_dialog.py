"""Dialog to edit a player's 3D look (Customize_table)."""

import tkinter as tk
from collections.abc import Callable
from tkinter import messagebox, ttk

from app.appearance import SLIDERS, AppearanceOptions, AppearanceRepository
from app.errors import ValidationError
from app.ui.player_form import ValueForm
from app.ui.user_facing_error import guarded, show_error
from app.ui.widgets import make_modal, photo

HEAD = "Head and hair"
BODY = "Body"
FACE = "Face shapes"
SECTIONS = (HEAD, BODY, FACE)
SELECTED = "#1e6fd9"  # frame of the chosen swatch
FOCUS = "#7a7a7a"  # ring around a palette that has the keyboard focus
_BODY_KEYS = {"muscularity", "body_hairstyle", "shape_body", "shape_body_amount"}


def _section(key: str) -> str:
    if key in _BODY_KEYS:
        return BODY
    if key.startswith("shape_"):
        return FACE
    return HEAD


class SwatchPicker(ttk.Frame):
    """The game's colour swatches in a row; a click chooses one. The row
    takes the focus with Tab, and the arrow keys (Home, End) choose too.

    It works like the form's other widgets: bound to a variable with
    `configure(textvariable=...)` (holding the chosen label) and enabled or
    disabled with `configure(state=...)`.
    """

    def __init__(
        self, parent: tk.Misc, choices: list[tuple[str, str]], images: dict[str, bytes]
    ) -> None:
        super().__init__(parent)
        self._variable: tk.StringVar | None = None
        self._enabled = True
        self._images: list[tk.PhotoImage] = []
        self.swatches: dict[str, tk.Label] = {}
        background = ttk.Style(self).lookup("TFrame", "background") or "#f0f0f0"
        self._background = background
        # A tk frame, for the focus ring the ttk one does not draw.
        row = tk.Frame(
            self,
            takefocus=1,
            background=background,
            highlightthickness=1,
            highlightbackground=background,
            highlightcolor=FOCUS,
        )
        row.pack(anchor="w")
        self._row = row
        for key, step in (("<Left>", -1), ("<Right>", 1)):
            row.bind(key, lambda _event, step=step: self.step(step))
        row.bind("<Home>", lambda _event: self.step(-len(choices)))
        row.bind("<End>", lambda _event: self.step(len(choices)))
        for value, label in choices:
            image = photo(self, images.get(value))
            if image is not None:
                self._images.append(image)
                swatch = tk.Label(row, image=image, borderwidth=0)
            else:  # a picture that cannot be shown: its name instead
                swatch = tk.Label(row, text=label, padx=4)
            swatch.configure(
                highlightthickness=2,
                highlightbackground=background,
                cursor="hand2",
            )
            swatch.pack(side="left", padx=(0, 2))
            swatch.bind("<Button-1>", lambda _event, chosen=label: self._click(chosen))
            self.swatches[label] = swatch
        self.name = ttk.Label(self, foreground="gray")
        self.name.pack(anchor="w")

    def choose(self, label: str) -> None:
        if self._enabled and self._variable is not None:
            self._variable.set(label)

    def step(self, offset: int) -> None:
        """Choose the swatch `offset` places from the chosen one."""
        labels = list(self.swatches)
        current = self._variable.get() if self._variable is not None else ""
        index = labels.index(current) if current in labels else -1
        self.choose(labels[max(0, min(len(labels) - 1, index + offset))])

    def focus_set(self) -> None:
        self._row.focus_set()

    def selected(self) -> str | None:
        """The label of the framed swatch, if any."""
        return next(
            (
                label
                for label, swatch in self.swatches.items()
                if swatch.cget("highlightbackground") == SELECTED
            ),
            None,
        )

    def configure(self, cnf: dict | None = None, **options: object) -> object:
        variable = options.pop("textvariable", None)
        state = options.pop("state", None)
        if variable is not None:
            self._variable = variable
            variable.trace_add("write", lambda *_: self._show())
            self._show()
        if state is not None:
            self._enabled = state != "disabled"
            self._row.configure(takefocus=1 if self._enabled else 0)
            for swatch in self.swatches.values():
                swatch.configure(cursor="hand2" if self._enabled else "")
        if cnf or options:
            return super().configure(cnf, **options)
        return None

    config = configure

    def _click(self, label: str) -> None:
        self.choose(label)
        if self._enabled:
            self._row.focus_set()

    def _show(self) -> None:
        chosen = self._variable.get() if self._variable is not None else ""
        for label, swatch in self.swatches.items():
            colour = SELECTED if label == chosen else self._background
            swatch.configure(highlightbackground=colour, highlightcolor=colour)
        self.name.configure(text=chosen)


class AppearanceForm(ValueForm):
    """Choices from the game's own list (colours as its swatches, when they
    can be read); numbers from 0 to 100."""

    def __init__(self, parent: tk.Misc, options: AppearanceOptions) -> None:
        super().__init__(parent)
        rows = dict.fromkeys(SECTIONS, 1)
        for index, section in enumerate(SECTIONS):
            ttk.Label(self, text=section, font=("", 10, "bold")).grid(
                row=0, column=2 * index, sticky="w", padx=(16 if index else 0, 8)
            )
        for key, label in options.keys():
            section = _section(key)
            if key in SLIDERS or key.endswith("_amount"):
                widget = ttk.Spinbox(self, from_=0, to=100, width=6)
            elif key in options.swatches:
                choices = options.options(key)
                self._set_choices(key, choices)
                widget = SwatchPicker(self, choices, options.swatches[key])
            else:
                widget = self._choice(key, options.options(key))
            column = 2 * SECTIONS.index(section)
            self._add_row(key, label, widget, rows[section], column)
            rows[section] += 1
        self.clear()


class AppearanceDialog(tk.Toplevel):
    def __init__(
        self,
        parent: tk.Misc,
        looks: AppearanceRepository,
        player_id: int,
        title: str,
        on_change: Callable[[], None],
    ) -> None:
        super().__init__(parent)
        self.looks = looks
        self.player_id = player_id
        self._on_change = on_change
        self.status = tk.StringVar(self)
        self.title(f"Appearance: {title}")
        self.form = AppearanceForm(self, looks.options)
        self.form.pack(fill="both", padx=8, pady=8)
        ttk.Label(
            self,
            text="The editor cannot show the 3D model: check the look in the game "
            "(for example in the line-up presentation of a match).",
            foreground="gray",
        ).pack(anchor="w", padx=8)
        buttons = ttk.Frame(self)
        buttons.pack(fill="x", padx=8, pady=4)
        for text, action in (
            ("Close", self.close),
            ("Save", self.save),
            ("Default look", self.default_look),
        ):
            ttk.Button(buttons, text=text, command=guarded(self, action)).pack(
                side="right", padx=(6, 0)
            )
        ttk.Label(self, textvariable=self.status, anchor="w").pack(
            fill="x", padx=8, pady=(0, 6)
        )
        self.protocol("WM_DELETE_WINDOW", guarded(self, self.close))
        self._load()
        make_modal(self, parent)

    def save(self) -> bool:
        # A player without a look gets one with everything shown in the form.
        changes = self.form.changes() if self.has_look else self.form.values()
        if not changes:
            self.status.set("No changes to save.")
            return True
        try:
            self.looks.set(self.player_id, changes)
        except ValidationError as error:
            show_error(self, error)
            self.form.focus(error.field)
            return False
        self._load()
        self._on_change()
        self.status.set("Saved. Use 'Apply to game' to see it in the game.")
        return True

    def default_look(self) -> None:
        if not self.looks.remove(self.player_id):
            self.status.set("This player already has the game's default look.")
            return
        self._load()
        self._on_change()
        self.status.set("The game's default look will be used.")

    def close(self) -> None:
        if self.has_look and self.form.is_dirty():
            answer = messagebox.askyesnocancel(
                "Unsaved changes", "Save the changes to this look?", parent=self
            )
            if answer is None or (answer and not self.save()):
                return
        self.destroy()

    def _load(self) -> None:
        look = self.looks.get(self.player_id)
        self.has_look = look is not None
        self.form.load(look if look is not None else self.looks.default())
        if not self.has_look:
            self.status.set(
                "No custom look: the game uses its default. Save to create one."
            )
