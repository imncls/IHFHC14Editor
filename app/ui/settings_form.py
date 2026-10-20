"""The editor's settings: the game's folder and its save folder.

Used in two places: the setup window, when the folders are not found at start,
and the Settings tab. Folders are checked as they are typed or chosen, and
only stored (in config.local.json) through app.config.save_settings.
"""

import tkinter as tk
from collections.abc import Callable
from pathlib import Path
from tkinter import filedialog, ttk

from app.config import (
    DLL_NAME,
    Paths,
    check_game_dir,
    check_save_dir,
    detected_folders,
    read_settings,
    save_settings,
)
from app.errors import FolderError
from app.ui.user_facing_error import guarded
from app.ui.widgets import make_modal

FIELDS = (
    (
        "game_dir",
        "Game folder",
        f"Where IHF Handball Challenge 14 is installed (it holds {DLL_NAME}).",
        check_game_dir,
    ),
    (
        "save_dir",
        "Save folder",
        "Where the game keeps its data: "
        r"Documents\neutron games\HandballChallenge14\save.",
        check_save_dir,
    ),
)
LABELS = {key: label for key, label, _hint, _check in FIELDS}
OK = "#2e7d32"
WRONG = "#c62828"


class SettingsForm(ttk.Frame):
    """A folder field per setting, with Browse, Detect and its check."""

    def __init__(self, parent: tk.Misc, game_dir: Path | None, save_dir: Path | None):
        super().__init__(parent)
        self.variables: dict[str, tk.StringVar] = {}
        self.checks: dict[str, ttk.Label] = {}
        start = {"game_dir": game_dir, "save_dir": save_dir}
        for row, (key, label, hint, _check) in enumerate(FIELDS):
            variable = tk.StringVar(self, str(start[key]) if start[key] else "")
            self.variables[key] = variable
            ttk.Label(self, text=label, font=("", 10, "bold")).grid(
                row=3 * row, column=0, sticky="w", pady=(10 if row else 0, 0)
            )
            ttk.Label(self, text=hint, foreground="gray").grid(
                row=3 * row,
                column=1,
                columnspan=3,
                sticky="w",
                padx=8,
                pady=(10 if row else 0, 0),
            )
            ttk.Entry(self, textvariable=variable, width=70).grid(
                row=3 * row + 1, column=0, columnspan=2, sticky="ew", pady=2
            )
            ttk.Button(
                self,
                text="Browse...",
                command=guarded(self, lambda k=key: self.browse(k)),
            ).grid(row=3 * row + 1, column=2, padx=(6, 0))
            ttk.Button(
                self, text="Detect", command=guarded(self, lambda k=key: self.detect(k))
            ).grid(row=3 * row + 1, column=3, padx=(6, 0))
            check = ttk.Label(self)
            check.grid(row=3 * row + 2, column=0, columnspan=4, sticky="w")
            self.checks[key] = check
            variable.trace_add("write", lambda *_args: self.check())
        self.columnconfigure(1, weight=1)
        self.check()

    def browse(self, key: str) -> None:
        current = Path(self.variables[key].get().strip() or Path.home())
        chosen = filedialog.askdirectory(
            parent=self,
            title=LABELS[key],
            initialdir=str(current if current.is_dir() else Path.home()),
            mustexist=True,
        )
        if chosen:
            self.variables[key].set(str(Path(chosen)))

    def detect(self, key: str) -> None:
        game_dir, save_dir = detected_folders()
        found = {"game_dir": game_dir, "save_dir": save_dir}[key]
        if found is None:
            self.checks[key].configure(
                text="✗ Not found in the Steam libraries: choose it with Browse.",
                foreground=WRONG,
            )
            return
        self.variables[key].set(str(found))

    def folders(self) -> tuple[Path, Path]:
        return (
            Path(self.variables["game_dir"].get().strip()),
            Path(self.variables["save_dir"].get().strip()),
        )

    def check(self) -> Paths | None:
        """Show each folder's check; the folders if both are right."""
        good = {}
        for key, _label, _hint, check in FIELDS:
            value = self.variables[key].get().strip()
            if not value:
                self.checks[key].configure(
                    text="✗ Choose the folder.", foreground=WRONG
                )
                continue
            try:
                good[key] = check(Path(value))
            except FolderError as error:
                self.checks[key].configure(text=f"✗ {error}", foreground=WRONG)
                continue
            self.checks[key].configure(text="✓ Found.", foreground=OK)
        if len(good) < len(FIELDS):
            return None
        return Paths(game_dir=good["game_dir"], save_dir=good["save_dir"])


def initial_folders(config_path: Path) -> tuple[Path | None, Path | None]:
    """What the setup window starts with: the folders set, else found."""
    settings = read_settings(config_path)
    game_dir, save_dir = detected_folders()
    return (
        Path(settings["game_dir"]) if settings.get("game_dir") else game_dir,
        Path(settings["save_dir"]) if settings.get("save_dir") else save_dir,
    )


class SetupWindow(tk.Toplevel):
    """Shown at start when the game or its data is not found: the folders
    are chosen here and stored, then the editor goes on."""

    def __init__(self, parent: tk.Misc, config_path: Path, reason: str) -> None:
        super().__init__(parent)
        self.config_path = config_path
        self.saved = False
        self.title("Set up the editor")
        frame = ttk.Frame(self, padding=16)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="Where is the game?", font=("", 13, "bold")).pack(
            anchor="w"
        )
        ttk.Label(frame, text=reason, wraplength=640).pack(anchor="w", pady=(4, 12))
        self.form = SettingsForm(frame, *initial_folders(config_path))
        self.form.pack(fill="x")
        ttk.Label(
            frame,
            text="They are kept in config.local.json next to the editor, and can be "
            "changed later in the Settings tab.",
            foreground="gray",
        ).pack(anchor="w", pady=(12, 0))
        buttons = ttk.Frame(frame)
        buttons.pack(fill="x", pady=(12, 0))
        ttk.Button(buttons, text="Quit", command=self.destroy).pack(side="right")
        ttk.Button(
            buttons, text="Continue", command=guarded(self, self.continue_)
        ).pack(side="right", padx=(0, 6))
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        make_modal(self, parent)

    def continue_(self) -> bool:
        paths = self.form.check()
        if paths is None:
            return False
        save_settings(self.config_path, paths.game_dir, paths.save_dir)
        self.saved = True
        self.destroy()
        return True

    def run(self) -> bool:
        """Wait until it closes; True when folders were stored."""
        self.wait_window()
        return self.saved


class SettingsTab(ttk.Frame):
    """The Settings tab: change the folders; the window starts again after."""

    def __init__(
        self,
        parent: tk.Misc,
        paths: Paths | None,
        on_save: Callable[[Paths], None],
    ) -> None:
        super().__init__(parent, padding=24)
        self._paths = paths
        self._on_save = on_save
        ttk.Label(self, text="Settings", font=("", 16, "bold")).pack(anchor="w")
        ttk.Label(
            self,
            text="The editor finds the game in your Steam libraries and its data in "
            "your Documents folder. Change them here if they are elsewhere; the "
            "window opens again with the new folders.",
            foreground="gray",
            wraplength=900,
        ).pack(anchor="w", pady=(2, 18))
        self.form = SettingsForm(
            self, paths.game_dir if paths else None, paths.save_dir if paths else None
        )
        self.form.pack(fill="x")
        ttk.Label(
            self,
            text="Encryption key: found in your game and kept in config.local.json "
            "(it is never shown).",
            foreground="gray",
        ).pack(anchor="w", pady=(18, 0))
        self.save_button = ttk.Button(
            self, text="Save", command=guarded(self, self.save)
        )
        self.save_button.pack(anchor="w", pady=(12, 0))
        if paths is None:
            self.save_button.state(["disabled"])

    def save(self) -> None:
        paths = self.form.check()
        if paths is not None:
            self._on_save(paths)

    def changed(self, paths: Paths) -> bool:
        return self._paths is None or paths != self._paths
