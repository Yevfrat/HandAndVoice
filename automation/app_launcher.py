"""Launch an app, or focus it if it is already running."""
from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path

import psutil

from automation import window_manager
from core.command import Result
from services.application_registry import UWP_ID_RE, AppEntry, ApplicationRegistry

log = logging.getLogger(__name__)

LAUNCHABLE_SUFFIXES = {".exe", ".lnk", ".cmd", ".bat", ".com"}


class AppLauncher:
    def __init__(self, registry: ApplicationRegistry, usage=None):
        self.registry = registry
        self.usage = usage

    def launch(self, name: str) -> Result:
        entry = self.registry.get(name)
        if entry is None:
            return Result.error(f"App not found: {name}")
        if not entry.target:
            return Result.error(f"No target set for {entry.name}. Set it on the Applications page.")
        try:
            result = self._launch_entry(entry)
        except Exception as exc:
            log.exception("Launching %s failed", entry.name)
            return Result.error(f"Could not open {entry.name}: {exc}")
        if result.ok and self.usage:
            self.usage.record(entry.name)
        return result

    def _launch_entry(self, entry: AppEntry) -> Result:
        kind, target = entry.kind, entry.target
        if kind == "uwp" or (kind == "alias" and UWP_ID_RE.match(target)):
            if not UWP_ID_RE.match(target):
                return Result.error(f"Invalid Store app id for {entry.name}")
            subprocess.Popen(["explorer.exe", f"shell:AppsFolder\\{target}"])
            return Result.success(f"Opening {entry.name}")
        if kind == "url":
            if not target.lower().startswith("https://"):
                return Result.error("Only https:// addresses can be opened")
            os.startfile(target)
            return Result.success(f"Opening {entry.name}")

        path = Path(target)
        if path.suffix.lower() not in LAUNCHABLE_SUFFIXES:
            return Result.error(f"{entry.name}: target is not a program ({path.suffix or 'no extension'})")
        if not path.is_file():
            return Result.error(f"{entry.name}: file not found: {path}")

        if path.suffix.lower() == ".exe" and self._focus_running(path):
            return Result.success(f"Switched to {entry.name}")
        os.startfile(str(path), "open", entry.arguments or "", str(path.parent))
        return Result.success(f"Opening {entry.name}")

    @staticmethod
    def _focus_running(path: Path) -> bool:
        exe = path.name.lower()
        me = os.getpid()
        pids = set()
        for p in psutil.process_iter(["name"]):
            try:
                if p.info["name"] and p.info["name"].lower() == exe and p.pid != me:
                    pids.add(p.pid)
            except psutil.Error:
                continue
        if not pids:
            return False
        windows = window_manager.windows_for_pids(pids)
        for hwnd, title, _pid in windows:
            if window_manager.bring_to_front(hwnd):
                log.info("Focused existing window of %s", exe)
                return True
        return False
