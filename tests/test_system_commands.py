import queue
from types import SimpleNamespace

import pytest

from automation import keyboard_controller as kc
from automation.media_controller import HANDLED as MEDIA_TYPES
from automation.media_controller import MediaController
from automation.system_controller import SHUTDOWN_DELAY_SECONDS, SystemController
from core.app_controller import AppController
from core.command import Command, CommandType
from services.config_store import ConfigStore


def win(hwnd, title, process="app.exe", elevated=False, own=False, protected=False):
    return SimpleNamespace(hwnd=hwnd, title=title, process=process, elevated=elevated, own=own,
                           protected=protected)


class FakeWM:
    def __init__(self, windows, fg=None):
        self.windows = {w.hwnd: w for w in windows}
        self.fg = fg if fg is not None else (windows[0] if windows else None)
        self.closed = []
        self.open = set(self.windows)

    def foreground_info(self): return self.fg
    def closable_windows(self): return [w for w in self.windows.values() if not (w.own or w.protected)]
    def describe(self, h): return self.windows.get(h)
    def post_close(self, h): self.closed.append(h)
    def is_open(self, h): return h in self.open


class FakeKB:
    def __init__(self): self.log = []
    def press(self, keys): self.log.append(keys)


class FakeProc:
    def __init__(self, rc=0, out="", err=""): self.returncode, self.stdout, self.stderr = rc, out, err


class Recorder:
    def __init__(self, rc=0): self.calls, self.rc = [], rc
    def __call__(self, args, **kw): self.calls.append(list(args)); return FakeProc(self.rc)


class FakeTimer:
    """Runs the callback when fired manually, so the 3 s report can be tested instantly."""
    instances = []
    def __init__(self, delay, fn, args=()): self.delay, self.fn, self.args, self.daemon = delay, fn, args, False; FakeTimer.instances.append(self)
    def start(self): pass
    def fire(self): self.fn(*self.args)


def cmd(ctype, target=None):
    return Command(ctype, target, raw=ctype.value.lower().replace("_", " "))


# ---------------- media ----------------
@pytest.mark.parametrize("ctype,key", [
    (CommandType.VOLUME_UP, "volume_up"), (CommandType.VOLUME_DOWN, "volume_down"),
    (CommandType.MUTE, "volume_mute"), (CommandType.PLAY_MEDIA, "media_play_pause"),
    (CommandType.PAUSE_MEDIA, "media_play_pause"),
])
def test_media_keys(ctype, key):
    kb = FakeKB()
    assert MediaController(kb).execute(cmd(ctype)).ok
    assert kb.log == [key]


def test_media_keys_are_virtual_keys_not_scancodes():
    for name, vk in (("volume_up", 0xAF), ("volume_down", 0xAE), ("volume_mute", 0xAD),
                     ("media_play_pause", 0xB3)):
        assert kc.vk_for(name) == vk
        inp = kc._key_input(vk, up=False)
        assert inp.ki.wVk == vk and not inp.ki.dwFlags & kc.KEYEVENTF_SCANCODE
    assert MEDIA_TYPES == {CommandType.VOLUME_UP, CommandType.VOLUME_DOWN, CommandType.MUTE,
                           CommandType.PLAY_MEDIA, CommandType.PAUSE_MEDIA}


def test_media_failure_is_reported():
    class Broken:
        def press(self, k): raise kc.InputBlockedError("blocked")
    assert not MediaController(Broken()).execute(cmd(CommandType.MUTE)).ok


# ---------------- screenshot / lock / power ----------------
def test_screenshot_and_elevated_block():
    kb = FakeKB()
    sc = SystemController(keyboard=kb, wm=FakeWM([win(1, "A")]))
    assert sc.screenshot().ok and kb.log == ["win+printscreen"]
    sc = SystemController(keyboard=kb, wm=FakeWM([win(1, "A", elevated=True)]))
    assert not sc.screenshot().ok and kb.log == ["win+printscreen"]


def test_lock():
    assert SystemController(lock_fn=lambda: True).lock().ok
    assert not SystemController(lock_fn=lambda: False).lock().ok


def test_sleep_command_line():
    calls = []
    sc = SystemController(popen=lambda args, **kw: calls.append(args))
    assert sc.sleep().ok
    assert calls == [["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"]]


def test_shutdown_restart_and_abort_command_lines():
    run = Recorder()
    sc = SystemController(run=run)
    assert "60 s" in sc.shutdown().message and "Hand, cancel" in sc.shutdown().message
    sc.restart()
    assert sc.abort_shutdown().ok
    assert run.calls == [["shutdown", "/s", "/t", "60"], ["shutdown", "/s", "/t", "60"],
                         ["shutdown", "/r", "/t", "60"], ["shutdown", "/a"]]
    assert SHUTDOWN_DELAY_SECONDS == 60
    assert not SystemController(run=Recorder(rc=1116)).abort_shutdown().ok
    assert not SystemController(run=Recorder(rc=5)).shutdown().ok


# ---------------- close all apps ----------------
def test_prepare_lists_names_count_and_skips_admin_windows():
    wm = FakeWM([win(1, "Notepad"), win(2, "Chrome"), win(3, "Task Manager", elevated=True),
                 win(4, "Mine", own=True), win(5, "Taskbar", protected=True)])
    sc = SystemController(wm=wm)
    c = cmd(CommandType.CLOSE_ALL_APPS)
    ok, detail = sc.prepare_close_all(c)
    assert ok and "2 window(s)" in detail and "Notepad" in detail and "Chrome" in detail
    assert "Mine" not in detail and "Taskbar" not in detail and "1 administrator" in detail
    assert c.target == "1,2"


def test_prepare_limits_names_shown_and_refuses_when_nothing_to_close():
    wm = FakeWM([win(i, f"App {i}") for i in range(1, 13)])
    ok, detail = SystemController(wm=wm).prepare_close_all(cmd(CommandType.CLOSE_ALL_APPS))
    assert ok and "12 window(s)" in detail and "and 4 more" in detail
    ok, detail = SystemController(wm=FakeWM([win(9, "Mine", own=True)])).prepare_close_all(
        cmd(CommandType.CLOSE_ALL_APPS))
    assert not ok


def test_close_all_posts_wm_close_only_to_the_listed_still_allowed_windows():
    FakeTimer.instances.clear()
    notes = []
    wm = FakeWM([win(1, "Notepad"), win(2, "Chrome"), win(3, "Mine", own=True), win(4, "Admin", elevated=True)])
    sc = SystemController(wm=wm, notify=lambda k, t: notes.append((k, t)), timer_factory=FakeTimer)
    res = sc.close_all(cmd(CommandType.CLOSE_ALL_APPS, "1,2,3,4,99"))      # 3 own, 4 admin, 99 gone
    assert res.ok and wm.closed == [1, 2] and "2 window" in res.message
    wm.open = {2}                                                            # Chrome asks "restore tabs?"
    assert FakeTimer.instances[0].delay == 3.0
    FakeTimer.instances[0].fire()
    assert notes == [("info", "Still open (probably asking to save): Chrome")]


def test_close_all_reports_when_everything_closed():
    FakeTimer.instances.clear()
    notes = []
    wm = FakeWM([win(1, "A"), win(2, "B")])
    sc = SystemController(wm=wm, notify=lambda k, t: notes.append(t), timer_factory=FakeTimer)
    sc.close_all(cmd(CommandType.CLOSE_ALL_APPS, "1,2"))
    wm.open = set()
    FakeTimer.instances[0].fire()
    assert notes == ["All 2 windows closed"]


def test_close_all_without_targets_fails():
    sc = SystemController(wm=FakeWM([win(1, "A")]))
    assert not sc.close_all(cmd(CommandType.CLOSE_ALL_APPS, "")).ok


def test_system_helpers_and_duplicate_store_windows_are_not_listed():
    from automation.window_manager import filter_closable

    infos = [win(1, "Notepad", "notepad.exe"), win(2, "Settings", "ApplicationFrameHost.exe"),
             win(3, "Settings", "SystemSettings.exe"), win(4, "Windows Input Experience", "TextInputHost.exe"),
             win(5, "Calculator", "CalculatorApp.exe")]
    assert [i.hwnd for i in filter_closable(infos)] == [1, 2, 5]


# ---------------- through the real controller + dispatcher ----------------
class Clock:
    def __init__(self): self.t = 100.0
    def __call__(self): return self.t


@pytest.fixture
def app(tmp_path):
    store = ConfigStore(data_dir=tmp_path / "d"); store.ensure_initialized()
    FakeTimer.instances.clear()
    run, popen = Recorder(), []
    wm = FakeWM([win(1, "Notepad"), win(2, "Chrome")])
    system = SystemController(keyboard=FakeKB(), wm=wm, run=run, popen=lambda a, **k: popen.append(a),
                              timer_factory=FakeTimer)
    media_kb = FakeKB()                  # NEVER the real keyboard: tests must not touch the PC's volume
    c = AppController(store, system=system, media=MediaController(media_kb))
    clock = Clock()
    c.dispatcher._clock = clock
    c.confirmation._clock = clock
    events = []
    c.feedback.add_listener(events.append)
    return SimpleNamespace(c=c, run=run, popen=popen, wm=wm, clock=clock, events=events, media_kb=media_kb)


def say(app, text):
    app.c.dispatcher.handle_text("hand " + text)


def tick(app, secs):
    app.clock.t += secs
    app.c.dispatcher.tick()


def test_close_all_apps_needs_confirmation_end_to_end(app):
    say(app, "close all apps")
    prompt = [e for e in app.events if e.kind == "prompt"][-1]
    assert "2 window(s)" in prompt.body and "Notepad" in prompt.body
    assert app.wm.closed == []                       # nothing before confirmation
    say(app, "confirm")
    assert app.wm.closed == [1, 2]


def test_close_all_apps_cancel_and_timeout_close_nothing(app):
    say(app, "close all apps"); say(app, "cancel")
    assert app.wm.closed == []
    say(app, "close all apps"); tick(app, 6)
    say(app, "confirm")
    assert app.wm.closed == []


def test_sleep_counts_down_and_can_be_cancelled(app):
    say(app, "sleep")
    prompt = [e for e in app.events if e.kind == "prompt"][-1]
    assert prompt.title == "Sleep in 3s" and app.popen == []
    tick(app, 1.1)
    assert [e for e in app.events if e.kind == "prompt"][-1].title == "Sleep in 2s"
    say(app, "cancel")
    tick(app, 5)
    assert app.popen == []                           # cancelled: the PC never sleeps


def test_sleep_runs_after_the_countdown(app):
    say(app, "sleep")
    tick(app, 2.9)
    assert app.popen == []
    tick(app, 0.2)
    assert app.popen == [["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"]]
    tick(app, 5)
    assert len(app.popen) == 1                       # only once


def test_sleep_countdown_uses_the_setting(app):
    app.c.store.set("sleep_countdown_seconds", 5)
    say(app, "sleep")
    tick(app, 4)
    assert app.popen == []
    tick(app, 1.5)
    assert len(app.popen) == 1


def test_shutdown_needs_confirmation_then_60s_and_cancel_aborts(app):
    say(app, "shut down")
    assert app.run.calls == []
    say(app, "confirm")
    assert app.run.calls == [["shutdown", "/s", "/t", "60"]]
    tick(app, 0.1)
    assert "Shutting down in" in [e for e in app.events if e.kind == "prompt"][-1].title
    say(app, "cancel")
    assert app.run.calls[-1] == ["shutdown", "/a"]
    assert [e for e in app.events if e.kind == "success"][-1].title == "Shutdown cancelled"


def test_restart_flow(app):
    say(app, "restart"); say(app, "confirm")
    assert app.run.calls == [["shutdown", "/r", "/t", "60"]]


def test_shutdown_declined_runs_nothing(app):
    say(app, "shut down"); say(app, "cancel"); tick(app, 6)
    say(app, "confirm")
    assert app.run.calls == []


def test_media_and_screenshot_through_the_dispatcher(app):
    say(app, "volume up"); say(app, "volume down"); say(app, "mute"); say(app, "play media")
    say(app, "pause media"); say(app, "screenshot")
    assert app.media_kb.log == ["volume_up", "volume_down", "volume_mute",
                                "media_play_pause", "media_play_pause"]
    assert app.c.system.keyboard.log == ["win+printscreen"]
    assert [e.title for e in app.events if e.kind == "success"][:3] == [
        "Volume up", "Volume down", "Mute toggled"]


def test_cancel_with_nothing_pending(app):
    say(app, "cancel")
    assert app.events[-1].kind == "error"      # parser: nothing to cancel -> rejected, nothing happens
