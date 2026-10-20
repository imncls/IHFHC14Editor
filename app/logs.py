"""Logging shared by the editor and the development tools."""

import logging
from pathlib import Path


def setup_logging(work_dir: Path, filename: str = "app.log") -> None:
    """Errors go to app.log; a readable history of changes to activity.log."""
    work_dir.mkdir(exist_ok=True)
    logging.basicConfig(
        filename=work_dir / filename,
        encoding="utf-8",
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    handler = logging.FileHandler(work_dir / "activity.log", encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
    logging.getLogger("app.activity").addHandler(handler)
