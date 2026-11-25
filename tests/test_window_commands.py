import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from automation.window_commands import (
    BROWSER_ONLY, DEFAULT_KEYS, ELEVATED_MESSAGE, HANDLED, SCROLL_NOTCHES, WindowCommands, register,
)
from core.command import Command, CommandType, Result
from core.confirmation import ConfirmationManager
from core.dispatcher import Dispatcher
from core.feedback import Feedback
from core.mode_manager import ModeManager
from voice.command_parser import CommandParser, FuzzyResult

COMMANDS = json.loads((Path(__file__).parent.parent / "config" / "voice_commands.json").read_text())


def info(process="notepad.exe", **kw):
    base = dict(hwnd=100, title="Untitled - Notepad", class_name="Notepad", pid=5, process=process,
                elevated=False, own=False, protected=False)
    base.update(kw)
    return SimpleNamespace(**base)


class FakeWM:
    def __init__(self, fg):
        self.fg = fg
        self.calls = []
        self.windows = {fg.hwnd: fg} if fg else {}
        self.minbox = self.maxbox = True

    def foreground_info(self): return self.fg
    def describe(self, hwnd): return self.windows.get(hwnd)
    def is_window(self, hwnd): return hwnd in self.windows
    def can_minimize(self, h): return self.minbox
    def can_maximize(self, h): return self.maxbox
    def minimize(self, h): self.calls.append(("minimize", h))
    def maximize(self, h): self.calls.append(("maximize", h))
    def post_close(self, h): self.calls.append(("close", h))


class FakeKB:
    def __init__(self): self.log = []
    def press(self, keys): self.log.append(("press", keys))
    def send_wheel(self, n): self.log.append(("wheel", n))


def make(fg=None, commands=None):
    wm = FakeWM(fg if fg is not None else info())
    kb = FakeKB()
    return WindowCommands(keyboard=kb, wm=wm, voice_commands=commands), wm, kb


def cmd(ctype, target=None, raw=""):
    return Command(ctype, target, raw=raw or ctype.value.lower().replace("_", " "))


# ---------------- keys ----------------
@pytest.mark.parametrize("ctype,keys", [
    (CommandType.SWITCH_WINDOW, "alt+tab"), (CommandType.TASK_VIEW, "win+tab"),
    (CommandType.SHOW_DESKTOP, "win+d"), (CommandType.FULLSCREEN, "f11"),
    (CommandType.NEXT_WINDOW, "alt+tab"), (CommandType.PREVIOUS_WINDOW, "alt+shift+tab"),
])
def test_window_keys(ctype, keys):
    wc, wm, kb = make()
    assert wc.execute(cmd(ctype)).ok
    assert kb.log == [("press", keys)]


@pytest.mark.parametrize("ctype,keys", [
    (CommandType.NEW_TAB, "ctrl+t"), (CommandType.CLOSE_TAB, "ctrl+w"), (CommandType.NEXT_TAB, "ctrl+tab"),
    (CommandType.PREVIOUS_TAB, "ctrl+shift+tab"), (CommandType.REFRESH, "f5"), (CommandType.BACK, "alt+left"),
    (CommandType.FORWARD, "alt+right"), (CommandType.PAGE_UP, "pageup"), (CommandType.PAGE_DOWN, "pagedown"),
    (CommandType.GO_TO_TOP, "home"), (CommandType.GO_TO_BOTTOM, "end"),
])
def test_browser_keys_in_browser(ctype, keys):
    wc, wm, kb = make(info("chrome.exe"))
    assert wc.execute(cmd(ctype)).ok
    assert kb.log == [("press", keys)]


@pytest.mark.parametrize("ctype,sign", [(CommandType.SCROLL_UP, 1), (CommandType.SCROLL_DOWN, -1)])
def test_scroll_is_five_wheel_notches(ctype, sign):
    wc, wm, kb = make(info("msedge.exe"))
    assert wc.execute(cmd(ctype)).ok
    assert kb.log == [("wheel", sign)] * SCROLL_NOTCHES == [("wheel", sign)] * 5


def test_browser_commands_rejected_outside_a_browser():
    for ctype in BROWSER_ONLY:
        wc, wm, kb = make(info("notepad.exe"))
        res = wc.execute(cmd(ctype))
        assert not res.ok and "browser" in res.message
        assert kb.log == []


def test_table_matches_spec_and_config():
    assert set(DEFAULT_KEYS) | {CommandType.SCROLL_UP, CommandType.SCROLL_DOWN, CommandType.MINIMIZE,
                                CommandType.MAXIMIZE, CommandType.CLOSE_WINDOW} == HANDLED
    for spec in COMMANDS.values():                  # shipped config agrees with the code defaults
        t = CommandType(spec["type"])
        if t in DEFAULT_KEYS:
            assert spec["keys"] == DEFAULT_KEYS[t]


def test_keys_can_be_overridden_in_voice_commands_json():
    wc, wm, kb = make(info("chrome.exe"), commands={"refresh": {"type": "REFRESH", "keys": "ctrl+r"}})
    wc.execute(cmd(CommandType.REFRESH))
    assert kb.log == [("press", "ctrl+r")]


# ---------------- minimize / maximize ----------------
def test_minimize_and_maximize():
    wc, wm, kb = make()
    assert wc.execute(cmd(CommandType.MINIMIZE)).ok
    assert wc.execute(cmd(CommandType.MAXIMIZE)).ok
    assert wm.calls == [("minimize", 100), ("maximize", 100)]
    wm.minbox = wm.maxbox = False
    assert "cannot be minimized" in wc.execute(cmd(CommandType.MINIMIZE)).message
    assert "cannot be maximized" in wc.execute(cmd(CommandType.MAXIMIZE)).message


def test_desktop_and_taskbar_are_never_touched():
    wc, wm, kb = make(info(class_name="Shell_TrayWnd", protected=True))
    assert not wc.execute(cmd(CommandType.MINIMIZE)).ok
    assert not wc.execute(cmd(CommandType.MAXIMIZE)).ok
    assert wm.calls == []


# ---------------- close window ----------------
def test_close_pins_the_target_and_posts_wm_close():
    wc, wm, kb = make()
    c = cmd(CommandType.CLOSE_WINDOW)
    ok, detail = wc.prepare_close(c)
    assert ok and "Untitled - Notepad" in detail and c.target == "100"
    other = info(hwnd=200, title="Other")
    wm.fg = other; wm.windows[200] = other           # focus moved before the user confirmed
    assert wc.execute(c).ok
    assert wm.calls == [("close", 100)]               # still the window that was requested


def test_close_refuses_own_desktop_taskbar_and_elevated():
    for bad, text in ((info(own=True), "own window"), (info(protected=True), "desktop and taskbar"),
                      (info(elevated=True), "administrator")):
        wc, wm, kb = make(bad)
        ok, msg = wc.prepare_close(cmd(CommandType.CLOSE_WINDOW))
        assert not ok and text in msg
        res = wc.execute(cmd(CommandType.CLOSE_WINDOW))
        assert not res.ok and wm.calls == []


def test_close_target_already_gone():
    wc, wm, kb = make()
    c = cmd(CommandType.CLOSE_WINDOW, target="999")
    assert wc.execute(c).message == "That window is already gone"
    assert wm.calls == []


# ---------------- elevated / errors ----------------
def test_elevated_foreground_blocks_keys_without_retry():
    wc, wm, kb = make(info(elevated=True))
    res = wc.execute(cmd(CommandType.SHOW_DESKTOP))
    assert not res.ok and res.message == ELEVATED_MESSAGE
    assert kb.log == []


def test_no_active_window():
    wc, wm, kb = make()
    wm.fg = None
    assert wc.execute(cmd(CommandType.MINIMIZE)).message == "No active window"


def test_input_blocked_error_message_is_shown():
    from automation.keyboard_controller import InputBlockedError

    class Blocked(FakeKB):
        def press(self, keys): raise InputBlockedError("Windows blocked keyboard input")
    wc, wm, _ = make()
    wc._keyboard = Blocked()
    res = wc.execute(cmd(CommandType.FULLSCREEN))
    assert not res.ok and "blocked" in res.message


# ---------------- through the dispatcher ----------------
class Resolver:
    def find_exact(self, t): return None
    def find_fuzzy(self, t): return FuzzyResult("none")


def build_dispatcher(fg=None):
    wc, wm, kb = make(fg)
    execs = {}
    register(execs, wc)
    fb = Feedback()
    events = []
    fb.add_listener(events.append)
    d = Dispatcher(CommandParser(COMMANDS, Resolver(), "hand"), ModeManager("HYBRID"),
                   ConfirmationManager(5.0), fb, execs,
                   {"CLOSE_WINDOW": "Ask the foreground window to close"},
                   preparers={CommandType.CLOSE_WINDOW: wc.prepare_close})
    return d, wm, kb, events


def test_close_window_needs_confirmation_end_to_end():
    d, wm, kb, events = build_dispatcher()
    d.handle_text("hand close window")
    assert wm.calls == []
    prompt = [e for e in events if e.kind == "prompt"][-1]
    assert "Untitled - Notepad" in prompt.body
    d.handle_text("hand confirm")
    assert wm.calls == [("close", 100)]


def test_close_window_cancel_does_nothing():
    d, wm, kb, events = build_dispatcher()
    d.handle_text("hand close window")
    d.handle_text("hand cancel")
    assert wm.calls == []


def test_close_window_on_own_window_never_asks():
    d, wm, kb, events = build_dispatcher(info(own=True))
    d.handle_text("hand close window")
    assert not [e for e in events if e.kind == "prompt"]
    assert events[-1].kind == "error" and wm.calls == []


def test_voice_minimize_through_dispatcher():
    d, wm, kb, events = build_dispatcher()
    d.handle_text("hand minimize")
    assert wm.calls == [("minimize", 100)] and events[-1].kind == "success"
