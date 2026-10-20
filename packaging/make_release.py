"""Zip the built program for a release: dist/IHFHCEditor-<version>-windows.zip.

Run after the PyInstaller build (see "Release build" in CONTRIBUTING.md), with the
same Python that made the build. The zip holds the program folder without any
user data (config.local.json, work/, backups/), plus the licence of this
program and the licences of what the build bundles (Python with its Tcl/Tk,
OpenSSL, SQLite... and Pillow).
"""

import importlib.metadata
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import __version__  # noqa: E402 - after the path is set

NAME = "IHFHCEditor"
# What the program writes next to itself: never part of a release.
USER_DATA = {"config.local.json", "work", "backups"}
README = f"""IHF Handball Challenge 14 Editor {__version__} (unofficial)

1. Unzip this folder somewhere you can write to (not under Program Files).
2. Run {NAME}.exe. On the first run it finds what it needs in your installed
   game and offers to back up the original data.
3. Close the game before 'Apply to game'.

Full instructions, the legal notice and the source code:
the project's README in its public repository.
"""


def release_files(build: Path) -> list[Path]:
    """The files of the build folder, without user data."""
    return sorted(
        path
        for path in build.rglob("*")
        if path.is_file() and path.relative_to(build).parts[0] not in USER_DATA
    )


def make_release(build: Path, target: Path) -> Path:
    if not (build / f"{NAME}.exe").is_file():
        raise SystemExit(f"Build {NAME} first: {build} has no {NAME}.exe.")
    pillow = importlib.metadata.distribution("pillow")
    pillow_licence = next(f for f in pillow.files if "LICENSE" in str(f).upper())
    extra = {
        "LICENSE.txt": (ROOT / "LICENSE").read_bytes(),
        "README.txt": README.encode("utf-8"),
        "licenses/Python.txt": (Path(sys.base_prefix) / "LICENSE.txt").read_bytes(),
        "licenses/Pillow.txt": Path(pillow.locate_file(pillow_licence)).read_bytes(),
    }
    target.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in release_files(build):
            archive.write(path, f"{NAME}/{path.relative_to(build).as_posix()}")
        for name, data in extra.items():
            archive.writestr(f"{NAME}/{name}", data)
    return target


if __name__ == "__main__":
    dist = ROOT / "dist"
    zip_path = make_release(dist / NAME, dist / f"{NAME}-{__version__}-windows.zip")
    print(f"Release: {zip_path}")
