"""The "New team" wizard: a copy of a club, its sheet and a logo in one window.

Teams made with the editor only go into leagues made with the editor, since
the game's own leagues have fixed fixtures (see docs/game-data.md). Nothing is
written until Create: the team (and a new league, if chosen) is created in one
transaction, then the logo and the copied players' portraits are stored.
"""

import tkinter as tk
from collections.abc import Callable
from tkinter import ttk
from typing import TYPE_CHECKING

from app.errors import ValidationError
from app.portrait_image import logo_preview, prepare_logo
from app.repository import POOL_TEAM_IDS
from app.teams import CHOICE, DETAIL_KEYS, FIELDS_BY_KEY, NewTeam
from app.ui.team_panel import TeamForm
from app.ui.user_facing_error import guarded, show_error
from app.ui.widgets import ask_image, follow_abbreviation, make_modal, photo

if TYPE_CHECKING:
    from app.ui.main_window import MainWindow

NEW_LEAGUE = "(new league)"
TEMPLATE = "template"  # the error field of a missing club


def club_choices(app: "MainWindow") -> list[tuple[int, str]]:
    """The clubs a new team can copy: every club but the pool."""
    return [
        (team_id, label)
        for team_id, label in app.repository.team_options("club")
        if team_id not in POOL_TEAM_IDS
    ]


class NewTeamDialog(tk.Toplevel):
    """A new team: the club it copies, the editor league it goes into, its
    sheet and logo, and optionally copies of the club's players.

    From the Teams tab the club is fixed; from the Leagues tab the league is
    fixed; from Home both are chosen.
    """

    def __init__(
        self,
        parent: tk.Misc,
        app: "MainWindow",
        on_created: Callable[[int, int, int], None],
        template_id: int | None = None,
        league_id: int | None = None,
    ) -> None:
        super().__init__(parent)
        self.app = app
        self.teams = app.team_repository()
        self.template_id = template_id
        self._on_created = on_created
        self.logo: tuple[bytes, bytes] | None = None
        self._logo_image: tk.PhotoImage | None = None
        editor_leagues = app.editor_leagues()
        self._league_ids = {league.name: league.id for league in editor_leagues}
        self._country_ids = {
            name: cid for cid, name in app.league_repository().countries()
        }
        self._clubs = (
            {}
            if template_id is not None
            else {label: club_id for club_id, label in club_choices(app)}
        )
        fixed_league = next(
            (league.name for league in editor_leagues if league.id == league_id), None
        )
        if template_id is not None:
            template = self.teams.get_team(template_id)["name"]
            self.title(f"New team (copy of {template})")
        elif fixed_league:
            self.title(f"New team in {fixed_league}")
        else:
            self.title("New team")

        self.name = tk.StringVar(self, self.teams.free_name())
        self.abbrev = tk.StringVar(self)
        follow_abbreviation(self.name, self.abbrev)
        self.template = tk.StringVar(self)
        self.league = tk.StringVar(
            self,
            fixed_league or (editor_leagues[0].name if editor_leagues else NEW_LEAGUE),
        )
        self.league_name = tk.StringVar(self)
        self.country = tk.StringVar(self)
        self.with_players = tk.BooleanVar(self, value=False)
        self.players_text = tk.StringVar(self)

        self.tabs = ttk.Notebook(self)
        self.tabs.pack(fill="both", expand=True, padx=8, pady=(8, 0))
        self.team_tab = ttk.Frame(self.tabs, padding=8)
        self.tabs.add(self.team_tab, text="Team")
        self.details_tab = ttk.Frame(self.tabs, padding=8)
        self.tabs.add(self.details_tab, text="Details")
        self._build_team_tab(fixed_league is not None)
        options = {
            key: self.teams.options(key, 0)
            for key in DETAIL_KEYS
            if FIELDS_BY_KEY[key].kind == CHOICE
        }
        self.details = TeamForm(
            self.details_tab, tuple(FIELDS_BY_KEY[key] for key in DETAIL_KEYS), options
        )
        self.details.pack(fill="both")
        ttk.Label(
            self.details_tab,
            text="Filled from the club it copies. Roles (captain, shooter...) "
            "follow the copied players; set them later in the Teams tab.",
            foreground="gray",
        ).pack(anchor="w", pady=(8, 0))

        buttons = ttk.Frame(self, padding=8)
        buttons.pack(fill="x")
        ttk.Button(buttons, text="Cancel", command=self.destroy).pack(side="right")
        ttk.Button(buttons, text="Create", command=guarded(self, self.create)).pack(
            side="right", padx=(0, 6)
        )
        self.league.trace_add("write", lambda *_: self._toggle_new_league())
        self.template.trace_add("write", lambda *_: self._club_changed())
        self._toggle_new_league()
        self._club_changed()
        make_modal(self, parent)

    def create(self) -> int | None:
        """Create the team (and its new league). None on an error."""
        try:
            template_id = self._template_id()
            if template_id is None:
                raise ValidationError("Choose the club to copy.", TEMPLATE)
            team = NewTeam(
                template_id,
                self.name.get(),
                self.abbrev.get(),
                self.with_players.get(),
                self.details.changes(),
            )
            if self.league.get() == NEW_LEAGUE:
                country = self._country_ids.get(self.country.get())
                if country is None:
                    raise ValidationError(
                        "Choose the country of the new league.", "country_id"
                    )
                league_id, created = self.teams.create_teams(
                    [team],
                    self.app.original_db(),
                    new_league=(self.league_name.get(), country),
                )
            else:
                league_id, created = self.teams.create_teams(
                    [team], self.app.original_db(), self._league_ids[self.league.get()]
                )
        except ValidationError as error:
            self._error(error)
            return None
        team_id, copies = created[0]
        portraits = self.app.workspace.portraits
        if portraits is not None:
            if self.logo is not None:
                portraits.set_logo(team_id, *self.logo)
                self.teams.use_custom_logo(team_id)
            for source_id, player_id in copies.items():
                portraits.copy(source_id, player_id)
        self.destroy()
        self._on_created(team_id, league_id, len(copies))
        return team_id

    def choose_logo(self) -> None:
        path = ask_image(self, "Choose an image for the logo")
        if path is not None:
            self._show_logo(prepare_logo(path))

    def remove_logo(self) -> None:
        self._show_logo(None)

    # Helpers

    def _build_team_tab(self, fixed_league: bool) -> None:
        form = ttk.Frame(self.team_tab)
        form.grid(row=0, column=0, sticky="nw")
        self._widgets: dict[str, ttk.Widget] = {
            "name": ttk.Entry(form, textvariable=self.name, width=34),
            "abbrev": ttk.Entry(form, textvariable=self.abbrev, width=6),
        }
        rows = [("Team name", "name"), ("Abbreviation (scoreboard)", "abbrev")]
        if self.template_id is None:
            self._widgets[TEMPLATE] = ttk.Combobox(
                form,
                textvariable=self.template,
                values=list(self._clubs),
                state="readonly",
                height=20,
            )
            rows.append(("Copy of (club)", TEMPLATE))
        self._widgets["league_id"] = ttk.Combobox(
            form,
            textvariable=self.league,
            values=[*self._league_ids, NEW_LEAGUE],
            state="disabled" if fixed_league else "readonly",
        )
        rows.append(("League", "league_id"))
        self._new_league_widgets: list[ttk.Widget] = []
        if not fixed_league:
            self._widgets["league_name"] = ttk.Entry(
                form, textvariable=self.league_name
            )
            self._widgets["country_id"] = ttk.Combobox(
                form,
                textvariable=self.country,
                values=list(self._country_ids),
                state="readonly",
            )
            rows += [
                ("New league name", "league_name"),
                ("New league country", "country_id"),
            ]
            self._new_league_widgets = [
                self._widgets["league_name"],
                self._widgets["country_id"],
            ]
        for row, (label, key) in enumerate(rows):
            ttk.Label(form, text=label).grid(row=row, column=0, sticky="w", pady=2)
            sticky = "w" if key == "abbrev" else "ew"
            self._widgets[key].grid(
                row=row, column=1, sticky=sticky, padx=(8, 0), pady=2
            )
        self.players_check = ttk.Checkbutton(
            form, textvariable=self.players_text, variable=self.with_players
        )
        self.players_check.grid(row=len(rows), column=1, sticky="w", padx=(8, 0))
        ttk.Label(
            form,
            text="The new team copies the club's data, kit and tactics. Copied "
            "players\nare new players with the same names, portraits and looks; "
            "the club\nkeeps its own. New teams go into leagues made with the "
            "editor:\nthe game's own leagues only take their original teams.",
            foreground="gray",
        ).grid(row=len(rows) + 1, column=0, columnspan=2, sticky="w", pady=(6, 0))

        side = ttk.Frame(self.team_tab, padding=(16, 0, 0, 0))
        side.grid(row=0, column=1, sticky="n")
        self.logo_label = ttk.Label(side, text="No logo", width=14)
        self.logo_label.pack()
        self.logo_button = ttk.Button(
            side, text="Choose logo...", command=guarded(self, self.choose_logo)
        )
        self.logo_button.pack(fill="x", pady=(6, 0))
        self.remove_logo_button = ttk.Button(
            side, text="Remove logo", command=guarded(self, self.remove_logo)
        )
        self.remove_logo_button.pack(fill="x", pady=(4, 0))
        self.remove_logo_button.state(["disabled"])
        if self.app.workspace.portraits is None:
            self.logo_button.state(["disabled"])
            ttk.Label(side, text="Needs the game's portraits", foreground="gray").pack()

    def _show_logo(self, logo: tuple[bytes, bytes] | None) -> None:
        self.logo = logo
        self._logo_image = photo(self, logo_preview(logo[0])) if logo else None
        if self._logo_image is not None:
            self.logo_label.configure(image=self._logo_image, text="")
        else:
            self.logo_label.configure(image="", text="No logo")
        self.remove_logo_button.state(["!disabled"] if logo else ["disabled"])

    def _error(self, error: ValidationError) -> None:
        """Show the tab and field the error is about."""
        field = error.field
        if error.team is None and field == "name":
            field = "league_name"  # the new league's name
        if field in DETAIL_KEYS:
            self.tabs.select(self.details_tab)
            show_error(self, error)
            self.details.focus(field)
            return
        self.tabs.select(self.team_tab)
        show_error(self, error)
        if field in self._widgets:
            self._widgets[field].focus_set()

    def _template_id(self) -> int | None:
        if self.template_id is not None:
            return self.template_id
        return self._clubs.get(self.template.get())

    def _club_changed(self) -> None:
        """The players' count, the sheet and the new league's country follow
        the club; fields already edited in the sheet keep their values."""
        template_id = self._template_id()
        count = 0 if template_id is None else self.teams.player_count(template_id)
        suffix = "" if template_id is None else f" ({count})"
        self.players_text.set(f"Copy the club's players too{suffix}")
        self.players_check.state(["!disabled"] if count else ["disabled"])
        if not count:
            self.with_players.set(False)
        if template_id is None:
            self.details.clear()
            return
        edits = self.details.changes()  # kept over the new club's sheet
        self.details.load(self.teams.get_team(template_id))
        self.details.put(edits)
        country = self.teams.country_of(template_id)
        if not self.country.get():
            self.country.set(
                next((n for n, cid in self._country_ids.items() if cid == country), "")
            )

    def _toggle_new_league(self) -> None:
        new = self.league.get() == NEW_LEAGUE
        for widget in self._new_league_widgets:
            enabled = "readonly" if isinstance(widget, ttk.Combobox) else "normal"
            widget.configure(state=enabled if new else "disabled")
