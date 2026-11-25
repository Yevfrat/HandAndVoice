"""Opens files chosen from the index. Executables are never opened."""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Callable

from core.command import Result
from services.file_indexer import BLOCKED_EXTENSIONS, FileIndexer, LookupResult

log = logging.getLogger(__name__)


class FileManager:
    def __init__(self, indexer: FileIndexer, opener: Callable[[str], None] = os.startfile):
        self.indexer = indexer
        self._opener = opener

    def lookup(self, query: str) -> LookupResult:
        return self.indexer.lookup(query)

    def open(self, path: str) -> Result:
        p = Path(path)
        ext = p.suffix.lower().lstrip(".")
        if ext in BLOCKED_EXTENSIONS:
            return Result.error(f"Refusing to open a program or script: {p.name}")
        if not p.is_file():
            return Result.error(f"File no longer exists: {p.name}")
        try:
            self._opener(str(p))
        except OSError as exc:
            return Result.error(f"Could not open {p.name}: {exc}")
        log.info("Opening file %s", p.name)       # file name only, never the path or contents
        return Result.success(f"Opening {p.name}")
