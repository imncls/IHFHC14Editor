"""Small widget helpers shared by the editor's tabs and dialogs."""

import base64
import io
import logging
import sys
import tkinter as tk
from collections.abc import Callable, Iterable
from pathlib import Path
from tkinter import filedialog, ttk

from PIL import Image, UnidentifiedImageError

from app.repository import RosterEntry
from app.teams import suggest_abbrev

logger = logging.getLogger(__name__)
# The window icon, in the sizes Windows uses for title bars and the taskbar.
WINDOW_ICON_SIZES = (16, 32, 48, 256)
IMAGE_TYPES = [
    ("Images", "*.jpg *.jpeg *.png *.bmp *.gif"),
    ("All files", "*.*"),
]

ROSTER_COLUMNS = {
    "jersey": ("#", 40),
    "first_name": ("First name", 110),
    "name": ("Name", 130),
    "position": ("Position", 100),
    "average": ("Avg", 50),
    "club": ("Club", 150),
}


def scrolled_tree(
    parent: tk.Misc, columns: tuple[str, ...] = (), show: str = "headings"
) -> ttk.Treeview:
    """A tree view with a vertical scroll bar, filling its parent."""
    frame = ttk.Frame(parent)
    frame.pack(fill="both", expand=True)
    tree = ttk.Treeview(frame, columns=columns, show=show, selectmode="browse")
    scrollbar = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
    tree.configure(yscrollcommand=scrollbar.set)
    scrollbar.pack(side="right", fill="y")
    tree.pack(side="left", fill="both", expand=True)
    return tree


def roster_tree(parent: tk.Misc, club: bool = True) -> ttk.Treeview:
    """A list of players with number, names, position, average and club."""
    columns = [key for key in ROSTER_COLUMNS if club or key != "club"]
    tree = scrolled_tree(parent, tuple(columns))
    for key in columns:
        heading, width = ROSTER_COLUMNS[key]
        tree.heading(key, text=heading)
        tree.column(key, width=width, stretch=key in {"name", "club"})
    return tree


def fill_roster(tree: ttk.Treeview, players: Iterable[RosterEntry]) -> None:
    tree.delete(*tree.get_children())
    columns = tree.cget("columns")
    for player in players:
        tree.insert(
            "",
            "end",
            str(player.id),
            values=tuple(getattr(player, column) for column in columns),
        )


def selected_id(tree: ttk.Treeview) -> int | None:
    selection = tree.selection()
    if not selection or not selection[0].isdigit():
        return None
    return int(selection[0])


def set_enabled(enabled: bool, *widgets: ttk.Widget) -> None:
    for widget in widgets:
        widget.state(["!disabled"] if enabled else ["disabled"])


def make_modal(dialog: tk.Toplevel, parent: tk.Misc) -> None:
    dialog.transient(parent)
    try:
        dialog.grab_set()
    except tk.TclError:
        pass  # not viewable (hidden parent); the dialog still works


def photo(master: tk.Misc, png: bytes | None) -> tk.PhotoImage | None:
    """A PNG as a Tk image, or None if there is none or it cannot be shown."""
    if png is None:
        return None
    try:
        return tk.PhotoImage(master=master, data=base64.b64encode(png).decode("ascii"))
    except tk.TclError:
        return None  # a damaged picture in the game files must not break the editor


def ask_image(parent: tk.Misc, title: str) -> Path | None:
    """Ask for a picture file; None if the person cancels."""
    path = filedialog.askopenfilename(parent=parent, title=title, filetypes=IMAGE_TYPES)
    return Path(path) if path else None


def follow_abbreviation(
    name: tk.StringVar,
    abbrev: tk.StringVar,
    on_change: Callable[[], None] | None = None,
) -> None:
    """Keep a team's abbreviation suggested from its name ("Friends FC" gives
    FRI) until it is edited by hand; empty while the name is."""

    def suggestion() -> str:
        return suggest_abbrev(name.get()) if name.get().strip() else ""

    suggested = suggestion()
    abbrev.set(suggested)

    def follow(*_args: object) -> None:
        nonlocal suggested
        if abbrev.get() == suggested:
            suggested = suggestion()
            abbrev.set(suggested)
        if on_change is not None:
            on_change()

    name.trace_add("write", follow)


def icon_path() -> Path:
    """icons/icon.png: next to the code, or inside the built program."""
    base = getattr(sys, "_MEIPASS", None)
    root = Path(base) if base else Path(__file__).resolve().parents[2]
    return root / "icons" / "icon.png"


def set_window_icon(root: tk.Tk, path: Path | None = None) -> bool:
    """Give every window of the program the editor's icon (title bar and
    taskbar). Without the picture the windows keep the default icon."""
    try:
        with Image.open(path or icon_path()) as image:
            artwork = image.convert("RGBA")
    except (OSError, UnidentifiedImageError) as error:
        logger.warning("Window icon unavailable: %s", error)
        return False
    images = []
    for size in WINDOW_ICON_SIZES:
        output = io.BytesIO()
        artwork.resize((size, size), Image.Resampling.LANCZOS).save(output, "PNG")
        image = photo(root, output.getvalue())
        if image is not None:
            images.append(image)
    if not images:
        return False
    root.iconphoto(True, *images)  # True: dialogs opened later get it too
    root.window_icon_images = images  # Tk images must stay referenced
    return True
