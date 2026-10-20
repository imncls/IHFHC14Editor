"""Form for one player's data (from playerDBData) and skills."""

import tkinter as tk
from collections.abc import Callable
from tkinter import ttk

from app.averages import AVERAGES, GOALKEEPER, SKILLS
from app.repository import OVERALL, Field, PlayerRepository
from app.validation import INT_RANGES

# Fields not shown: player_img points to no file in the game (portraits go
# by player id, see app/portraits.py).
HIDDEN_FIELDS = {"player_img"}
# Corrections to playerDBData.description; other labels are used as they are.
LABELS = {
    "dob": "Date of birth (d.m.yyyy)",
    "prone_to_inj": "Prone to injuries",
    "player_img": "Image file name",
}
# English versions of the names the game shows (Spanish: Estado físico,
# Potencia de tiro, Precisión, Pase, Velocidad, Salto, Potencia, Agilidad,
# Bloqueo, Comprensión de juego, Reacción).
SKILL_LABELS = {
    "sk_fit": "Fitness",
    "sk_throw": "Throwing power",
    "sk_acc": "Accuracy",
    "sk_pass": "Passing",
    "sk_speed": "Speed",
    "sk_jump": "Jumping",
    "sk_power": "Power",
    "sk_mob": "Agility",
    "sk_block": "Blocking",
    "sk_comp": "Game understanding",
    "sk_react": "Reaction",
}


def label_for(field: Field) -> str:
    text = LABELS.get(field.key, field.description)
    return text[:1].upper() + text[1:]


class ValueForm(ttk.Frame):
    """Labelled values that know what changed since they were loaded.

    Choice fields show labels and store ids.
    """

    def __init__(self, parent: tk.Misc) -> None:
        super().__init__(parent)
        self._variables: dict[str, tk.StringVar] = {}
        self._widgets: dict[str, ttk.Widget] = {}
        self._label_to_id: dict[str, dict[str, str]] = {}
        self._id_to_label: dict[str, dict[str, str]] = {}
        self._loaded: dict[str, str] | None = None

    def _choice(self, key: str, choices: list[tuple[int, str]]) -> ttk.Combobox:
        self._set_choices(key, choices)
        return ttk.Combobox(self, values=[text for _, text in choices], width=24)

    def _set_choices(self, key: str, choices: list[tuple[int, str]]) -> None:
        self._id_to_label[key] = {str(i): text for i, text in choices}
        self._label_to_id[key] = {text: str(i) for i, text in choices}

    def _add_row(
        self, key: str, label: str, widget: ttk.Widget, row: int, column: int
    ) -> None:
        padding = (16, 8) if column else (0, 8)
        ttk.Label(self, text=label).grid(
            row=row, column=column, sticky="w", padx=padding, pady=2
        )
        variable = tk.StringVar(self)
        widget.configure(textvariable=variable)
        widget.grid(row=row, column=column + 1, sticky="ew", pady=2)
        self._variables[key] = variable
        self._widgets[key] = widget

    def update_options(self, key: str, choices: list[tuple[int, str]]) -> None:
        """New labels for a choice field (for example a renamed team)."""
        variable = self._variables[key]
        stored = self._label_to_id[key].get(variable.get(), variable.get())
        self._set_choices(key, choices)
        self._widgets[key].configure(values=[text for _, text in choices])
        if self._loaded is not None:
            variable.set(self._id_to_label[key].get(stored, stored))

    def load(self, values: dict[str, str]) -> None:
        self._loaded = {key: values.get(key, "") for key in self._variables}
        for key, variable in self._variables.items():
            shown = self._id_to_label.get(key, {}).get(self._loaded[key])
            variable.set(self._loaded[key] if shown is None else shown)
        self.set_enabled(True)

    def clear(self) -> None:
        self._loaded = None
        for variable in self._variables.values():
            variable.set("")
        self.set_enabled(False)

    def set(self, key: str, shown: str) -> None:
        self._variables[key].set(shown)

    def put(self, values: dict[str, str]) -> None:
        """Show stored values (choice ids are shown as their labels)."""
        for key, value in values.items():
            self._variables[key].set(self._id_to_label.get(key, {}).get(value, value))

    def on_change(self, key: str, callback: Callable[[], None]) -> None:
        """Call back whenever this field changes, typed or loaded."""
        self._variables[key].trace_add("write", lambda *_: callback())

    def focus(self, key: str | None) -> None:
        if key in self._widgets:
            self._widgets[key].focus_set()

    def changes(self) -> dict[str, str]:
        """The fields whose stored value differs from what was loaded."""
        if self._loaded is None:
            return {}
        changed = {}
        for key, variable in self._variables.items():
            shown = variable.get()
            stored = self._label_to_id.get(key, {}).get(shown, shown)
            if stored != self._loaded[key]:
                changed[key] = stored
        return changed

    def is_dirty(self) -> bool:
        return bool(self.changes())

    def values(self) -> dict[str, str]:
        """Every field's stored value, changed or not."""
        return {
            key: self._label_to_id.get(key, {}).get(variable.get(), variable.get())
            for key, variable in self._variables.items()
        }

    def set_enabled(self, enabled: bool) -> None:
        for widget in self._widgets.values():
            if isinstance(widget, ttk.Combobox):
                widget.configure(state="readonly" if enabled else "disabled")
            else:
                widget.configure(state="normal" if enabled else "disabled")


class PlayerForm(ValueForm):
    def __init__(
        self,
        parent: tk.Misc,
        fields: list[Field],
        options: dict[str, list[tuple[int, str]]],
    ) -> None:
        super().__init__(parent)
        self.averages = tk.StringVar(self)

        for row, field in enumerate(fields):
            if choices := options.get(field.key):
                widget = self._choice(field.key, choices)
            elif field.type == "INT" and field.key in INT_RANGES:
                low, high = INT_RANGES[field.key]
                widget = ttk.Spinbox(self, from_=low, to=high, width=8)
            else:
                widget = ttk.Entry(self, width=26)
            self._add_row(field.key, label_for(field), widget, row, column=0)

        ttk.Label(self, text="Skills", font=("", 10, "bold")).grid(
            row=0, column=2, sticky="w", padx=(16, 8)
        )
        for row, key in enumerate(SKILLS, start=1):
            widget = ttk.Spinbox(self, from_=0, to=100, width=6)
            self._add_row(key, SKILL_LABELS[key], widget, row, column=2)
        row = len(SKILLS) + 1
        ttk.Label(self, textvariable=self.averages).grid(
            row=row, column=2, columnspan=2, sticky="w", padx=(16, 0), pady=(6, 2)
        )
        self._add_row(
            OVERALL, "Overall (goalkeeper)", ttk.Entry(self, width=8), row + 1, 2
        )
        self.columnconfigure(1, weight=1)
        self.clear()
        self.on_change("main_pos", self._update_overall)

    def load(self, values: dict[str, str]) -> None:
        super().load(values)
        self._update_overall()
        self.averages.set(
            "Averages: total {}, defense {}, offense {}".format(
                *(values.get(key, "-") for key in AVERAGES)
            )
        )

    def clear(self) -> None:
        super().clear()
        self.averages.set("")

    def _update_overall(self) -> None:
        """Only goalkeepers have a hand-set overall."""
        if self._loaded is None:
            return
        goalkeeper = self.values().get("main_pos") == str(GOALKEEPER)
        self._widgets[OVERALL].configure(state="normal" if goalkeeper else "disabled")


def player_form(parent: tk.Misc, repository: PlayerRepository) -> PlayerForm:
    """The player form with every editable field, its choices and the teams."""
    fields = [
        field
        for field in repository.fields().values()
        if field.key not in HIDDEN_FIELDS
    ]
    options = {field.key: repository.options(field) for field in fields}
    for field in repository.team_fields():
        fields.append(field)
        options[field.key] = repository.team_options(field.key)
    return PlayerForm(parent, fields, options)
