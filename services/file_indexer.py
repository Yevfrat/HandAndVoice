"""SQLite index of the user's folders (file names only, never file contents)."""
from __future__ import annotations

import logging
import os
import sqlite3
import threading
from collections import Counter
from contextlib import closing
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Optional

from rapidfuzz import fuzz

from voice.text_normalizer import normalize

log = logging.getLogger(__name__)

SKIP_DIR_NAMES = {"node_modules", ".git", "venv", ".venv", "__pycache__"}
# Never indexed, never opened from the index, even if the user adds them to the extension list.
BLOCKED_EXTENSIONS = {"exe", "bat", "cmd", "ps1", "msi", "com", "scr", "vbs", "vbe", "js", "jse",
                      "wsf", "lnk", "reg", "dll", "jar", "hta", "pif", "msc", "cpl"}
FILE_ATTRIBUTE_HIDDEN = 0x2
FILE_ATTRIBUTE_SYSTEM = 0x4
MAX_FILES = 150_000
MAX_DEPTH = 12
MAX_PICKER = 9


# ---------------------------------------------------------------- folders
def known_folder(name: str) -> Optional[Path]:
    """Documents/Desktop/Downloads as Windows knows them (works with OneDrive redirection)."""
    try:
        from win32com.shell import shell

        fid = {"documents": shell.FOLDERID_Documents, "desktop": shell.FOLDERID_Desktop,
               "downloads": shell.FOLDERID_Downloads}.get(name.lower())
        if fid is not None:
            return Path(shell.SHGetKnownFolderPath(fid, 0, 0))
    except Exception:
        log.debug("Known folder lookup failed for %s", name)
    return None


def resolve_folders(names: Iterable[str]) -> list[Path]:
    """Folder names (Documents, Projects...) or absolute paths -> existing directories."""
    out: list[Path] = []
    home = Path(os.environ.get("USERPROFILE") or Path.home())
    for raw in names:
        raw = str(raw).strip()
        if not raw:
            continue
        p = Path(raw)
        candidates = [p] if p.is_absolute() else [known_folder(raw), home / raw]
        for c in candidates:
            if c is not None and c.is_dir() and c not in out:
                out.append(c)
                break
    return out


def normalize_extensions(exts: Iterable[str]) -> set[str]:
    clean = {str(e).strip().lower().lstrip(".") for e in exts}
    return {e for e in clean if e and e not in BLOCKED_EXTENSIONS and e.isalnum()}


# ---------------------------------------------------------------- results
@dataclass
class FileHit:
    name: str
    path: str
    ext: str
    score: float


@dataclass
class LookupResult:
    status: str                          # "none" | "open" | "picker"
    hits: list[FileHit] = field(default_factory=list)


class FileIndexer:
    def __init__(self, db_path: Path, store=None,
                 folder_resolver: Callable[[Iterable[str]], list[Path]] = resolve_folders):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.store = store
        self._resolve = folder_resolver
        self._rebuild_lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        with closing(self._connect()) as con, con:
            con.execute(
                "CREATE TABLE IF NOT EXISTS files ("
                "path TEXT PRIMARY KEY, name TEXT NOT NULL, stem_normalized TEXT NOT NULL, "
                "ext TEXT NOT NULL, mtime REAL NOT NULL, size INTEGER NOT NULL)"
            )

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(str(self.db_path), timeout=10)

    def _setting(self, key: str, default):
        return self.store.get(key, default) if self.store is not None else default

    # ---------------- building ----------------
    def rebuild(self) -> int:
        """Re-scan all folders. Returns the number of indexed files."""
        if not self._rebuild_lock.acquire(blocking=False):
            return self.count()          # a rebuild is already running
        try:
            folders = self._resolve(self._setting("index_folders", []))
            exts = normalize_extensions(self._setting("index_extensions", []))
            rows: list[tuple] = []
            for folder in folders:
                self._scan(folder, exts, rows, 0)
                if len(rows) >= MAX_FILES:
                    break
            with closing(self._connect()) as con, con:
                con.execute("DELETE FROM files")
                con.executemany("INSERT OR REPLACE INTO files VALUES (?,?,?,?,?,?)", rows)
            log.info("File index rebuilt: %d files in %d folders", len(rows), len(folders))
            return len(rows)
        finally:
            self._rebuild_lock.release()

    def _scan(self, folder: Path, exts: set[str], rows: list[tuple], depth: int) -> None:
        if depth > MAX_DEPTH or len(rows) >= MAX_FILES:
            return
        try:
            it = os.scandir(folder)
        except OSError:
            return
        with it:
            for entry in it:
                try:
                    attrs = getattr(entry.stat(follow_symlinks=False), "st_file_attributes", 0)
                    if attrs & (FILE_ATTRIBUTE_HIDDEN | FILE_ATTRIBUTE_SYSTEM):
                        continue
                    if entry.is_dir(follow_symlinks=False):
                        if entry.name.lower() not in SKIP_DIR_NAMES and not entry.is_symlink():
                            self._scan(Path(entry.path), exts, rows, depth + 1)
                    elif entry.is_file():
                        stem, dot, ext = entry.name.rpartition(".")
                        ext = ext.lower()
                        if not dot or ext not in exts:
                            continue
                        norm = normalize(stem)
                        if not norm:
                            continue
                        st = entry.stat()
                        rows.append((entry.path, entry.name, norm, ext, st.st_mtime, st.st_size))
                except OSError:
                    continue

    def rebuild_async(self, on_done: Optional[Callable[[], None]] = None) -> None:
        def work():
            try:
                self.rebuild()
            except Exception:
                log.exception("File index rebuild failed")
            if on_done:
                on_done()
        threading.Thread(target=work, name="file-index", daemon=True).start()

    def start_periodic(self, on_done: Optional[Callable[[], None]] = None) -> None:
        """Rebuild now (in the background) and then every index_refresh_minutes."""
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()

        def loop():
            while not self._stop.is_set():
                try:
                    self.rebuild()
                except Exception:
                    log.exception("File index rebuild failed")
                if on_done:
                    on_done()
                minutes = float(self._setting("index_refresh_minutes", 30))
                if self._stop.wait(minutes * 60):
                    return
        self._thread = threading.Thread(target=loop, name="file-index-loop", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    # ---------------- reading ----------------
    def count(self) -> int:
        with closing(self._connect()) as con:
            return con.execute("SELECT COUNT(*) FROM files").fetchone()[0]

    def search(self, query: str, limit: int = 50) -> list[FileHit]:
        """All query tokens must appear in the file name (AND); ranked with rapidfuzz."""
        q = normalize(query)
        tokens = q.split()
        if not tokens:
            return []
        where = " AND ".join("stem_normalized LIKE ?" for _ in tokens)
        params = [f"%{t}%" for t in tokens]
        with closing(self._connect()) as con:
            rows = con.execute(
                f"SELECT name, path, ext, stem_normalized, mtime FROM files WHERE {where} LIMIT 1000",
                params,
            ).fetchall()
        scored = []
        for name, path, ext, stem, mtime in rows:
            # token_set_ratio alone is 100 for every superset name, so average it with
            # token_sort_ratio: an exact name beats "name plus extra words".
            score = (fuzz.token_set_ratio(q, stem) + fuzz.token_sort_ratio(q, stem)) / 2
            scored.append((score, mtime, FileHit(name, path, ext, score)))
        scored.sort(key=lambda t: (-t[0], -t[1]))
        return [h for _, _, h in scored[:limit]]

    def lookup(self, query: str) -> LookupResult:
        hits = self.search(query)
        return decide(hits, float(self._setting("file_open_threshold", 85)),
                      float(self._setting("file_open_gap", 10)))

    def words(self, limit: int = 1500) -> list[str]:
        """Most common plain words in file names (for the speech grammar)."""
        counter: Counter = Counter()
        with closing(self._connect()) as con:
            for (stem,) in con.execute("SELECT stem_normalized FROM files"):
                for tok in stem.split():
                    if tok.isalpha() and tok.isascii() and len(tok) >= 2:
                        counter[tok] += 1
        return [w for w, _ in counter.most_common(limit)]


def decide(hits: list[FileHit], threshold: float = 85, gap: float = 10) -> LookupResult:
    """Open directly when the match is clear, otherwise show a numbered picker (max 9)."""
    if not hits:
        return LookupResult("none")
    if len(hits) == 1:
        return LookupResult("open", hits[:1])
    if hits[0].score >= threshold and hits[0].score - hits[1].score >= gap:
        return LookupResult("open", hits[:1])
    return LookupResult("picker", hits[:MAX_PICKER])
