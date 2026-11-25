"""Rotating file log. Never log audio, video or file contents (file names only)."""
from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"


def setup_logging(data_dir: Path, level: str = "INFO") -> Path:
    log_dir = Path(data_dir) / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "handandvoice.log"

    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    for h in list(root.handlers):
        if getattr(h, "_hv", False):
            root.removeHandler(h)
            h.close()

    file_handler = RotatingFileHandler(log_file, maxBytes=512_000, backupCount=3, encoding="utf-8")
    file_handler.setFormatter(logging.Formatter(_FORMAT))
    file_handler._hv = True  # type: ignore[attr-defined]
    root.addHandler(file_handler)

    console = logging.StreamHandler()
    console.setFormatter(logging.Formatter(_FORMAT))
    console._hv = True  # type: ignore[attr-defined]
    root.addHandler(console)
    return log_file
