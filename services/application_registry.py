"""Searchable registry of launchable apps (spec section 6).

Sources, in order: Start Menu shortcuts, registry uninstall keys, UWP/Store apps
(Get-StartApps). User aliases from app_aliases.json override and extend them.
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from rapidfuzz import fuzz, process

from voice.command_parser import FuzzyResult
from voice.text_normalizer import normalize

log = logging.getLogger(__name__)

# Store apps ("Package_hash!App") and shell apps like "Microsoft.Windows.Explorer".
# Path-like ids ({GUID}\...), URLs and bare words are rejected.
UWP_ID_RE = re.compile(
    r"^(?:[A-Za-z0-9_.\-]+![A-Za-z0-9_.\-]+|[A-Za-z0-9_\-]+(?:\.[A-Za-z0-9_\-]+)+)$"
)
SKIP_NAME_PARTS = ("uninstall", "readme", "release notes", "license", "documentation")
MAX_PICKER = 9


@dataclass
class AppEntry:
    name: str
    aliases: list[str] = field(default_factory=list)
    kind: str = "exe"            # lnk | exe | uwp | url | alias
    target: str = ""
    arguments: str = ""
    source: str = "alias"        # startmenu | registry | uwp | alias


# ---------------------------------------------------------------- discovery
def _resolve_lnk(path: Path) -> tuple[str, str]:
    import win32com.client

    shell = win32com.client.Dispatch("WScript.Shell")
    sc = shell.CreateShortcut(str(path))
    return sc.TargetPath or "", sc.Arguments or ""


def _skip_name(name: str) -> bool:
    low = name.lower()
    return any(part in low for part in SKIP_NAME_PARTS)


def start_menu_dirs() -> list[Path]:
    dirs = []
    for var in ("ProgramData", "AppData"):
        base = os.environ.get(var)
        if base:
            dirs.append(Path(base) / "Microsoft" / "Windows" / "Start Menu" / "Programs")
    return dirs


def scan_start_menu(dirs: Optional[list[Path]] = None,
                    resolver: Optional[Callable[[Path], tuple[str, str]]] = None) -> list[AppEntry]:
    dirs = dirs if dirs is not None else start_menu_dirs()
    com_started = False
    if resolver is None:
        import pythoncom

        pythoncom.CoInitialize()
        com_started = True
        resolver = _resolve_lnk
    entries: list[AppEntry] = []
    try:
        for d in dirs:
            if not Path(d).is_dir():
                continue
            for lnk in Path(d).rglob("*.lnk"):
                name = lnk.stem
                if _skip_name(name):
                    continue
                try:
                    target, args = resolver(lnk)
                except Exception:
                    log.debug("Could not resolve shortcut %s", lnk.name)
                    continue
                if not target.lower().endswith(".exe") or not os.path.isfile(target):
                    continue
                entries.append(AppEntry(name, [], "lnk", target, args, "startmenu"))
    finally:
        if com_started:
            import pythoncom

            pythoncom.CoUninitialize()
    return entries


def _icon_to_exe(icon: str) -> str:
    icon = os.path.expandvars(icon.strip())
    icon = re.sub(r",\s*-?\d+$", "", icon).strip().strip('"')
    return icon


def scan_uninstall_keys() -> list[AppEntry]:
    import winreg

    roots = [
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
    ]
    entries: list[AppEntry] = []

    def value(key, name):
        try:
            return winreg.QueryValueEx(key, name)[0]
        except OSError:
            return None

    for hive, path in roots:
        try:
            root = winreg.OpenKey(hive, path)
        except OSError:
            continue
        with root:
            i = 0
            while True:
                try:
                    sub = winreg.EnumKey(root, i)
                except OSError:
                    break
                i += 1
                try:
                    with winreg.OpenKey(root, sub) as k:
                        name = value(k, "DisplayName")
                        icon = value(k, "DisplayIcon")
                        system = value(k, "SystemComponent")
                except OSError:
                    continue
                if not name or not isinstance(name, str) or system == 1 or _skip_name(name):
                    continue
                exe = _icon_to_exe(icon) if isinstance(icon, str) else ""
                if exe.lower().endswith(".exe") and os.path.isfile(exe):
                    entries.append(AppEntry(name.strip(), [], "exe", exe, "", "registry"))
    return entries


def parse_start_apps(output: str) -> list[AppEntry]:
    """Parse the JSON from `Get-StartApps | ConvertTo-Json` (object or list)."""
    output = output.strip()
    if not output:
        return []
    data = json.loads(output)
    if isinstance(data, dict):
        data = [data]
    entries = []
    for item in data:
        name = str(item.get("Name", "")).strip()
        app_id = str(item.get("AppID", "")).strip()
        if name and UWP_ID_RE.match(app_id):  # only real Store apps have "PackageFamily!App"
            entries.append(AppEntry(name, [], "uwp", app_id, "", "uwp"))
    return entries


def scan_uwp(run=subprocess.run) -> list[AppEntry]:
    cmd = ["powershell", "-NoProfile", "-NonInteractive", "-Command",
           "[Console]::OutputEncoding=[Text.Encoding]::UTF8; Get-StartApps | ConvertTo-Json"]
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        proc = run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                   timeout=30, creationflags=flags)
        return parse_start_apps(proc.stdout)
    except Exception as exc:
        log.warning("UWP app discovery failed: %s", exc)
        return []


# ---------------------------------------------------------------- merging
def target_is_usable(kind: str, target: str) -> bool:
    if not target:
        return False
    if kind == "uwp":
        return bool(UWP_ID_RE.match(target))
    if kind == "url":
        return target.lower().startswith("https://")
    return os.path.isabs(target) and os.path.isfile(target)


def merge_entries(discovered: list[AppEntry], user_aliases: list[dict]) -> list[AppEntry]:
    """Apply user aliases on top of discovered apps.

    An alias entry with a usable target wins. Otherwise it adopts the target of a
    discovered app whose name equals its name or any of its aliases, so "VS Code"
    with alias "Visual Studio Code" finds the Start Menu entry automatically.
    """
    result: dict[str, AppEntry] = {}
    for e in discovered:
        result.setdefault(normalize(e.name), e)

    for u in user_aliases:
        name = u["name"]
        aliases = list(u.get("aliases", []))
        kind, target, args = u.get("kind", "exe"), u.get("target", ""), u.get("arguments", "")
        disc_key = next((normalize(n) for n in [name] + aliases if normalize(n) in result), None)
        disc = result.get(disc_key) if disc_key else None

        if target_is_usable(kind, target):
            entry = AppEntry(name, aliases, kind, target, args, "alias")
        elif disc is not None:
            entry = AppEntry(name, aliases, disc.kind, disc.target, args or disc.arguments,
                             f"alias+{disc.source}")
        else:
            found = shutil.which(target) if target and kind in ("exe", "lnk") else None
            entry = AppEntry(name, aliases, kind, found or target, args, "alias")

        if disc_key is not None and disc_key != normalize(name):
            result.pop(disc_key, None)      # the discovered duplicate is absorbed
        result[normalize(name)] = entry
    return sorted(result.values(), key=lambda e: e.name.lower())


# ---------------------------------------------------------------- registry
class ApplicationRegistry:
    def __init__(self, alias_manager, usage=None, store=None,
                 scanners: Optional[list[Callable[[], list[AppEntry]]]] = None):
        self.alias_manager = alias_manager
        self.usage = usage
        self.store = store
        self.scanners = scanners if scanners is not None else [scan_start_menu, scan_uninstall_keys, scan_uwp]
        self._lock = threading.RLock()
        self._entries: list[AppEntry] = []
        self._exact: dict[str, AppEntry] = {}
        self._choices: list[tuple[str, AppEntry]] = []
        self._refreshing = False

    # ----- settings -----
    def _setting(self, key: str, default):
        return self.store.get(key, default) if self.store is not None else default

    # ----- data -----
    @property
    def entries(self) -> list[AppEntry]:
        with self._lock:
            return list(self._entries)

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)

    def get(self, name: str) -> Optional[AppEntry]:
        key = normalize(name)
        with self._lock:
            for e in self._entries:
                if normalize(e.name) == key:
                    return e
        return None

    def refresh(self) -> None:
        discovered: list[AppEntry] = []
        for scan in self.scanners:
            try:
                discovered += scan()
            except Exception:
                log.exception("App scanner %s failed", getattr(scan, "__name__", scan))
        entries = merge_entries(discovered, self.alias_manager.load_app_aliases())
        exact: dict[str, AppEntry] = {}
        for e in entries:                       # names first, so a name beats another entry's alias
            exact.setdefault(normalize(e.name), e)
        for e in entries:
            for a in e.aliases:
                exact.setdefault(normalize(a), e)
        choices = [(k, e) for k, e in exact.items()]
        with self._lock:
            self._entries, self._exact, self._choices = entries, exact, choices
        log.info("Application registry: %d apps", len(entries))

    def refresh_async(self, on_done: Optional[Callable[[], None]] = None) -> None:
        with self._lock:
            if self._refreshing:
                return
            self._refreshing = True

        def work():
            try:
                self.refresh()
            finally:
                with self._lock:
                    self._refreshing = False
                if on_done:
                    on_done()

        threading.Thread(target=work, name="app-registry", daemon=True).start()

    # ----- matching -----
    def find_exact(self, text: str) -> Optional[AppEntry]:
        with self._lock:
            return self._exact.get(normalize(text))

    def find_fuzzy(self, text: str) -> FuzzyResult:
        threshold = float(self._setting("fuzzy_threshold", 85))
        gap = float(self._setting("picker_gap", 5))
        query = normalize(text)
        with self._lock:
            choices = list(self._choices)
        if not query or not choices:
            return FuzzyResult("none")
        matches = process.extract(query, [c[0] for c in choices], scorer=fuzz.token_set_ratio,
                                  score_cutoff=threshold, limit=None)
        best: dict[int, tuple[float, AppEntry]] = {}
        for _, score, idx in matches:
            entry = choices[idx][1]
            if id(entry) not in best or score > best[id(entry)][0]:
                best[id(entry)] = (score, entry)
        ranked = sorted(best.values(), key=lambda t: -t[0])
        if not ranked:
            return FuzzyResult("none")
        if len(ranked) == 1 or ranked[0][0] - ranked[1][0] >= gap:
            return FuzzyResult("single", [ranked[0][1]])
        # close call: never guess. Most used apps first, then best score.
        def usage(e: AppEntry) -> int:
            return self.usage.count(e.name) if self.usage else 0

        picker = sorted(ranked, key=lambda t: (-usage(t[1]), -t[0]))[:MAX_PICKER]
        return FuzzyResult("ambiguous", [e for _, e in picker])
