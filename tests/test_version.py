import re
import tomllib
from pathlib import Path

from app import __version__

ROOT = Path(__file__).resolve().parent.parent


def test_versions_match() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert project["project"]["version"] == __version__
    info = (ROOT / "packaging" / "version_info.txt").read_text(encoding="utf-8")
    numbers = tuple(int(part) for part in __version__.split("."))
    for field in ("filevers", "prodvers"):
        assert f"{field}=({', '.join(map(str, (*numbers, 0)))})" in info
    for field in ("FileVersion", "ProductVersion"):
        assert re.search(rf"'{field}', '{re.escape(__version__)}'", info)
