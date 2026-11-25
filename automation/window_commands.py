"""Window, browser and key commands (spec 10.2 / 10.3).

Everything goes through a fixed table; nothing from speech is executed as text.
Keys can be overridden per command with a "keys" field in voice_commands.json.
"""
from __future__ import annotations

import logging
from typing import Callable, Optional

from automation.web_controller import is_browser_process
from core.command import Command, CommandType, Result

log = logging.getLogger(__name__)

SCROLL_NOTCHES = 5
ELEVATED_MESSAGE = "Target app runs as administrator; keyboard input is blocked by Windows"

# command -> keys. Browser commands only work while a browser is in the foreground.
DEFAULT_KEYS: dict[CommandType, str] = {
    CommandType.SWITCH_WINDOW: "alt+tab",
    CommandType.TASK_VIEW: "win+tab",
    CommandType.FULLSCREEN: "f11",
    CommandType.SHOW_DESKTOP: "win+d",
    CommandType.NEXT_WINDOW: "alt+tab",
    CommandType.PREVIOUS_WINDOW: "alt+shift+tab",
    CommandType.NEW_TAB: "ctrl+t",
    CommandType.CLOSE_TAB: "ctrl+w",
    CommandType.NEXT_TAB: "ctrl+tab",
    CommandType.PREVIOUS_TAB: "ctrl+shift+tab",
    CommandType.REFRESH: "f5",
    CommandType.BACK: "alt+left",
    CommandType.FORWARD: "alt+right",
    CommandType.PAGE_UP: "pageup",
    CommandType.PAGE_DOWN: "pagedown",
    CommandType.GO_TO_TOP: "home",
    CommandType.GO_TO_BOTTOM: "end",
}
BROWSER_ONLY = {
    CommandType.NEW_TAB, CommandType.CLOSE_TAB, CommandType.NEXT_TAB, CommandType.PREVIOUS_TAB,
    CommandType.REFRESH, CommandType.BACK, CommandType.FORWARD, CommandType.SCROLL_UP,
    CommandType.SCROLL_DOWN, CommandType.PAGE_UP, CommandType.PAGE_DOWN, CommandType.GO_TO_TOP,
    CommandType.GO_TO_BOTTOM,
}
DONE_MESSAGES = {
    CommandType.SWITCH_WINDOW: "Switched window", CommandType.TASK_VIEW: "Task View",
    CommandType.SHOW_DESKTOP: "Show desktop", CommandType.NEXT_WINDOW: "Next window",
    CommandType.PREVIOUS_WINDOW: "Previous window", CommandType.FULLSCREEN: "Fullscreen toggled (F11)",
    CommandType.NEW_TAB: "New tab", CommandType.CLOSE_TAB: "Tab closed", CommandType.NEXT_TAB: "Next tab",
    CommandType.PREVIOUS_TAB: "Previous tab", CommandType.REFRESH: "Refreshed", CommandType.BACK: "Back",
    CommandType.FORWARD: "Forward", CommandType.SCROLL_UP: "Scrolled up", CommandType.SCROLL_DOWN: "Scrolled down",
    CommandType.PAGE_UP: "Page up", CommandType.PAGE_DOWN: "Page down", CommandType.GO_TO_TOP: "Top of page",
    CommandType.GO_TO_BOTTOM: "Bottom of page",
}
HANDLED = set(DEFAULT_KEYS) | {CommandType.MINIMIZE, CommandType.MAXIMIZE, CommandType.CLOSE_WINDOW,
                               CommandType.SCROLL_UP, CommandType.SCROLL_DOWN}


class WindowCommands:
    def __init__(self, keyboard=None, wm=None, voice_commands: Optional[dict] = None):
        self._keyboard = keyboard
        self._wm = wm
        self.keys = dict(DEFAULT_KEYS)
        for spec in (voice_commands or {}).values():       # user overrides from voice_commands.json
            keys = spec.get("keys")
            try:
                ctype = CommandType(spec.get("type"))
            except ValueError:
                continue
            if keys and ctype in self.keys:
                self.keys[ctype] = str(keys)

    # lazily bound so the module can be imported (and tested) without touching Windows
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

    # ------------------------------------------------------------------ execute
    def execute(self, cmd: Command) -> Result:
        ctype = cmd.type
        info = self.wm.foreground_info()
        if ctype == CommandType.CLOSE_WINDOW:
            return self._close(cmd, info)       # checks its own (remembered) target
        if info is None:
            return Result.error("No active window")
        if ctype in BROWSER_ONLY and not is_browser_process(info.process):
            return Result.error(f"'{cmd.label()}' works only when a browser is in focus")
        if info.elevated:
            return Result.error(ELEVATED_MESSAGE)
        try:
            if ctype == CommandType.MINIMIZE:
                return self._minimize(info)
            if ctype == CommandType.MAXIMIZE:
                return self._maximize(info)
            if ctype in (CommandType.SCROLL_UP, CommandType.SCROLL_DOWN):
                sign = 1 if ctype == CommandType.SCROLL_UP else -1
                for _ in range(SCROLL_NOTCHES):
                    self.keyboard.send_wheel(sign)
                return Result.success(DONE_MESSAGES[ctype])
            keys = self.keys.get(ctype)
            if keys is None:
                return Result.error(f"'{cmd.label()}' is not a window command")
            self.keyboard.press(keys)
            return Result.success(DONE_MESSAGES.get(ctype, cmd.label()))
        except Exception as exc:
            name = type(exc).__name__
            log.warning("Window command %s failed: %s", ctype.value, name)
            return Result.error(str(exc) if name == "InputBlockedError" else f"Could not run '{cmd.label()}'")

    # ------------------------------------------------------------------ window actions
    def _minimize(self, info) -> Result:
        if info.protected:
            return Result.error("The desktop and taskbar cannot be minimized")
        if not self.wm.can_minimize(info.hwnd):
            return Result.error("This window cannot be minimized")
        self.wm.minimize(info.hwnd)
        return Result.success(f"Minimized {self._short(info)}")

    def _maximize(self, info) -> Result:
        if info.protected:
            return Result.error("The desktop and taskbar cannot be maximized")
        if not self.wm.can_maximize(info.hwnd):
            return Result.error("This window cannot be maximized")
        self.wm.maximize(info.hwnd)
        return Result.success(f"Maximized {self._short(info)}")

    def prepare_close(self, cmd: Command) -> tuple[bool, str]:
        """Runs when Close Window is requested, before the confirmation prompt.
        Remembers which window to close so a later focus change cannot redirect it."""
        info = self.wm.foreground_info()
        if info is None:
            return False, "No active window"
        refusal = self._close_refusal(info)
        if refusal:
            return False, refusal
        cmd.target = str(info.hwnd)
        return True, f"Window: {self._short(info)}"

    def _close_refusal(self, info) -> Optional[str]:
        if info.own:
            return "Hand&Voice will not close its own window (use the tray menu: Quit)"
        if info.protected:
            return "The desktop and taskbar cannot be closed"
        if info.elevated:
            return ELEVATED_MESSAGE
        return None

    def _close(self, cmd: Command, info) -> Result:
        # After confirmation the command carries the window chosen at request time.
        if cmd.target and cmd.target.isdigit():
            hwnd = int(cmd.target)
        elif info is not None:
            hwnd = info.hwnd
        else:
            return Result.error("No active window")
        target = info if (info is not None and hwnd == info.hwnd) else self.wm.describe(hwnd)
        if target is None or not self.wm.is_window(hwnd):
            return Result.error("That window is already gone")
        refusal = self._close_refusal(target)
        if refusal:
            return Result.error(refusal)
        self.wm.post_close(hwnd)
        return Result.success(f"Asked {self._short(target)} to close")

    @staticmethod
    def _short(info) -> str:
        title = (info.title or info.process or "window").strip()
        return f"'{title[:40]}'" if title else "window"


def register(executors: dict, window_commands: WindowCommands) -> None:
    """Add every window command to the dispatcher's executor table."""
    for ctype in HANDLED:
        executors[ctype] = window_commands.execute
