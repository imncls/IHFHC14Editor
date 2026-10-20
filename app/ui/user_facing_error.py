"""The only way the UI shows errors: app-authored text, never raw exceptions."""

import logging
import tkinter as tk
from collections.abc import Callable
from tkinter import messagebox

from app.errors import AppError

GENERIC_MESSAGE = "Something went wrong. The details are in work/app.log."

logger = logging.getLogger(__name__)


def user_message(error: BaseException) -> str:
    return str(error) if isinstance(error, AppError) else GENERIC_MESSAGE


def guarded(parent: tk.Misc, action: Callable[[], object]) -> Callable[..., None]:
    """Wrap a UI callback so any error is shown safely."""

    def run(*_event: object) -> None:
        try:
            action()
        except Exception as error:
            show_error(parent, error)

    return run


def show_error(parent: tk.Misc | None, error: BaseException) -> None:
    if isinstance(error, AppError):
        logger.warning("%s: %s", type(error).__name__, error)
    else:
        logger.error("Unexpected error", exc_info=error)
    messagebox.showerror("Error", user_message(error), parent=parent)
