"""Sleep, lock, shut down, restart, screenshot and Close All Apps.

Nothing here runs without the user's confirmation or a cancellable countdown; the
dispatcher enforces that. Processes are never killed: windows get WM_CLOSE.
"""
from __future__ import annotations

import logging
import subprocess
import threading
from typing import Callable, Optional

from core.command import Command, CommandType, Result

log = logging.getLogger(__name__)

SHUTDOWN_DELAY_SECONDS = 60
CLOSE_REPORT_DELAY_SECONDS = 3.0
MAX_NAMES_SHOWN = 8
ELEVATED_MESSAGE = "Target app runs as administrator; keyboard input is blocked by Windows"


def _no_window():
    return getattr(subprocess, "CREATE_NO_WINDOW", 0)


class SystemController:
    def __init__(self, keyboard=None, wm=None, run=subprocess.run, popen=subprocess.Popen,
                 lock_fn: Optional[Callable[[], bool]] = None,
                 notify: Optional[Callable[[str, str], None]] = None,
                 timer_factory=threading.Timer):
        self._keyboard = keyboard
        self._wm = wm
        self._run = run
        self._popen = popen
        self._lock_fn = lock_fn
        self._notify = notify or (lambda kind, text: None)
        self._timer = timer_factory

    @property
    def keyboard(self):
        if self._keyboard is None:
            from automation import keyboard_controller

            self._keyboard = keyboard_controller
        return self._keyboard

    @property
    def wm(self):
        if self._wm is None:
            from automation import window_manager

            self._wm = window_manager
        return self._wm

    # ------------------------------------------------------------------ simple actions
    def screenshot(self, cmd: Optional[Command] = None) -> Result:
        info = self.wm.foreground_info()
        if info is not None and info.elevated:
            return Result.error(ELEVATED_MESSAGE)
        try:
            self.keyboard.press("win+printscreen")
        except Exception as exc:
            return Result.error(str(exc))
        return Result.success("Screenshot taken (saved by Windows in Pictures\\Screenshots)")

    def lock(self, cmd: Optional[Command] = None) -> Result:
        try:
            if self._lock_fn is not None:
                ok = self._lock_fn()
            else:
                import ctypes

                ok = bool(ctypes.windll.user32.LockWorkStation())
        except Exception as exc:
            return Result.error(f"Could not lock the screen: {exc}")
        return Result.success("Screen locked") if ok else Result.error("Windows refused to lock the screen")

    def sleep(self, cmd: Optional[Command] = None) -> Result:
        """Called by the countdown after it ran out. The app stops with the PC; restart it afterwards."""
        try:
            self._popen(["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"],
                        creationflags=_no_window())
        except OSError as exc:
            return Result.error(f"Could not start sleep: {exc}")
        return Result.success("Going to sleep")

    def shutdown(self, cmd: Optional[Command] = None) -> Result:
        return self._shutdown("/s", "Shutting down")

    def restart(self, cmd: Optional[Command] = None) -> Result:
        return self._shutdown("/r", "Restarting")

    def _shutdown(self, flag: str, word: str) -> Result:
        try:
            proc = self._run(["shutdown", flag, "/t", str(SHUTDOWN_DELAY_SECONDS)],
                             capture_output=True, text=True, creationflags=_no_window())
        except OSError as exc:
            return Result.error(f"Could not start: {exc}")
        if proc.returncode != 0:
            return Result.error(f"Windows refused: {(proc.stderr or proc.stdout or '').strip()[:120]}")
        return Result.success(f"{word} in {SHUTDOWN_DELAY_SECONDS} s - say 'Hand, cancel' to abort")

    def abort_shutdown(self) -> Result:
        try:
            proc = self._run(["shutdown", "/a"], capture_output=True, text=True,
                             creationflags=_no_window())
        except OSError as exc:
            return Result.error(f"Could not cancel: {exc}")
        if proc.returncode != 0:
            return Result.error("No shutdown was in progress")
        return Result.success("Shutdown cancelled")

    # ------------------------------------------------------------------ Close All Apps
    @staticmethod
    def _label(info) -> str:
        title = (info.title or info.process or "window").strip()
        return title[:40]

    def prepare_close_all(self, cmd: Command) -> tuple[bool, str]:
        """List the windows that will be asked to close, before the confirmation prompt."""
        windows = self.wm.closable_windows()
        targets = [w for w in windows if not w.elevated]
        blocked = [w for w in windows if w.elevated]
        if not targets:
            return False, "There are no windows to close"
        cmd.target = ",".join(str(w.hwnd) for w in targets)
        shown = [self._label(w) for w in targets[:MAX_NAMES_SHOWN]]
        more = len(targets) - len(shown)
        detail = f"{len(targets)} window(s) will be asked to close: " + ", ".join(shown)
        if more > 0:
            detail += f" and {more} more"
        if blocked:
            detail += f"\n{len(blocked)} administrator window(s) will be skipped"
        return True, detail

    def close_all(self, cmd: Command) -> Result:
        """WM_CLOSE to the windows listed at request time. Apps may ask "Save changes?"."""
        hwnds = [int(x) for x in (cmd.target or "").split(",") if x.strip().isdigit()]
        sent: list[tuple[int, str]] = []
        for hwnd in hwnds:
            info = self.wm.describe(hwnd)
            if info is None or info.own or info.protected or info.elevated:
                continue
            try:
                self.wm.post_close(hwnd)
            except Exception:
                log.warning("Could not post WM_CLOSE to a window")
                continue
            sent.append((hwnd, self._label(info)))
        if not sent:
            return Result.error("No windows could be closed")
        timer = self._timer(CLOSE_REPORT_DELAY_SECONDS, self._report_remaining, args=(sent,))
        if hasattr(timer, "daemon"):
            timer.daemon = True
        timer.start()
        return Result.success(f"Asked {len(sent)} window(s) to close")

    def _report_remaining(self, sent: list[tuple[int, str]]) -> None:
        still = [name for hwnd, name in sent if self.wm.is_open(hwnd)]
        if not still:
            self._notify("info", f"All {len(sent)} windows closed")
        else:
            shown = ", ".join(still[:MAX_NAMES_SHOWN])
            self._notify("info", f"Still open (probably asking to save): {shown}")


def register(executors: dict, system: SystemController, media) -> None:
    from automation.media_controller import HANDLED as MEDIA

    for ctype in MEDIA:
        executors[ctype] = media.execute
    executors[CommandType.SCREENSHOT] = system.screenshot
    executors[CommandType.LOCK_SCREEN] = system.lock
    executors[CommandType.CLOSE_ALL_APPS] = system.close_all
