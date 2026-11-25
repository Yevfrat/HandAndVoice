import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from services.alias_manager import AliasManager
from services.application_registry import (
    AppEntry, ApplicationRegistry, merge_entries, parse_start_apps, scan_start_menu,
    scan_uninstall_keys, target_is_usable,
)
from services.config_store import ConfigStore
from services.usage_stats import UsageStats


@pytest.fixture
def store(tmp_path):
    s = ConfigStore(data_dir=tmp_path / "data")
    s.ensure_initialized()
    return s


def make_registry(store, discovered, usage=None, aliases=None):
    am = AliasManager(store)
    if aliases is not None:
        am.save_app_aliases(aliases)
    return ApplicationRegistry(am, usage, store, scanners=[lambda: list(discovered)])


def disc(name, target="C:\\x\\a.exe", kind="lnk", source="startmenu"):
    return AppEntry(name, [], kind, target, "", source)


# ---------------- discovery ----------------
def test_start_menu_scan_resolves_shortcuts(tmp_path):
    exe = tmp_path / "Foo.exe"
    exe.write_bytes(b"")
    menu = tmp_path / "Programs" / "Sub"
    menu.mkdir(parents=True)
    (menu / "Foo App.lnk").write_bytes(b"")
    (menu / "Uninstall Foo.lnk").write_bytes(b"")
    (menu / "Doc.lnk").write_bytes(b"")
    fake = {"Foo App.lnk": (str(exe), "--x"), "Uninstall Foo.lnk": (str(exe), ""),
            "Doc.lnk": (str(tmp_path / "readme.txt"), "")}
    entries = scan_start_menu([tmp_path / "Programs"], resolver=lambda p: fake[p.name])
    assert [(e.name, e.kind, e.target, e.arguments, e.source) for e in entries] == [
        ("Foo App", "lnk", str(exe), "--x", "startmenu")]


def test_parse_get_startapps_json_list_and_single():
    one = json.dumps({"Name": "Calculator", "AppID": "Microsoft.WindowsCalculator_8wekyb3d8bbwe!App"})
    many = json.dumps([
        {"Name": "Calculator", "AppID": "Microsoft.WindowsCalculator_8wekyb3d8bbwe!App"},
        {"Name": "Notepad", "AppID": "{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\\notepad.exe"},  # classic: skipped
        {"Name": "", "AppID": "X!Y"},
        {"Name": "File Explorer", "AppID": "Microsoft.Windows.Explorer"},
        {"Name": "Unity Release Notes", "AppID": "http://unity3d.com/whatsnew.html"},
    ])
    assert [e.name for e in parse_start_apps(one)] == ["Calculator"]
    got = parse_start_apps(many)
    assert [(e.name, e.kind) for e in got] == [("Calculator", "uwp"), ("File Explorer", "uwp")]
    assert parse_start_apps("") == []


@pytest.mark.skipif(sys.platform != "win32", reason="Windows registry")
def test_registry_scan_returns_existing_executables():
    for e in scan_uninstall_keys():
        assert e.kind == "exe" and Path(e.target).is_file()


# ---------------- merging ----------------
def test_alias_adopts_discovered_target_through_its_aliases():
    discovered = [disc("Visual Studio Code", "C:\\vs\\Code.exe")]
    user = [{"name": "VS Code", "aliases": ["Visual Studio Code", "Code Editor"],
             "target": "", "kind": "exe", "arguments": ""}]
    merged = merge_entries(discovered, user)
    assert [e.name for e in merged] == ["VS Code"]            # no duplicate entry
    assert merged[0].target == "C:\\vs\\Code.exe"
    assert "Code Editor" in merged[0].aliases


def test_usable_alias_target_wins(tmp_path):
    exe = tmp_path / "My.exe"
    exe.write_bytes(b"")
    merged = merge_entries([disc("Telegram")], [
        {"name": "Telegram", "aliases": [], "target": str(exe), "kind": "exe", "arguments": ""}])
    assert merged[0].target == str(exe) and merged[0].source == "alias"


def test_alias_lists_are_independent():
    user = [
        {"name": "ChatGPT", "aliases": ["ChatGPT Desktop"], "target": "", "kind": "exe", "arguments": ""},
        {"name": "ChatGPT Classic", "aliases": [], "target": "https://chatgpt.com", "kind": "url",
         "arguments": ""},
    ]
    merged = {e.name: e for e in merge_entries([], user)}
    assert merged["ChatGPT"].aliases == ["ChatGPT Desktop"]
    assert merged["ChatGPT Classic"].aliases == []
    assert merged["ChatGPT Classic"].kind == "url"


def test_target_is_usable():
    assert target_is_usable("uwp", "Microsoft.WindowsCalculator_8wekyb3d8bbwe!App")
    assert not target_is_usable("uwp", "calc")
    assert target_is_usable("url", "https://x.org") and not target_is_usable("url", "http://x.org")
    assert not target_is_usable("exe", "code") and not target_is_usable("exe", "")


# ---------------- matching ----------------
def test_exact_match_by_name_and_alias_case_insensitive(store):
    reg = make_registry(store, [disc("Calculator")],
                        aliases=[{"name": "Calc Pro", "aliases": ["Number Cruncher"], "target": "",
                                  "kind": "exe", "arguments": ""}])
    reg.refresh()
    assert reg.find_exact("CALCULATOR").name == "Calculator"
    assert reg.find_exact("number cruncher").name == "Calc Pro"
    assert reg.find_exact("nothing") is None


def test_fuzzy_single_match_launches(store):
    reg = make_registry(store, [disc("Calculator"), disc("Notepad")], aliases=[])
    reg.refresh()
    res = reg.find_fuzzy("calculater")
    assert res.status == "single" and res.entries[0].name == "Calculator"


def test_fuzzy_close_call_gives_picker_sorted_by_usage(store, tmp_path):
    usage = UsageStats(tmp_path / "u.db")
    usage.record("Visual Studio Code Insiders")
    usage.record("Visual Studio Code Insiders")
    usage.record("Visual Studio Code")
    reg = make_registry(store, [disc("Visual Studio Code"), disc("Visual Studio Code Insiders"),
                                disc("Notepad")], usage=usage, aliases=[])
    reg.refresh()
    res = reg.find_fuzzy("visual studio code")
    assert res.status in ("ambiguous", "single")
    res = reg.find_fuzzy("studio code")                       # subset of both names -> both 100
    assert res.status == "ambiguous"
    assert [e.name for e in res.entries] == ["Visual Studio Code Insiders", "Visual Studio Code"]


def test_no_fuzzy_match(store):
    reg = make_registry(store, [disc("Calculator")], aliases=[])
    reg.refresh()
    assert reg.find_fuzzy("banana").status == "none"


def test_scanner_failure_does_not_break_refresh(store):
    def boom():
        raise RuntimeError("scan failed")
    am = AliasManager(store)
    reg = ApplicationRegistry(am, None, store, scanners=[boom, lambda: [disc("Notepad")]])
    reg.refresh()
    assert [e.name for e in reg.entries if e.name == "Notepad"] == ["Notepad"]


def test_refresh_async_calls_back(store):
    import threading
    reg = make_registry(store, [disc("Notepad")], aliases=[])
    done = threading.Event()
    reg.refresh_async(done.set)
    assert done.wait(5)
    assert len(reg) == 1


# ---------------- usage + alias manager ----------------
def test_usage_stats(tmp_path):
    u = UsageStats(tmp_path / "u.db")
    u.record("Claude"); u.record("claude"); u.record("Notepad")
    assert u.count("CLAUDE") == 2
    assert u.top(1) == [("claude", 2)]


def test_alias_manager_roundtrip(store):
    am = AliasManager(store)
    am.upsert_app_alias({"name": "Foo", "aliases": ["Bar", "bar", "Foo"], "kind": "weird", "target": " x "})
    entry = next(e for e in am.load_app_aliases() if e["name"] == "Foo")
    assert entry["aliases"] == ["Bar"] and entry["kind"] == "exe" and entry["target"] == "x"
    am.upsert_app_alias({"name": "Foo2", "aliases": []}, replace_name="Foo")
    names = [e["name"] for e in am.load_app_aliases()]
    assert "Foo2" in names and "Foo" not in names
    am.delete_app_alias("foo2")
    assert "Foo2" not in [e["name"] for e in am.load_app_aliases()]
    with pytest.raises(ValueError):
        am.upsert_app_alias({"name": "  "})
