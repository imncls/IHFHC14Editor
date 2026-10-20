"""Make build/icon.ico (the icon of IHFHCEditor.exe) from icons/icon.png.

Run before the PyInstaller build (see "Release build" in CONTRIBUTING.md).
The .ico is made at build time, so the only icon kept in the repository is
the artwork itself.
"""

from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "icons" / "icon.png"
TARGET = ROOT / "build" / "icon.ico"
# The sizes Windows asks for: title bars, taskbar, Explorer views.
SIZES = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]


def make_icon(source: Path = SOURCE, target: Path = TARGET) -> Path:
    with Image.open(source) as image:
        artwork = image.convert("RGBA")
    target.parent.mkdir(parents=True, exist_ok=True)
    artwork.save(target, format="ICO", sizes=SIZES)
    return target


if __name__ == "__main__":
    print(f"Icon: {make_icon()}")
