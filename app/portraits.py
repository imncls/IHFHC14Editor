"""Player portraits and team logos in guiportraits.v (a ZIP XORed with 0x55).

The installed archive is always the original plus the custom portraits kept
in work/portraits/<player_id>.png and the custom logos kept in
work/logos/<team_id>.dds and <team_id>_small.dds. It is rebuilt from the
original backup, never edited in place. Custom logos are added under their
own names (custom_logo_<team_id>.dds), so no original logo is replaced.
"""

import hashlib
import io
import logging
import os
import re
import shutil
import zipfile
from pathlib import Path

from app.config import PORTRAITS_ARCHIVE
from app.errors import ValidationError, WorkspaceError
from app.files import delete_inside, sha256_file

PORTRAIT_DIR = "gui/images_fullprice/portraits/"
LOGO_DIR = "gui/images_fullprice/logos/teams/"
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
DDS_SIGNATURE = b"DDS "
XOR_TABLE = bytes(byte ^ 0x55 for byte in range(256))
# Fixed timestamp for added entries, so rebuilds are reproducible.
NEW_ENTRY_TIME = (1980, 1, 1, 0, 0, 0)
_CUSTOM_FILE = re.compile(r"(\d+)\.png")
_CUSTOM_LOGO_FILE = re.compile(r"(\d+)\.dds")
_PORTRAIT_ENTRY = re.compile(r"portrait_(\d+)\.png")
_LOGO_ENTRY = re.compile(r"custom_logo_(\d+)\.dds")

activity = logging.getLogger("app.activity")


def portrait_name(player_id: int) -> str:
    return f"{PORTRAIT_DIR}portrait_{player_id}.png"


def logo_name(team_id: int, small: bool = False) -> str:
    """The archive entry of a team's custom logo (the game adds _small)."""
    return f"{LOGO_DIR}custom_logo_{team_id}{'_small' if small else ''}.dds"


def logo_file(team_id: int) -> str:
    """The playerDBTeam.logoFile value for a team's custom logo."""
    return logo_name(team_id).replace("/", "\\")


def entry_name(logo_path: str) -> str:
    """The archive entry of a playerDBTeam.logoFile value."""
    return logo_path.replace("\\", "/")


def small_logo_name(name: str) -> str:
    return name.removesuffix(".dds") + "_small.dds"


def portrait_files(portraits: dict[int, bytes]) -> dict[str, bytes]:
    return {portrait_name(player_id): png for player_id, png in portraits.items()}


def logo_files(logos: dict[int, tuple[bytes, bytes]]) -> dict[str, bytes]:
    files = {}
    for team_id, (big, small) in logos.items():
        files[logo_name(team_id)] = big
        files[logo_name(team_id, small=True)] = small
    return files


def open_archive(encoded: bytes) -> zipfile.ZipFile:
    try:
        return zipfile.ZipFile(io.BytesIO(encoded.translate(XOR_TABLE)))
    except zipfile.BadZipFile:
        raise WorkspaceError("The portrait archive is not valid.") from None


def portrait_entries(archive: zipfile.ZipFile) -> dict[int, zipfile.ZipInfo]:
    """Portrait entries by player id (names compared without case)."""
    entries = {}
    for info in archive.infolist():
        if not info.filename.casefold().startswith(PORTRAIT_DIR):
            continue
        match = _PORTRAIT_ENTRY.fullmatch(info.filename[len(PORTRAIT_DIR) :].casefold())
        if match:
            entries[int(match.group(1))] = info
    return entries


def logo_entries(archive: zipfile.ZipFile) -> dict[int, tuple[bytes, bytes]]:
    """Custom logos in an archive by team id, as (big, small)."""
    names = {info.filename.casefold(): info for info in archive.infolist()}
    logos = {}
    for name, info in names.items():
        if not name.startswith(LOGO_DIR):
            continue
        match = _LOGO_ENTRY.fullmatch(name[len(LOGO_DIR) :])
        small = names.get(small_logo_name(name))
        if match and small is not None:
            logos[int(match.group(1))] = (archive.read(info), archive.read(small))
    return logos


def rebuild(original: bytes, files: dict[str, bytes]) -> bytes:
    """The original archive with these entries replaced or added, encoded.

    Names are compared without case, as the game's own entries vary.
    """
    source = open_archive(original)
    added = {name.casefold(): name for name in files}
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as target:
        for info in source.infolist():
            name = added.pop(info.filename.casefold(), None)
            if name is None:
                target.writestr(info, source.read(info))
                continue
            entry = zipfile.ZipInfo(name, info.date_time)
            entry.compress_type = zipfile.ZIP_DEFLATED
            target.writestr(entry, files[name])
        for name in sorted(added.values()):
            entry = zipfile.ZipInfo(name, NEW_ENTRY_TIME)
            entry.compress_type = zipfile.ZIP_DEFLATED
            target.writestr(entry, files[name])
    _check(output.getvalue(), len(source.infolist()) + len(added), files)
    return output.getvalue().translate(XOR_TABLE)


def changed_portraits(installed: bytes, original: bytes) -> dict[int, bytes]:
    """Portraits of the installed archive that differ from the original."""
    current, base = open_archive(installed), open_archive(original)
    base_entries = portrait_entries(base)
    changed = {}
    for player_id, info in portrait_entries(current).items():
        data = current.read(info)
        old = base_entries.get(player_id)
        if old is None or base.read(old) != data:
            changed[player_id] = data
    return changed


def changed_logos(installed: bytes, original: bytes) -> dict[int, tuple[bytes, bytes]]:
    """Custom logos of the installed archive that the original does not have."""
    base = logo_entries(open_archive(original))
    return {
        team_id: logo
        for team_id, logo in logo_entries(open_archive(installed)).items()
        if base.get(team_id) != logo
    }


def _check(plain: bytes, expected_entries: int, files: dict[str, bytes]) -> None:
    archive = zipfile.ZipFile(io.BytesIO(plain))
    if archive.testzip() is not None or len(archive.infolist()) != expected_entries:
        raise WorkspaceError("The rebuilt portrait archive failed its check.")
    for name, data in files.items():
        if archive.read(name) != data:
            raise WorkspaceError("The rebuilt portrait archive failed its check.")


class PortraitStore:
    """Custom portraits in work/portraits and the original archive backup."""

    def __init__(self, archive_path: Path, root: Path) -> None:
        root = root.resolve()
        self.archive_path = archive_path
        self.custom_dir = root / "work" / "portraits"
        self.logo_dir = root / "work" / "logos"
        self.original_path = root / "backups" / "original" / PORTRAITS_ARCHIVE
        self.original_hash_path = self.original_path.with_name(
            PORTRAITS_ARCHIVE + ".sha256"
        )
        self._original: zipfile.ZipFile | None = None
        self._original_hashes: dict[str, int] | None = None

    # Original backup

    def has_original(self) -> bool:
        return self.original_path.is_file()

    def backup(self, source: Path | None = None) -> Path:
        """Save the original archive once, with its hash. Never overwrites it."""
        if self.original_path.exists():
            raise WorkspaceError("The original portraits are already backed up.")
        source = (source or self.archive_path).resolve()
        if not source.is_file():
            raise WorkspaceError("The portrait archive was not found.")
        if not portrait_entries(open_archive(source.read_bytes())):
            raise WorkspaceError("The file is not the game's portrait archive.")
        self.original_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, self.original_path)
        self.original_hash_path.write_text(sha256_file(self.original_path), "ascii")
        activity.info("backup: original portraits saved from %s", source.name)
        return self.original_path

    def original_bytes(self) -> bytes:
        if not self.original_path.is_file() or not self.original_hash_path.is_file():
            raise WorkspaceError("Back up the original portraits first.")
        data = self.original_path.read_bytes()
        expected = self.original_hash_path.read_text("ascii").strip()
        if hashlib.sha256(data).hexdigest() != expected:
            raise WorkspaceError("The original portraits do not match their hash.")
        return data

    # Custom portraits

    def custom_ids(self) -> list[int]:
        if not self.custom_dir.is_dir():
            return []
        return sorted(
            int(match.group(1))
            for path in self.custom_dir.iterdir()
            if (match := _CUSTOM_FILE.fullmatch(path.name))
        )

    def get(self, player_id: int) -> bytes | None:
        """The portrait the player will have: custom, else the original one."""
        custom = self.custom_dir / f"{player_id}.png"
        if custom.is_file():
            return custom.read_bytes()
        if not self.has_original():
            return None
        info = portrait_entries(self._original_archive()).get(player_id)
        return self._original_archive().read(info) if info else None

    def read_original(self, name: str) -> bytes | None:
        """One entry of the original archive (name compared without case)."""
        if not self.has_original():
            return None
        archive = self._original_archive()
        for info in archive.infolist():
            if info.filename.casefold() == name.casefold():
                return archive.read(info)
        return None

    def original_portrait(self, player_id: int) -> bytes | None:
        """The game's own portrait of a player, if it has one."""
        if not self.has_original():
            return None
        info = portrait_entries(self._original_archive()).get(player_id)
        return self._original_archive().read(info) if info else None

    def game_portrait_of(self, png: bytes) -> int | None:
        """The player whose original game portrait this exact image is, if any
        (a copied player's photo); None for any other image."""
        if not self.has_original():
            return None
        if self._original_hashes is None:
            archive = self._original_archive()
            self._original_hashes = {}
            for player_id, info in sorted(portrait_entries(archive).items()):
                digest = hashlib.sha256(archive.read(info)).hexdigest()
                self._original_hashes.setdefault(digest, player_id)
        return self._original_hashes.get(hashlib.sha256(png).hexdigest())

    def has_custom(self) -> bool:
        return bool(self.custom_ids() or self.custom_logo_ids())

    def _original_archive(self) -> zipfile.ZipFile:
        if self._original is None:
            self._original = open_archive(self.original_bytes())
        return self._original

    def set(self, player_id: int, png: bytes) -> None:
        if player_id <= 0:
            raise ValidationError("The player id must be a positive number.")
        if not png.startswith(PNG_SIGNATURE):
            raise ValidationError("The portrait must be a PNG image.")
        self.custom_dir.mkdir(parents=True, exist_ok=True)
        target = self.custom_dir / f"{player_id}.png"
        staging = target.with_suffix(".tmp")
        staging.write_bytes(png)
        os.replace(staging, target)
        activity.info("player %s: custom portrait set", player_id)

    def copy(self, source_id: int, player_id: int) -> bool:
        """Give a player the portrait another one has, as a custom portrait."""
        png = self.get(source_id)
        if png is None:
            return False
        self.set(player_id, png)
        return True

    def remove(self, player_id: int) -> bool:
        target = self.custom_dir / f"{player_id}.png"
        if not target.is_file():
            return False
        delete_inside(target, self.custom_dir)
        activity.info("player %s: custom portrait removed", player_id)
        return True

    # Custom team logos

    def custom_logo_ids(self) -> list[int]:
        if not self.logo_dir.is_dir():
            return []
        return sorted(
            int(match.group(1))
            for path in self.logo_dir.iterdir()
            if (match := _CUSTOM_LOGO_FILE.fullmatch(path.name))
        )

    def get_logo(self, team_id: int) -> tuple[bytes, bytes] | None:
        """The team's custom logo as (big, small) DDS, if it has one."""
        big = self.logo_dir / f"{team_id}.dds"
        small = self.logo_dir / f"{team_id}_small.dds"
        if not big.is_file() or not small.is_file():
            return None
        return big.read_bytes(), small.read_bytes()

    def set_logo(self, team_id: int, big: bytes, small: bytes) -> None:
        if team_id <= 0:
            raise ValidationError("The team id must be a positive number.")
        if not big.startswith(DDS_SIGNATURE) or not small.startswith(DDS_SIGNATURE):
            raise ValidationError("The logo must be a DDS image.")
        self._write_logo(team_id, big, small)
        activity.info("team %s: custom logo set", team_id)

    def _write_logo(self, team_id: int, big: bytes, small: bytes) -> None:
        self.logo_dir.mkdir(parents=True, exist_ok=True)
        # The small one first: a big logo alone is not a logo yet.
        for name, data in ((f"{team_id}_small.dds", small), (f"{team_id}.dds", big)):
            staging = self.logo_dir / (name + ".tmp")
            staging.write_bytes(data)
            os.replace(staging, self.logo_dir / name)

    def remove_logo(self, team_id: int) -> bool:
        removed = False
        for name in (f"{team_id}.dds", f"{team_id}_small.dds"):
            if (self.logo_dir / name).is_file():
                delete_inside(self.logo_dir / name, self.logo_dir)
                removed = True
        if removed:
            activity.info("team %s: custom logo removed", team_id)
        return removed

    # Installing

    def build(self) -> bytes:
        """The archive to install: the original plus the custom files."""
        original = self.original_bytes()
        portraits = {
            player_id: (self.custom_dir / f"{player_id}.png").read_bytes()
            for player_id in self.custom_ids()
        }
        logos = {team_id: self.get_logo(team_id) for team_id in self.custom_logo_ids()}
        files = portrait_files(portraits) | logo_files(
            {team_id: logo for team_id, logo in logos.items() if logo}
        )
        return rebuild(original, files) if files else original

    def install(self, encoded: bytes) -> bool:
        """Install the archive unless it is already installed. True if written."""
        if self.archive_path.is_file():
            if sha256_file(self.archive_path) == hashlib.sha256(encoded).hexdigest():
                return False
        # Write next to the target first so the final step is an atomic rename.
        staging = self.archive_path.with_name(self.archive_path.name + ".tmp")
        staging.write_bytes(encoded)
        os.replace(staging, self.archive_path)
        return True

    def restore_original(self) -> None:
        self.install(self.original_bytes())
        for player_id in self.custom_ids():
            delete_inside(self.custom_dir / f"{player_id}.png", self.custom_dir)
        for team_id in self.custom_logo_ids():
            self.remove_logo(team_id)
        activity.info("restore: original portraits installed in the game")

    def sync_from_installed(self) -> None:
        """Make the custom portraits and logos match what the game has."""
        installed = self.archive_path.read_bytes()
        original = self.original_bytes()
        changed = changed_portraits(installed, original)
        for player_id in self.custom_ids():
            if player_id not in changed:
                delete_inside(self.custom_dir / f"{player_id}.png", self.custom_dir)
        self.custom_dir.mkdir(parents=True, exist_ok=True)
        for player_id, png in changed.items():
            (self.custom_dir / f"{player_id}.png").write_bytes(png)
        logos = changed_logos(installed, original)
        for team_id in self.custom_logo_ids():
            if team_id not in logos:
                for name in (f"{team_id}.dds", f"{team_id}_small.dds"):
                    delete_inside(self.logo_dir / name, self.logo_dir)
        for team_id, (big, small) in logos.items():
            self._write_logo(team_id, big, small)
