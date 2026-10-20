"""Main window: Home, Players, Teams and Leagues tabs, and the game workflow."""

import logging
import sqlite3
import tkinter as tk
from collections.abc import Callable
from pathlib import Path
from tkinter import messagebox, ttk

from app import CREDIT, __version__, database
from app.appearance import AppearanceOptions, load_options
from app.config import Config, Paths, load_config, save_settings
from app.errors import AppError, FolderError, WorkspaceError
from app.gamecrypto import GameCrypto
from app.leagues import League, LeagueRepository
from app.portraits import PortraitStore
from app.repository import POOL_TEAM_IDS, PlayerRepository
from app.saves import SaveGame, describe, saves_using
from app.teams import TeamRepository
from app.ui.home_tab import HomeTab
from app.ui.leagues_tab import LeaguesTab
from app.ui.new_player_dialog import NewPlayerDialog
from app.ui.players_tab import PlayersTab
from app.ui.settings_form import SettingsTab, SetupWindow
from app.ui.teams_tab import TeamsTab
from app.ui.user_facing_error import guarded, show_error
from app.ui.widgets import set_window_icon
from app.workspace import Workspace

logger = logging.getLogger(__name__)

TITLE = f"IHF Handball Challenge 14 Editor {__version__}"
ABOUT = (
    f"IHF Handball Challenge 14 Editor\n{CREDIT}\n\n"
    "An unofficial tool, not affiliated with Bigben Interactive, Neutron Games "
    "or the IHF. It needs your own installed copy of the game and contains no "
    "part of it."
)


class MainWindow:
    def __init__(
        self,
        root: tk.Tk,
        workspace: Workspace,
        appearance_options: AppearanceOptions | None = None,
        config_path: Path | None = None,
        paths: Paths | None = None,
    ) -> None:
        self.root = root
        self.workspace = workspace
        self.appearance_options = appearance_options
        self.config_path = config_path
        # Set when the Settings tab stores other folders: run_gui then opens
        # the window again, with the working copy loaded from the new game.
        self.restart_requested = False
        self._connection: sqlite3.Connection | None = None
        self.repository = self._connect()
        self.change_count = 0
        # A working copy left from an earlier session may hold changes too.
        self.earlier_changes = False
        self.status = tk.StringVar(root)
        self.changes_text = tk.StringVar(root)
        self._stale: set[ttk.Frame] = set()

        root.title(TITLE)
        root.geometry("1400x780")
        root.minsize(1100, 620)
        root.protocol("WM_DELETE_WINDOW", self.guard(self.close))
        self._build_menu()
        bar = ttk.Frame(root, padding=(8, 6, 8, 0))
        bar.pack(side="top", fill="x")
        ttk.Label(bar, textvariable=self.changes_text).pack(side="left")
        self.apply_button = ttk.Button(bar, command=self.guard(self.apply))
        self.apply_button.pack(side="right")
        # The status bar is packed before the tabs so it always has room.
        self.status_bar = ttk.Label(
            root, textvariable=self.status, anchor="w", padding=(8, 3)
        )
        self.status_bar.pack(side="bottom", fill="x")
        ttk.Separator(root).pack(side="bottom", fill="x")
        self.notebook = ttk.Notebook(root)
        self.notebook.pack(fill="both", expand=True, padx=6, pady=6)
        self.players = PlayersTab(self.notebook, self)
        self.teams = TeamsTab(self.notebook, self)
        self.leagues = LeaguesTab(self.notebook, self)
        self.home = HomeTab(self.notebook, self)
        self.settings = SettingsTab(
            self.notebook, paths if config_path else None, self.save_settings
        )
        for tab, text in (
            (self.home, "Home"),
            (self.players, "Players"),
            (self.teams, "Teams"),
            (self.leagues, "Leagues"),
            (self.settings, "Settings"),
        ):
            self.notebook.add(tab, text=f"  {text}  ")
        self.notebook.bind("<<NotebookTabChanged>>", self.guard(self._on_tab_changed))
        root.bind("<Control-s>", self.guard(self.save_current))
        root.bind("<Control-f>", self.guard(self.focus_search))
        self._update_changes()

    # Shared by the tabs

    @property
    def connection(self) -> sqlite3.Connection:
        return self._connection

    @property
    def tabs(self) -> tuple[ttk.Frame, ...]:
        return (self.home, self.players, self.teams, self.leagues)

    @property
    def unapplied(self) -> bool:
        return self.change_count > 0 or self.earlier_changes

    def guard(self, action: Callable[[], object]) -> Callable[..., None]:
        return guarded(self.root, action)

    def team_repository(self) -> TeamRepository:
        return TeamRepository(self._connection)

    def league_repository(self) -> LeagueRepository:
        return LeagueRepository(self._connection)

    def original_db(self) -> Path:
        return self.workspace.original_clear_db()

    def is_original_player(self, player_id: int) -> bool:
        try:
            return self.repository.is_original(player_id, self.original_db())
        except WorkspaceError:
            return True  # without the original backup nothing counts as new

    def is_original_team(self, team_id: int) -> bool:
        if team_id in POOL_TEAM_IDS:
            return True
        try:
            return self.team_repository().is_original(team_id, self.original_db())
        except WorkspaceError:
            return True

    def editor_leagues(self) -> list[League]:
        try:
            return self.league_repository().editor_leagues(self.original_db())
        except WorkspaceError:
            return []

    def saves_warning(self, saves: list[SaveGame]) -> str:
        """Extra text for a confirmation when saved leagues use what goes away."""
        if not saves:
            return ""
        return (
            "\n\nThese saved leagues use it and may no longer load in the game: "
            f"{describe(saves)}."
        )

    def set_status(self, text: str) -> None:
        self.status.set(text)

    def changed(self) -> None:
        """A change was saved in the working copy: count it, refresh other tabs."""
        self.change_count += 1
        current = self._current_tab()
        self._stale = {tab for tab in self.tabs if tab is not current}
        self._update_changes()

    # Navigation

    def show_tab(self, tab: ttk.Frame) -> None:
        self.notebook.select(tab)
        self._refresh_if_stale(tab)

    def show_player(self, player_id: int) -> None:
        self.show_tab(self.players)
        self.players.show_player(player_id)

    def show_team(self, team_id: int) -> None:
        self.show_tab(self.teams)
        self.teams.show_team(team_id)

    def show_league(self, league_id: int) -> None:
        self.show_tab(self.leagues)
        self.leagues.show_league(league_id)

    def find_player(self, text: str) -> None:
        self.show_tab(self.players)
        self.players.find(text)

    def new_player(self, team_id: int | None) -> NewPlayerDialog | None:
        self.show_tab(self.players)
        return self.players.new_player(team_id)

    def save_current(self) -> None:
        save = getattr(self._current_tab(), "save", None)
        if save is not None:
            save()

    def focus_search(self) -> None:
        self.show_tab(self.players)
        self.players.focus_search()

    # Game workflow

    def apply(self) -> bool:
        if not self._resolve_unsaved():
            return False
        if not messagebox.askyesno(
            "Apply to game",
            "Install the saved changes in the game?\n\n"
            "The installed file is backed up first. The game must be closed.",
            parent=self.root,
        ):
            return False
        self.workspace.apply()
        self.change_count = 0
        self.earlier_changes = False
        self._update_changes()
        self.set_status("Changes installed in the game.")
        return True

    def reload(self) -> None:
        if not self._resolve_unsaved():
            return
        if messagebox.askyesno(
            "Reload from game",
            "Discard every change not applied to the game "
            "and load the installed data again?",
            parent=self.root,
        ):
            self._reload_working_copy()
            self.set_status("Reloaded from the game.")

    def restore(self) -> None:
        teams, leagues = self.team_repository().editor_made(self.original_db())
        using = saves_using(self.workspace.saves_dir, teams, leagues)
        if messagebox.askyesno(
            "Restore original",
            "Install the original game data?\n\n"
            "Every change, in the game and in the editor, is lost."
            + self.saves_warning(using),
            icon="warning",
            parent=self.root,
        ):
            self.workspace.restore()
            self._reload_working_copy()
            self.set_status("Original game data restored.")

    def save_settings(self, paths: Paths) -> bool:
        """Store other folders and open the window again with them. Changes
        not in the game are applied or discarded first: the working copy
        belongs to the game it was loaded from."""
        if self.config_path is None:
            return False
        if not self.settings.changed(paths):
            self.set_status("No changes to save.")
            return False
        if not self._resolve_unsaved():
            return False
        if self.unapplied:
            answer = messagebox.askyesnocancel(
                "Changes not in the game",
                "Some saved changes are not in the game yet, and the editor "
                "loads the data of the new folders.\n\n"
                "Apply them to the current game first? (No discards them.)",
                icon="warning",
                parent=self.root,
            )
            if answer is None or (answer and not self.apply()):
                return False
        save_settings(self.config_path, paths.game_dir, paths.save_dir)
        self.restart_requested = True
        self._disconnect()
        self.root.destroy()
        return True

    def close(self) -> None:
        if not self._resolve_unsaved():
            return
        if self.unapplied:
            answer = messagebox.askyesnocancel(
                "Apply to game?",
                "Some saved changes are not in the game yet. Apply them now?",
                parent=self.root,
            )
            if answer is None or (answer and not self.apply()):
                return
        self._disconnect()
        self.root.destroy()

    # Helpers

    def _build_menu(self) -> None:
        menu = tk.Menu(self.root)
        game = tk.Menu(menu, tearoff=False)
        game.add_command(
            label="Apply to game", accelerator="", command=self.guard(self.apply)
        )
        game.add_command(label="Reload from game", command=self.guard(self.reload))
        game.add_separator()
        game.add_command(label="Restore original...", command=self.guard(self.restore))
        game.add_separator()
        game.add_command(label="Exit", command=self.guard(self.close))
        menu.add_cascade(label="Game", menu=game)
        help_menu = tk.Menu(menu, tearoff=False)
        help_menu.add_command(label="About...", command=self.guard(self.about))
        menu.add_cascade(label="Help", menu=help_menu)
        self.root.configure(menu=menu)

    def about(self) -> None:
        messagebox.showinfo("About", ABOUT, parent=self.root)

    def _current_tab(self) -> ttk.Frame | None:
        selected = self.notebook.select()
        return next((tab for tab in self.tabs if str(tab) == selected), None)

    def _on_tab_changed(self, _event: object = None) -> None:
        tab = self._current_tab()
        if tab is not None:
            self._refresh_if_stale(tab)

    def _refresh_if_stale(self, tab: ttk.Frame) -> None:
        if tab in self._stale:
            self._stale.discard(tab)
            tab.refresh()

    def _update_changes(self) -> None:
        count = self.change_count
        if count:
            plural = "s" if count != 1 else ""
            self.changes_text.set(f"{count} saved change{plural} not in the game yet.")
            self.apply_button.configure(text=f"Apply to game ({count})")
        elif self.earlier_changes:
            self.changes_text.set(
                "The working copy may hold changes from an earlier session."
            )
            self.apply_button.configure(text="Apply to game")
        else:
            self.changes_text.set("The game has every saved change.")
            self.apply_button.configure(text="Apply to game")
        self.root.title(f"{TITLE} *" if self.unapplied else TITLE)

    def _resolve_unsaved(self) -> bool:
        """Ask about unsaved forms in every tab. False means cancel."""
        return all(
            tab.resolve_unsaved()
            for tab in self.tabs
            if hasattr(tab, "resolve_unsaved")
        )

    def _reload_working_copy(self) -> None:
        # The working copy is replaced on disk, so no connection may hold it.
        self._disconnect()
        try:
            self.workspace.start(force=True)
        finally:
            self.repository = self._connect()
        self.change_count = 0
        self.earlier_changes = False
        self._stale.clear()
        for tab in self.tabs:
            tab.reset()
        self._update_changes()

    def _connect(self) -> PlayerRepository:
        self._connection = database.connect(self.workspace.clear_db)
        return PlayerRepository(self._connection)

    def _disconnect(self) -> None:
        if self._connection is not None:
            self._connection.close()
            self._connection = None


def prepare_workspace(parent: tk.Misc, workspace: Workspace) -> tuple[str, bool]:
    """Offer the original backup and make sure a working copy exists.

    Returns a status message and whether an earlier working copy was kept.
    """
    if not workspace.original_path.is_file() and messagebox.askyesno(
        "Original backup",
        "There is no backup of the original game data yet.\n\n"
        "Save the installed data as the original now? "
        "Only do this if it has not been edited.",
        parent=parent,
    ):
        workspace.backup()
    portraits = workspace.portraits
    if (
        portraits
        and not portraits.has_original()
        and messagebox.askyesno(
            "Original portraits",
            "There is no backup of the original player portraits yet.\n\n"
            "Save the installed portraits as the original now? "
            "Only do this if they have not been edited.",
            parent=parent,
        )
    ):
        portraits.backup()
    if workspace.clear_db.is_file():
        return (
            "Using the existing working copy. It may hold changes not applied yet.",
            True,
        )
    workspace.start()
    return "Loaded the installed game data.", False


def run_gui(root_dir: Path) -> int:
    """Open the editor's window; again after the Settings tab changes the
    folders, with the working copy loaded from the new game."""
    reload = False
    while True:
        code, restart = _run_window(root_dir, reload)
        if not restart:
            return code
        reload = True


def load_or_set_up(root: tk.Tk, config_path: Path) -> Config | None:
    """The configuration; when the folders are not found, the setup window
    asks for them first. None if the person quits."""
    while True:
        try:
            return load_config(config_path)
        except FolderError as error:
            if not SetupWindow(root, config_path, str(error)).run():
                return None


def _run_window(root_dir: Path, reload: bool) -> tuple[int, bool]:
    """Run the window once. Returns the exit code and whether to restart."""
    root = tk.Tk()
    root.withdraw()
    set_window_icon(root)
    root.report_callback_exception = lambda _type, error, _tb: show_error(root, error)
    config_path = root_dir / "config.local.json"
    try:
        config = load_or_set_up(root, config_path)
        if config is None:
            root.destroy()
            return 0, False
        crypto = GameCrypto(config.dll_path, config.key)
        portraits = PortraitStore(config.portraits_path, root_dir)
        workspace = Workspace(config.dump_path, crypto, root_dir, portraits=portraits)
        if reload:  # other folders: the working copy comes from the new game
            workspace.start(force=True)
        status, earlier = prepare_workspace(root, workspace)
        try:
            options = load_options(config.gfx_path)
        except AppError as error:
            logger.warning("Appearance unavailable: %s", error)
            options = None
        window = MainWindow(root, workspace, options, config_path, config)
    except Exception as error:
        show_error(root, error)
        root.destroy()
        return 1, False
    window.earlier_changes = earlier
    window._update_changes()
    window.set_status(status)
    root.deiconify()
    root.mainloop()
    return 0, window.restart_requested
