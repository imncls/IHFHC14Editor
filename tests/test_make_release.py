import importlib.util
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location(
    "make_release", ROOT / "packaging" / "make_release.py"
)
make_release = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(make_release)


def test_release_leaves_out_user_data(tmp_path: Path) -> None:
    build = tmp_path / "IHFHCEditor"
    for name in (
        "IHFHCEditor.exe",
        "_internal/python314.dll",
        "config.local.json",
        "work/clear.db",
        "backups/original/save.dump",
    ):
        (build / name).parent.mkdir(parents=True, exist_ok=True)
        (build / name).write_bytes(b"x")
    target = make_release.make_release(build, tmp_path / "release.zip")
    names = set(zipfile.ZipFile(target).namelist())
    assert {
        "IHFHCEditor/IHFHCEditor.exe",
        "IHFHCEditor/_internal/python314.dll",
        "IHFHCEditor/LICENSE.txt",
        "IHFHCEditor/README.txt",
        "IHFHCEditor/licenses/Python.txt",
        "IHFHCEditor/licenses/Pillow.txt",
    } == names
