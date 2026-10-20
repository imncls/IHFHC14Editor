"""Backup, start, apply and restore of the game's player database."""

import logging
import os
import shutil
import sqlite3
import subprocess
from collections.abc import Callable
from contextlib import closing
from datetime import datetime
from pathlib import Path

from app.errors import CryptoError, GameRunningError, WorkspaceError
from app.files import delete_inside, sha256_file
from app.gamecrypto import Crypto
from app.portraits import PortraitStore

GAME_EXE = "HC14.exe"
ROTATING_BACKUPS_KEPT = 5

activity = logging.getLogger("app.activity")


def is_game_running() -> bool:
    tasklist = (
        Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "tasklist.exe"
    )
    try:
        result = subprocess.run(  # noqa: S603 - fixed executable and arguments
            [str(tasklist), "/FI", f"IMAGENAME eq {GAME_EXE}", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
            errors="replace",
            check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        raise WorkspaceError("Could not check whether the game is running.") from None
    return f'"{GAME_EXE.lower()}"' in result.stdout.lower()


class Workspace:
    def __init__(
        self,
        dump_path: Path,
        crypto: Crypto,
        root: Path,
        is_game_running: Callable[[], bool] = is_game_running,
        portraits: PortraitStore | None = None,
    ) -> None:
        self.dump_path = dump_path
        self.portraits = portraits
        self._crypto = crypto
        self._is_game_running = is_game_running
        root = root.resolve()
        self.work_dir = root / "work"
        self.clear_db = self.work_dir / "clear.db"
        self.original_clear_path = self.work_dir / "original.db"
        self.installed_clear_path = self.work_dir / "installed.db"
        self.original_path = root / "backups" / "original" / dump_path.name
        self.original_hash_path = self.original_path.with_name(
            dump_path.name + ".sha256"
        )
        self.rotating_dir = root / "backups" / "rotating"
        # The game keeps its saved leagues (SGT_*.sg) next to the save folder.
        self.saves_dir = dump_path.parent.parent

    def backup(self, source: Path | None = None) -> Path:
        """Save the original once, with its hash. Never overwrites it."""
        if self.original_path.exists():
            raise WorkspaceError("The original backup already exists.")
        source = (source or self.dump_path).resolve()
        if not source.is_file():
            raise WorkspaceError("The backup source file does not exist.")
        if not self._crypto.verify(source):
            raise WorkspaceError("The backup source does not open with the key.")
        self.original_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, self.original_path)
        self.original_hash_path.write_text(sha256_file(self.original_path), "ascii")
        activity.info("backup: original saved from %s", source.name)
        return self.original_path

    def start(self, force: bool = False) -> Path:
        """Decrypt the installed database into the working copy."""
        if self.clear_db.exists() and not force:
            raise WorkspaceError(
                "A working copy already exists and may hold unapplied changes. "
                "Use --force to discard it."
            )
        self._require_no_journal()
        self.work_dir.mkdir(parents=True, exist_ok=True)
        staging = self.work_dir / "clear.tmp"
        self._crypto.decrypt(self.dump_path, staging)
        os.replace(staging, self.clear_db)
        if self.portraits and self.portraits.has_original():
            self.portraits.sync_from_installed()
        activity.info("start: working copy loaded from the game")
        return self.clear_db

    def apply(self) -> None:
        """Encrypt the working copy and install it in the save folder."""
        self._require_game_closed()
        if not self.original_path.is_file():
            raise WorkspaceError(
                "Back up the original game data before applying changes."
            )
        if not self.clear_db.is_file():
            raise WorkspaceError("There is no working copy. Run 'start' first.")
        self._require_no_journal()
        self._require_integrity()
        # Built and checked before anything is installed.
        portraits = self._portraits_to_install()
        encrypted = self.work_dir / "encrypted.tmp"
        self._crypto.encrypt(self.clear_db, encrypted)
        if not self._crypto.verify(encrypted):
            raise CryptoError("The encrypted copy does not open with the key.")
        # Portraits and logos first: if they fail, the game is left untouched.
        # If the data then fails, extra custom images in the archive are harmless
        # (nothing points to them yet), while data pointing to missing logos is not.
        if portraits is not None and self.portraits.install(portraits):
            activity.info("apply: portraits installed in the game")
        self._rotate_backup()
        self._install(encrypted)
        delete_inside(encrypted, self.work_dir)
        activity.info("apply: changes installed in the game")

    def restore(self) -> None:
        """Install the original backup again, after checking its hash."""
        self._require_game_closed()
        self._require_verified_original()
        self._rotate_backup()
        self._install(self.original_path)
        if self.portraits and self.portraits.has_original():
            self.portraits.restore_original()
        activity.info("restore: original installed in the game")

    def original_clear_db(self) -> Path:
        """The decrypted original, made once from the verified backup."""
        if not self.original_clear_path.is_file():
            self._require_verified_original()
            self.work_dir.mkdir(parents=True, exist_ok=True)
            staging = self.work_dir / "original.tmp"
            self._crypto.decrypt(self.original_path, staging)
            os.replace(staging, self.original_clear_path)
        return self.original_clear_path

    def snapshot_installed(self) -> tuple[Path, Path]:
        """Decrypt the installed data and the original, to compare or read them.

        Returns (installed, original). The working copy is not touched.
        """
        self.work_dir.mkdir(parents=True, exist_ok=True)
        staging = self.work_dir / "installed.tmp"
        self._crypto.decrypt(self.dump_path, staging)
        os.replace(staging, self.installed_clear_path)
        return self.installed_clear_path, self.original_clear_db()

    def _portraits_to_install(self) -> bytes | None:
        if self.portraits is None:
            return None
        if not self.portraits.has_original():
            if self.portraits.has_custom():
                raise WorkspaceError("Back up the original portraits first.")
            return None
        return self.portraits.build()

    def _require_verified_original(self) -> None:
        if not self.original_path.is_file() or not self.original_hash_path.is_file():
            raise WorkspaceError("There is no original backup.")
        expected = self.original_hash_path.read_text("ascii").strip()
        if sha256_file(self.original_path) != expected:
            raise WorkspaceError("The original backup does not match its hash.")

    def _require_game_closed(self) -> None:
        if self._is_game_running():
            raise GameRunningError(f"Close {GAME_EXE} first.")

    def _require_no_journal(self) -> None:
        if self.clear_db.with_name(self.clear_db.name + "-journal").exists():
            raise WorkspaceError(
                "The working copy has a pending -journal file. "
                "Close any program that has it open."
            )

    def _require_integrity(self) -> None:
        try:
            with closing(sqlite3.connect(self.clear_db)) as connection:
                result = connection.execute("PRAGMA integrity_check").fetchone()
        except sqlite3.DatabaseError:
            result = None
        if result != ("ok",):
            raise WorkspaceError("The working copy failed the integrity check.")

    def _rotate_backup(self) -> None:
        if not self.dump_path.is_file():
            return
        self.rotating_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        name = self.dump_path.name
        shutil.copyfile(self.dump_path, self.rotating_dir / f"{stamp}-{name}")
        backups = sorted(self.rotating_dir.glob(f"*-{name}"))
        for old in backups[:-ROTATING_BACKUPS_KEPT]:
            delete_inside(old, self.rotating_dir)

    def _install(self, source: Path) -> None:
        # Copy next to the target first so the final step is an atomic rename.
        staging = self.dump_path.with_name(self.dump_path.name + ".tmp")
        shutil.copyfile(source, staging)
        os.replace(staging, self.dump_path)
