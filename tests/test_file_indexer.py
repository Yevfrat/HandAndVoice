import ctypes
import os
import sys
from pathlib import Path

import pytest

from automation.file_manager import FileManager
from services.config_store import ConfigStore
from services.file_indexer import (
    BLOCKED_EXTENSIONS, FileHit, FileIndexer, decide, normalize_extensions, resolve_folders,
)


@pytest.fixture
def tree(tmp_path):
    root = tmp_path / "Docs"
    (root / "sub").mkdir(parents=True)
    (root / "node_modules").mkdir()
    (root / ".git").mkdir()
    (root / "venv").mkdir()
    (root / "__pycache__").mkdir()
    files = [
        "Economics.pdf", "Economics notes final.docx", "Economics-Lecture_01.pdf",
        "sub/Budget 2024.xlsx", "sub/chapter 1 intro.txt", "sub/Photo.JPG",
        "setup.exe", "run.bat", "script.ps1", "installer.msi", "link.lnk", "readme.unknown",
        "node_modules/hidden_pkg.txt", ".git/config.txt", "venv/lib.py", "__pycache__/x.py",
    ]
    for f in files:
        (root / f).write_text("x")
    return root


def make(tmp_path, tree, exts=None):
    s = ConfigStore(data_dir=tmp_path / "data"); s.ensure_initialized()
    s.update({"index_extensions": exts or s.get("index_extensions")})
    return FileIndexer(tmp_path / "idx.db", s, folder_resolver=lambda names: [tree])


def names(hits):
    return [h.name for h in hits]


def test_index_contains_only_allowed_files(tmp_path, tree):
    idx = make(tmp_path, tree)
    n = idx.rebuild()
    got = {h.name for h in idx.search("e")} | {h.name for h in idx.search("a")} | {h.name for h in idx.search("o")}
    assert n == idx.count()
    assert "Economics.pdf" in got and "Photo.JPG" in got
    for bad in ("setup.exe", "run.bat", "script.ps1", "installer.msi", "link.lnk", "readme.unknown",
                "hidden_pkg.txt", "config.txt", "lib.py", "x.py"):
        assert bad not in got, bad
    assert n == 6          # 3 economics + budget + chapter + photo


def test_blocked_extensions_cannot_be_enabled(tmp_path, tree):
    idx = make(tmp_path, tree, exts=["pdf", "exe", ".BAT", "ps1", "msi"])
    idx.rebuild()
    assert {h.ext for h in idx.search("setup")} == set()
    assert normalize_extensions(["PDF", ".Docx", "exe", "", "a b"]) == {"pdf", "docx"}
    assert {"exe", "bat", "cmd", "ps1", "msi"} <= BLOCKED_EXTENSIONS


def test_search_requires_all_tokens_and_normalizes(tmp_path, tree):
    idx = make(tmp_path, tree); idx.rebuild()
    assert names(idx.search("budget")) == ["Budget 2024.xlsx"]
    assert names(idx.search("Economics Lecture")) == ["Economics-Lecture_01.pdf"]   # - and _ ignored
    assert idx.search("economics budget") == []                                     # AND
    assert names(idx.search("chapter one")) == ["chapter 1 intro.txt"]             # number words
    assert idx.search("") == []


def test_exact_name_beats_longer_names(tmp_path, tree):
    idx = make(tmp_path, tree); idx.rebuild()
    hits = idx.search("economics")
    assert hits[0].name == "Economics.pdf"
    assert len(hits) == 3
    look = idx.lookup("economics")
    assert look.status == "open" and look.hits[0].name == "Economics.pdf"


def test_decide_rules():
    h = lambda s: FileHit("a", "p", "pdf", s)
    assert decide([]).status == "none"
    assert decide([h(40)]).status == "open"                       # only one candidate
    assert decide([h(95), h(80)]).status == "open"                # clear winner
    assert decide([h(95), h(90)]).status == "picker"              # too close
    assert decide([h(80), h(60)]).status == "picker"              # winner below threshold
    many = decide([h(90)] * 15)
    assert many.status == "picker" and len(many.hits) == 9


def test_rebuild_picks_up_changes(tmp_path, tree):
    idx = make(tmp_path, tree); idx.rebuild()
    (tree / "newfile.txt").write_text("x")
    assert idx.search("newfile") == []
    idx.rebuild()
    assert names(idx.search("newfile")) == ["newfile.txt"]


@pytest.mark.skipif(sys.platform != "win32", reason="hidden attribute is Windows only")
def test_hidden_files_and_folders_skipped(tmp_path, tree):
    (tree / "secret.txt").write_text("x")
    (tree / "hidden_dir").mkdir()
    (tree / "hidden_dir" / "inside.txt").write_text("x")
    for p in (tree / "secret.txt", tree / "hidden_dir"):
        ctypes.windll.kernel32.SetFileAttributesW(str(p), 0x2)
    idx = make(tmp_path, tree); idx.rebuild()
    assert idx.search("secret") == [] and idx.search("inside") == []


def test_words_for_grammar(tmp_path, tree):
    idx = make(tmp_path, tree); idx.rebuild()
    words = idx.words()
    assert words[0] == "economics"          # most common
    assert "budget" in words and "1" not in words and "2024" not in words


def test_resolve_folders(tmp_path):
    (tmp_path / "Projects").mkdir()
    got = resolve_folders([str(tmp_path / "Projects"), str(tmp_path / "missing"), ""])
    assert got == [tmp_path / "Projects"]
    docs = resolve_folders(["Documents"])           # real known folder on this machine, or nothing
    assert all(p.is_dir() for p in docs)


def test_file_manager_opens_and_refuses(tmp_path, tree):
    idx = make(tmp_path, tree); idx.rebuild()
    opened = []
    fm = FileManager(idx, opener=opened.append)
    ok = fm.open(str(tree / "Economics.pdf"))
    assert ok.ok and opened == [str(tree / "Economics.pdf")]
    for bad in ("setup.exe", "run.bat", "script.ps1", "installer.msi"):
        res = fm.open(str(tree / bad))
        assert not res.ok and "Refusing" in res.message
    assert len(opened) == 1
    gone = fm.open(str(tree / "deleted.pdf"))
    assert not gone.ok and "no longer exists" in gone.message


def test_log_never_contains_paths_or_contents(tmp_path, tree, caplog):
    import logging
    idx = make(tmp_path, tree); idx.rebuild()
    (tree / "Economics.pdf").write_text("TOP SECRET CONTENT")
    with caplog.at_level(logging.INFO):
        FileManager(idx, opener=lambda p: None).open(str(tree / "Economics.pdf"))
    text = caplog.text
    assert "Economics.pdf" in text and "TOP SECRET" not in text and str(tree) not in text
