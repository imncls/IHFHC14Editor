"""The "New league" wizard: a playable league and its teams in one window.

Each team row copies a club, optionally with its players. Nothing is written
until Create: the league and every team are created in one transaction.
"""

import tkinter as tk
from collections.abc import Callable
from tkinter import ttk
from typing import TYPE_CHECKING

from app.errors import ValidationError
from app.leagues import MIN_LEAGUE_TEAMS
from app.teams import NewTeam
from app.ui.new_team_dialog import TEMPLATE, club_choices
from app.ui.player_form import ValueForm
from app.ui.user_facing_error import guarded, show_error
from app.ui.widgets import follow_abbreviation, make_modal

if TYPE_CHECKING:
    from app.ui.main_window import MainWindow

START_ROWS = MIN_LEAGUE_TEAMS


class LeagueForm(ValueForm):
    def __init__(self, parent: tk.Misc, countries: list[tuple[int, str]]) -> None:
        super().__init__(parent)
        self._add_row("name", "Name", ttk.Entry(self, width=34), 0, 0)
        self._add_row(
            "country_id", "Country", self._choice("country_id", countries), 1, 0
        )
        self.columnconfigure(1, weight=1)


class TeamRow:
    """One team of the new league: club, name, abbreviation, players."""

    def __init__(
        self,
        parent: ttk.Frame,
        row: int,
        clubs: list[str],
        on_change: Callable[[], None],
        on_remove: Callable[["TeamRow"], None],
    ) -> None:
        self.club = tk.StringVar(parent)
        self.name = tk.StringVar(parent)
        self.abbrev = tk.StringVar(parent)
        self.with_players = tk.BooleanVar(parent, value=False)
        self.widgets: dict[str, ttk.Widget] = {
            TEMPLATE: ttk.Combobox(
                parent,
                textvariable=self.club,
                values=clubs,
                state="readonly",
                width=28,
                height=20,
            ),
            "name": ttk.Entry(parent, textvariable=self.name, width=26),
            "abbrev": ttk.Entry(parent, textvariable=self.abbrev, width=6),
            "with_players": ttk.Checkbutton(parent, variable=self.with_players),
            "remove": ttk.Button(
                parent, text="Remove", command=lambda: on_remove(self)
            ),
        }
        for column, widget in enumerate(self.widgets.values()):
            widget.grid(row=row, column=column, sticky="w", padx=(0, 6), pady=2)
        self.club.trace_add("write", lambda *_: on_change())
        follow_abbreviation(self.name, self.abbrev, on_change)

    def is_empty(self) -> bool:
        return not self.club.get() and not self.name.get().strip()

    def destroy(self) -> None:
        for widget in self.widgets.values():
            widget.destroy()


class NewLeagueDialog(tk.Toplevel):
    """A new playable league with the teams it starts with."""

    def __init__(
        self, parent: tk.Misc, app: "MainWindow", on_created: Callable[[int], None]
    ) -> None:
        super().__init__(parent)
        self.app = app
        self._on_created = on_created
        self._clubs = {label: club_id for club_id, label in club_choices(app)}
        self.rows: list[TeamRow] = []
        self._next_row = 1
        self.count = tk.StringVar(self)
        self.title("New league")
        self.form = LeagueForm(self, app.league_repository().countries())
        self.form.pack(fill="x", padx=8, pady=8)
        self.form.load({"name": "", "country_id": ""})

        ttk.Label(self, text="Teams", font=("", 10, "bold")).pack(anchor="w", padx=8)
        ttk.Label(
            self,
            text="Each team copies a club's data, kit and tactics; 'Players' "
            "copies its players too (same names, new ids). Empty rows are skipped.",
            foreground="gray",
        ).pack(anchor="w", padx=8)
        self.rows_frame = ttk.Frame(self, padding=(8, 4))
        self.rows_frame.pack(fill="x")
        for column, text in enumerate(
            ("Copy of (club)", "Name", "Abbreviation", "Players")
        ):
            ttk.Label(self.rows_frame, text=text).grid(
                row=0, column=column, sticky="w", padx=(0, 6)
            )
        for _ in range(START_ROWS):
            self.add_row()
        bar = ttk.Frame(self, padding=(8, 0))
        bar.pack(fill="x")
        ttk.Button(bar, text="Add team", command=guarded(self, self.add_row)).pack(
            side="left"
        )
        ttk.Label(bar, textvariable=self.count).pack(side="left", padx=8)

        buttons = ttk.Frame(self, padding=8)
        buttons.pack(fill="x")
        ttk.Button(buttons, text="Cancel", command=self.destroy).pack(side="right")
        ttk.Button(buttons, text="Create", command=guarded(self, self.create)).pack(
            side="right", padx=(0, 6)
        )
        self._update_count()
        make_modal(self, parent)
        self.form.focus("name")

    def add_row(self) -> TeamRow:
        row = TeamRow(
            self.rows_frame,
            self._next_row,
            list(self._clubs),
            self._update_count,
            self.remove_row,
        )
        self._next_row += 1
        self.rows.append(row)
        self._update_count()
        return row

    def remove_row(self, row: TeamRow) -> None:
        row.destroy()
        self.rows.remove(row)
        self._update_count()

    def create(self) -> int | None:
        """Create the league and its teams. None on an error."""
        rows = [row for row in self.rows if not row.is_empty()]
        values = self.form.values()
        try:
            teams = []
            for index, row in enumerate(rows):
                template_id = self._clubs.get(row.club.get())
                if template_id is None:
                    raise ValidationError(
                        f"Team {index + 1}: choose the club to copy.", TEMPLATE, index
                    )
                teams.append(
                    NewTeam(
                        template_id,
                        row.name.get(),
                        row.abbrev.get(),
                        row.with_players.get(),
                    )
                )
            league_id, created = self.app.team_repository().create_teams(
                teams,
                self.app.original_db(),
                new_league=(values["name"], int(values["country_id"] or 0)),
            )
        except ValidationError as error:
            show_error(self, error)
            if error.team is not None:
                widget = rows[error.team].widgets.get(error.field or "")
                if widget is not None:
                    widget.focus_set()
            else:
                self.form.focus(error.field)
            return None
        portraits = self.app.workspace.portraits
        if portraits is not None:
            for _team_id, copies in created:
                for source_id, player_id in copies.items():
                    portraits.copy(source_id, player_id)
        self.destroy()
        self._on_created(league_id)
        return league_id

    def _update_count(self) -> None:
        count = sum(not row.is_empty() for row in self.rows)
        if count >= MIN_LEAGUE_TEAMS and count % 2 == 0:
            self.count.set(f"{count} teams: ready for league mode.")
        else:
            self.count.set(
                f"{count} teams: league mode needs an even number, "
                f"at least {MIN_LEAGUE_TEAMS} (you can add teams later)."
            )
