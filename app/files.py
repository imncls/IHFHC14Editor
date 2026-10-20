"""File helpers shared by the workspace and the portrait store."""

import hashlib
from pathlib import Path

from app.errors import WorkspaceError


def sha256_file(path: Path) -> str:
    with path.open("rb") as file:
        return hashlib.file_digest(file, "sha256").hexdigest()


def delete_inside(path: Path, folder: Path) -> None:
    """Delete a file, refusing anything outside the given app folder."""
    if not path.resolve().is_relative_to(folder.resolve()):
        raise WorkspaceError("Refusing to delete a file outside the app folders.")
    path.unlink(missing_ok=True)
