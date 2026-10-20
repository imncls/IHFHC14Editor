import importlib.util
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location(
    "make_icon", ROOT / "packaging" / "make_icon.py"
)
make_icon = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(make_icon)


def test_icon_has_every_size(tmp_path: Path) -> None:
    target = make_icon.make_icon(target=tmp_path / "icon.ico")
    with Image.open(target) as icon:
        assert icon.format == "ICO"
        assert set(icon.info["sizes"]) == set(make_icon.SIZES)
