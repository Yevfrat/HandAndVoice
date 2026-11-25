"""Engine message -> AppController.poll() -> gesture controller -> dispatcher, with fake hardware."""
from types import SimpleNamespace

import pytest

from automation.media_controller import MediaController
from automation.system_controller import SystemController
from core.app_controller import AppController
from gestures.engine import GestureMessage
from gestures.types import GestureEvent, GestureStatus
from services.config_store import ConfigStore


class FakeKB:
    def __init__(self): self.log = []
    def key_down(self, k): self.log.append(("down", k))
    def key_up(self, k): self.log.append(("up", k))
    def press(self, k): self.log.append(("press", k))
    def send_wheel(self, n): self.log.append(("wheel", n))


class FakeWM:
    def __init__(self):
        self.closed = []
        self.info = SimpleNamespace(process="notepad.exe", elevated=False, own=False, protected=False,
                                    title="Notes", hwnd=1)
    def foreground_info(self): return self.info
    def closable_windows(self): return [SimpleNamespace(hwnd=7, title="Doc", process="x.exe", elevated=False)]
    def describe(self, h): return SimpleNamespace(hwnd=h, title="Doc", process="x.exe", elevated=False,
                                                  own=False, protected=False)
    def post_close(self, h): self.closed.append(h)
    def is_open(self, h): return False


class FakeTimer:
    def __init__(self, delay, fn, args=()): self.fn, self.args, self.daemon = fn, args, False
    def start(self): pass


class Clock:
    def __init__(self): self.t = 500.0
    def __call__(self): return self.t


@pytest.fixture
def app(tmp_path):
    store = ConfigStore(data_dir=tmp_path / "d"); store.ensure_initialized()
    wm, kb = FakeWM(), FakeKB()
    slept, mediakb = [], FakeKB()
    system = SystemController(keyboard=FakeKB(), wm=wm, popen=lambda a, **k: slept.append(a),
                              timer_factory=FakeTimer)
    c = AppController(store, system=system, media=MediaController(mediakb), gesture_keyboard=kb, gesture_wm=wm)
    clock = Clock()
    c.dispatcher._clock = clock
    c.confirmation._clock = clock
    c.gesture_controller._clock = clock
    c.gesture_ui.toggle_overlay = lambda: ui.append("toggle")
    ui = []
    events = []
    c.feedback.add_listener(events.append)
    return SimpleNamespace(c=c, wm=wm, kb=kb, slept=slept, clock=clock, events=events, ui=ui, mediakb=mediakb)


def post(app, name, phase="start", y=0.5):
    app.c.gesture_events.put(GestureMessage("event", GestureEvent(name, phase, 0.5, y, 0.2)))
    app.c.poll()


def test_thumbs_up_then_ok_closes_all_apps_through_the_real_dispatcher(app):
    post(app, "THUMBS_UP")
    assert app.wm.closed == [] and app.c.confirmation.is_pending
    prompt = [e for e in app.events if e.kind == "prompt"][-1]
    assert "1 window(s)" in prompt.body and "Doc" in prompt.body
    post(app, "OK")
    assert app.wm.closed == [7]
    assert app.c.recent_gesture_events[0][1:] == ("OK", "start")


def test_ily_cancels_the_confirmation_and_closes_nothing(app):
    post(app, "THUMBS_UP"); post(app, "ILY")
    assert app.wm.closed == [] and not app.c.confirmation.is_pending


def test_fist_countdown_can_be_cancelled_by_ily(app):
    post(app, "FIST")
    assert app.c.dispatcher.countdown_active and app.slept == []
    app.clock.t += 1.0; app.c.poll()
    post(app, "ILY")
    app.clock.t += 10; app.c.poll()
    assert app.slept == [] and not app.c.dispatcher.countdown_active


def test_fist_sleeps_when_not_cancelled(app):
    post(app, "FIST")
    app.clock.t += 3.2; app.c.poll()
    assert app.slept == [["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"]]


def test_open_palm_calls_the_ui_toggle(app):
    post(app, "OPEN_PALM")
    assert app.ui == ["toggle"]
    title, body = app.c.overlay_info_lines()
    assert title == "Hand&Voice" and "Mode: Hybrid" in body and "Close all apps" in body


def test_scroll_ticks_do_not_flood_the_event_list(app):
    post(app, "POINT_UP", "start")
    for _ in range(5):
        app.clock.t += 0.1
        post(app, "POINT_UP", "tick")
    assert [e[1:] for e in app.c.recent_gesture_events] == [("POINT_UP", "start")]
    assert len([x for x in app.kb.log if x[0] == "wheel"]) >= 5


def test_camera_stopping_releases_alt(app):
    post(app, "INDEX_THUMB")
    assert app.c.gesture_controller.s.alt_held
    app.c.gesture_events.put(GestureMessage("camera", False))
    app.c.poll()
    assert not app.c.gesture_controller.s.alt_held and app.kb.log[-1] == ("up", "alt")


def test_an_engine_error_releases_alt(app):
    post(app, "INDEX_THUMB")
    app.c.gesture_events.put(GestureMessage("error", "The camera stopped delivering frames"))
    app.c.poll()
    assert app.kb.log[-1] == ("up", "alt") and app.c.gesture_controller.mode == "NORMAL"


def test_changing_to_a_mode_without_a_camera_releases_alt(app):
    post(app, "INDEX_THUMB")
    app.c.mode_manager.set_mode("VOICE_ONLY")
    assert app.kb.log[-1] == ("up", "alt")


def test_pausing_releases_alt(app):
    post(app, "INDEX_THUMB")
    app.c.mode_manager.pause()
    assert app.kb.log[-1] == ("up", "alt")


def test_shutdown_releases_alt_first(app):
    post(app, "INDEX_THUMB")
    app.c.shutdown()
    assert app.kb.log[-1] == ("up", "alt")


def test_disabling_gestures_releases_alt(app):
    post(app, "INDEX_THUMB")
    app.c.set_gestures_enabled(False)
    assert app.kb.log[-1] == ("up", "alt")


def test_volume_mode_through_the_wiring(app):
    post(app, "SHAKA", "start", y=0.6)
    app.clock.t += 0.2; post(app, "SHAKA", "tick", y=0.6)
    app.clock.t += 0.2; post(app, "SHAKA", "tick", y=0.5)
    assert app.mediakb.log == [("press", "volume_up")]
    post(app, "SHAKA", "end")
    assert app.c.gesture_controller.mode == "NORMAL"


def test_voice_next_window_steps_the_switcher_only_while_it_is_open(app):
    from core.command import Command, CommandType
    post(app, "INDEX_THUMB")
    app.kb.log.clear()
    app.c.dispatcher.dispatch(Command(CommandType.NEXT_WINDOW, None, "voice", 1.0, "HYBRID", False, "next window"))
    assert app.kb.log == [("press", "right")]


def test_status_messages_update_the_state(app):
    app.c.gesture_events.put(GestureMessage("camera", True))
    app.c.gesture_events.put(GestureMessage("status", GestureStatus("PEACE", 0.9, 12.0, 0.4, True, False)))
    seen = []
    app.c.add_gesture_listener(lambda k, p: seen.append(k))
    app.c.poll()
    assert app.c.camera_on and app.c.gesture_status.gesture == "PEACE" and "status" in seen
