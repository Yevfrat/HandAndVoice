import os
import queue
import time
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from core.app_controller import AppController
from core.command import CommandType, Result
from core.confirmation import ConfirmationManager
from core.dispatcher import Dispatcher
from core.feedback import Feedback
from core.mode_manager import Mode, ModeManager
from gestures.engine import GestureEngine
from gestures.face import WINK_LEFT, WINK_RIGHT
from gestures.face_controller import FaceController
from gestures.types import GestureEvent
from services.config_store import ConfigStore
from tests.face_model import CLOSED_EAR, OPEN_EAR, detection
from tests.test_gesture_engine import GESTURES, FakeCamera, collect, drain, wait_for


# ---------------------------------------------------------------- ModeManager
def test_face_only_turns_voice_and_hand_gestures_off_and_the_camera_on():
    m = ModeManager("HYBRID")
    m.set_mode(Mode.FACE_ONLY)
    assert (m.voice_enabled, m.hands_enabled, m.face_enabled, m.camera_enabled) == (False, False, True, True)
    for mode, voice, hands in ((Mode.HYBRID, True, True), (Mode.VOICE_ONLY, True, False),
                               (Mode.GESTURE_ONLY, False, True), (Mode.PAUSED, False, False)):
        m.set_mode(mode)
        assert (m.voice_enabled, m.hands_enabled, m.face_enabled) == (voice, hands, False)


def test_face_only_is_never_a_start_mode(tmp_path):
    assert ModeManager("FACE_ONLY").mode == Mode.HYBRID
    store = ConfigStore(data_dir=tmp_path / "d"); store.ensure_initialized()
    store.set("start_mode", "FACE_ONLY")
    assert store.get("start_mode") == "HYBRID"            # the setting refuses it


def test_resume_leaves_face_only_for_the_last_ordinary_mode_and_pause_never_returns_to_it():
    m = ModeManager("HYBRID")
    m.set_mode(Mode.VOICE_ONLY)
    m.set_mode(Mode.FACE_ONLY)
    m.resume()
    assert m.mode == Mode.VOICE_ONLY
    m.set_mode(Mode.FACE_ONLY); m.pause(); m.resume()
    assert m.mode == Mode.VOICE_ONLY                        # resume after a pause does not bring Face Only back


# ---------------------------------------------------------------- FaceController actions
class FakeKB:
    def __init__(self): self.log = []
    def press(self, k): self.log.append(k)


class FakeWM:
    def __init__(self, process="notepad.exe", elevated=False):
        self.info = SimpleNamespace(process=process, elevated=elevated)
    def foreground_info(self): return self.info


def rig(style="TABS", process="notepad.exe", elevated=False, mode=Mode.FACE_ONLY):
    fb = Feedback()
    events, ran = [], []
    fb.add_listener(events.append)
    modes = ModeManager("HYBRID")
    modes.set_mode(mode)
    executors = {t: (lambda c, t=t: (ran.append((t.name, c.source)), Result.success(t.name))[1])
                 for t in (CommandType.NEXT_TAB, CommandType.PREVIOUS_TAB, CommandType.NEXT_WINDOW,
                           CommandType.PREVIOUS_WINDOW)}
    disp = Dispatcher(None, modes, ConfirmationManager(5.0), fb, executors, {})
    kb = FakeKB()
    fc = FaceController(disp, fb, lambda: {"face_wink_style": style}, keyboard=kb, wm=FakeWM(process, elevated))
    return SimpleNamespace(fc=fc, kb=kb, ran=ran, events=events, modes=modes)


def ev(name, phase="start"):
    return GestureEvent(name, phase)


def test_tabs_style_in_a_browser():
    r = rig("TABS", "chrome.exe")
    r.fc.handle(ev(WINK_RIGHT)); r.fc.handle(ev(WINK_LEFT))
    assert r.ran == [("NEXT_TAB", "face"), ("PREVIOUS_TAB", "face")] and r.kb.log == []


def test_tabs_style_outside_a_browser_switches_windows():
    r = rig("TABS", "notepad.exe")
    r.fc.handle(ev(WINK_RIGHT)); r.fc.handle(ev(WINK_LEFT))
    assert r.ran == [("NEXT_WINDOW", "face"), ("PREVIOUS_WINDOW", "face")]


def test_pages_style_sends_page_down_and_page_up_in_any_app():
    r = rig("PAGES", "notepad.exe")
    r.fc.handle(ev(WINK_RIGHT)); r.fc.handle(ev(WINK_LEFT))
    assert r.kb.log == ["pagedown", "pageup"] and r.ran == []


def test_history_style_sends_forward_and_back():
    r = rig("HISTORY", "firefox.exe")
    r.fc.handle(ev(WINK_RIGHT)); r.fc.handle(ev(WINK_LEFT))
    assert r.kb.log == ["alt+right", "alt+left"]


def test_only_the_start_of_a_wink_acts():
    r = rig("PAGES")
    for phase in ("end", "tick"):
        r.fc.handle(ev(WINK_RIGHT, phase))
    r.fc.handle(ev("POINT_RIGHT"))                          # a hand gesture name means nothing here
    assert r.kb.log == []


def test_administrator_windows_are_refused_without_retry():
    r = rig("PAGES", elevated=True)
    r.fc.handle(ev(WINK_RIGHT))
    assert r.kb.log == [] and [e.title for e in r.events if e.kind == "error"] == [
        "Target app runs as administrator; keyboard input is blocked by Windows"]


def test_face_commands_are_refused_outside_face_only_mode():
    r = rig("TABS", "chrome.exe", mode=Mode.HYBRID)
    r.fc.handle(ev(WINK_RIGHT))
    assert r.ran == [] and [e.title for e in r.events if e.kind == "error"] == [
        "Face expressions are off in this mode"]


def test_hand_commands_are_refused_in_face_only_mode():
    r = rig("TABS", "chrome.exe")
    from core.command import Command
    r.fc.dispatcher.dispatch(Command(CommandType.NEXT_TAB, None, "gesture", 1.0, "FACE_ONLY", False, "x"))
    assert r.ran == [] and any(e.kind == "error" for e in r.events)


# ---------------------------------------------------------------- through the real controller
@pytest.fixture
def app(tmp_path):
    store = ConfigStore(data_dir=tmp_path / "d"); store.ensure_initialized()
    kb, wm = FakeKB(), FakeWM("chrome.exe")
    kb.key_down = lambda k: kb.log.append("down " + k)
    kb.key_up = lambda k: kb.log.append("up " + k)
    kb.send_wheel = lambda n: kb.log.append("wheel")
    c = AppController(store, gesture_keyboard=kb, gesture_wm=wm)
    c.engine.set_enabled(True)
    c._apply_gesture_state()
    return SimpleNamespace(c=c, store=store, kb=kb)


def device_flags(c):
    return c.engine._enabled.is_set(), c.gestures._enabled.is_set(), c.gestures.pipeline


def test_entering_face_only_closes_the_microphone_and_switches_the_camera_to_the_face_pipeline(app):
    c = app.c
    assert device_flags(c) == (True, True, "hand")
    c.mode_manager.set_mode(Mode.FACE_ONLY)
    assert device_flags(c) == (False, True, "face")
    c.mode_manager.resume()
    assert device_flags(c) == (True, True, "hand")


def test_face_only_works_even_if_hand_gestures_are_switched_off(app):
    c = app.c
    c.set_gestures_enabled(False)
    assert device_flags(c)[1] is False
    c.mode_manager.set_mode(Mode.FACE_ONLY)
    assert device_flags(c) == (False, True, "face")


def test_entering_face_only_releases_a_held_alt_key(app):
    c = app.c
    c.gesture_controller._keyboard = app.kb
    c.gesture_controller.handle(GestureEvent("INDEX_THUMB", "start"))
    assert c.gesture_controller.s.alt_held
    c.mode_manager.set_mode(Mode.FACE_ONLY)
    assert not c.gesture_controller.s.alt_held and "up alt" in app.kb.log


def test_face_mode_cannot_be_entered_by_voice(app):
    c = app.c
    for phrase in ("face only", "hand face only", "hand face mode", "hand start face"):
        c.dispatcher.handle_text(phrase)
    assert c.mode_manager.mode != Mode.FACE_ONLY
    assert not any("face" in k for k in c.voice_commands)


def test_events_are_routed_by_the_current_mode(app):
    from gestures.engine import GestureMessage
    c = app.c
    app.store.set("face_wink_style", "PAGES")
    wink = GestureMessage("event", GestureEvent(WINK_RIGHT, "start"))
    c.gesture_events.put(wink); c.poll()
    assert app.kb.log == []                                  # Hybrid: a wink does nothing
    c.mode_manager.set_mode(Mode.FACE_ONLY)
    c.gesture_events.put(wink); c.poll()
    assert app.kb.log == ["pagedown"]
    c.gesture_events.put(GestureMessage("event", GestureEvent("ROCK", "start"))); c.poll()
    assert app.kb.log == ["pagedown"]                        # a hand gesture does nothing in Face Only


def test_ctrl_alt_r_leaves_face_only(app):
    c = app.c
    c.mode_manager.set_mode(Mode.FACE_ONLY)
    K = lambda **kw: SimpleNamespace(**kw)
    c.hotkeys._press(K(name="ctrl_l")); c.hotkeys._press(K(name="alt_l")); c.hotkeys._press(K(vk=0x52, char="\x12"))
    c.poll()
    assert c.mode_manager.mode == Mode.HYBRID and device_flags(c) == (True, True, "hand")


def test_the_user_is_told_what_face_only_does(app):
    events = []
    app.c.feedback.add_listener(events.append)
    app.c.mode_manager.set_mode(Mode.FACE_ONLY)
    info = [e for e in events if e.kind == "info"][-1]
    assert info.title == "Face Only mode" and "RIGHT eye = next" in info.body and "Ctrl+Alt+R" in info.body


def test_face_settings_reload_the_engine_and_are_validated(app):
    c = app.c
    v = c.gestures._version
    c.set_face_setting("face_wink_style", "history")
    c.set_face_setting("face_hold_seconds", 99)
    c.set_face_setting("face_swap_eyes", True)
    assert app.store.get("face_wink_style") == "HISTORY" and app.store.get("face_hold_seconds") == 3.0
    assert app.store.get("face_swap_eyes") is True and c.gestures._version == v + 3
    c.set_face_setting("face_wink_style", "nonsense")
    assert app.store.get("face_wink_style") == "TABS"


# ---------------------------------------------------------------- the engine's face pipeline
class FakeFaceTracker:
    def __init__(self, right=OPEN_EAR, left=OPEN_EAR):
        self.right, self.left, self.closed = right, left, False

    def detect(self, frame, ts):
        return None if self.right is None else detection(self.right, self.left)

    def close(self):
        self.closed = True


def make_engine(tmp_path, face_tracker):
    store = ConfigStore(data_dir=tmp_path / "d"); store.ensure_initialized()
    store.update({"stable_frames": 2, "face_hold_seconds": 0.2})
    events = queue.Queue()
    FakeCamera.instances.clear()
    eng = GestureEngine(store, lambda: GESTURES, events, camera_factory=lambda s: FakeCamera(s),
                        tracker_factory=lambda s: (_ for _ in ()).throw(AssertionError("hand tracker used")),
                        face_tracker_factory=lambda s: face_tracker)
    return eng, events


def test_face_pipeline_reports_status_and_winks(tmp_path):
    tracker = FakeFaceTracker()
    eng, events = make_engine(tmp_path, tracker)
    eng.set_pipeline("face")
    eng.start(); eng.set_enabled(True)
    try:
        ok, got = collect(events, lambda g: any(m.kind == "status" and not m.payload.calibrating
                                                and m.payload.face for m in g), timeout=5)
        assert ok and all(m.payload.pipeline == "face" for m in got if m.kind == "status")
        tracker.right = CLOSED_EAR                              # close the right eye
        ok, got = collect(events, lambda g: any(m.kind == "event" and m.payload.name == WINK_RIGHT for m in g),
                          timeout=5)
        assert ok
        statuses = [m.payload for m in got if m.kind == "status"]
        assert any(s.right_eye < 0.5 and s.left_eye > 0.9 for s in statuses)
    finally:
        eng.shutdown()


def test_no_face_gives_status_without_events(tmp_path):
    eng, events = make_engine(tmp_path, FakeFaceTracker(right=None))
    eng.set_pipeline("face")
    eng.start(); eng.set_enabled(True)
    try:
        ok, got = collect(events, lambda g: len([m for m in g if m.kind == "status"]) > 6)
        assert ok and not any(m.payload.face for m in got if m.kind == "status")
        assert not [m for m in got if m.kind == "event"]
    finally:
        eng.shutdown()


def test_switching_pipelines_restarts_the_tracker_but_uses_one_camera_at_a_time(tmp_path):
    from tests.test_gesture_engine import FakeTracker
    store = ConfigStore(data_dir=tmp_path / "d"); store.ensure_initialized()
    events = queue.Queue()
    FakeCamera.instances.clear()
    face = FakeFaceTracker()
    eng = GestureEngine(store, lambda: GESTURES, events, camera_factory=lambda s: FakeCamera(s),
                        tracker_factory=lambda s: FakeTracker("PEACE"), face_tracker_factory=lambda s: face)
    eng.start(); eng.set_enabled(True)
    try:
        assert wait_for(lambda: FakeCamera.instances and FakeCamera.instances[0].opened)
        eng.set_pipeline("face")
        assert wait_for(lambda: len(FakeCamera.instances) == 2 and FakeCamera.instances[1].opened)
        assert FakeCamera.instances[0].closed                    # the first camera was released first
        eng.set_pipeline("face")                                 # same pipeline again: no restart
        time.sleep(0.3)
        assert len(FakeCamera.instances) == 2
    finally:
        eng.shutdown()


def test_missing_face_model_is_reported_clearly(tmp_path):
    from gestures.face_tracker import FaceModelMissingError
    store = ConfigStore(data_dir=tmp_path / "d"); store.ensure_initialized()
    events = queue.Queue()
    FakeCamera.instances.clear()

    def bad(s):
        raise FaceModelMissingError("The face model is missing. Run: python scripts/download_models.py --face")
    eng = GestureEngine(store, lambda: GESTURES, events, camera_factory=lambda s: FakeCamera(s),
                        face_tracker_factory=bad)
    eng.set_pipeline("face")
    eng.start(); eng.set_enabled(True)
    try:
        ok, got = collect(events, lambda g: any(m.kind == "error" for m in g))
        assert ok and "face model" in [m.payload for m in got if m.kind == "error"][0]
        assert wait_for(lambda: FakeCamera.instances[-1].closed)       # the camera is not left on
    finally:
        eng.shutdown()


# ---------------------------------------------------------------- UI
@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def test_face_page_button_and_model_check(app, qapp, monkeypatch, tmp_path):
    from ui import face_page
    from ui.face_page import FacePage
    monkeypatch.setattr(face_page, "MODEL_FILE", tmp_path / "missing.task")
    page = FacePage(app.c)
    assert not page.toggle.isEnabled() and "MISSING" in page.model_label.text()
    present = tmp_path / "face_landmarker.task"; present.write_bytes(b"x")
    monkeypatch.setattr(face_page, "MODEL_FILE", present)
    page.refresh_model()
    assert page.toggle.isEnabled() and "installed" in page.model_label.text()
    got = []
    page.start_requested.connect(lambda: got.append("start")); page.leave_requested.connect(lambda: got.append("leave"))
    page.toggle.click()
    page.set_mode("FACE_ONLY")
    assert "Leave" in page.toggle.text()
    page.toggle.click()
    assert got == ["start", "leave"]


def test_face_page_shows_every_state(app, qapp):
    from gestures.types import GestureStatus
    from ui.face_page import FacePage
    page = FacePage(app.c)
    page.set_mode("FACE_ONLY")
    page.set_status(GestureStatus(pipeline="face", face=False), camera=True)
    assert page.headline.text() == "No face in view"
    page.set_status(GestureStatus(pipeline="face", face=True, calibrating=True), camera=True)
    assert "Learning" in page.headline.text()
    page.set_status(GestureStatus(pipeline="face", face=True, right_eye=1.0, left_eye=0.95, fps=12), camera=True)
    assert page.headline.text() == "Both eyes open" and page.right_bar.value() == 100 and page.left_bar.value() == 95
    page.set_status(GestureStatus(gesture=WINK_RIGHT, pipeline="face", face=True, right_eye=0.1, progress=0.5),
                    camera=True)
    assert page.headline.text() == "RIGHT eye closed" and page.right_bar.value() == 10 and "50%" in page.hint.text()
    page.set_status(GestureStatus(gesture=WINK_LEFT, pipeline="face", face=True, started=True), camera=True)
    assert page.headline.text() == "LEFT eye closed" and page.hint.text() == "Done."
    page.set_mode("HYBRID")
    assert page.headline.text() == "Face Only mode is OFF"


def test_face_page_settings_save(app, qapp):
    from ui.face_page import FacePage
    page = FacePage(app.c)
    page.style.setCurrentIndex(page.style.findData("PAGES")); page.style.activated.emit(page.style.currentIndex())
    assert app.store.get("face_wink_style") == "PAGES"
    assert page.table.item(0, 1).text() == "Page Down" and page.table.item(1, 1).text() == "Page Up"
    page.hold.setValue(0.8); page.hold.editingFinished.emit()
    assert app.store.get("face_hold_seconds") == 0.8
    page.swap.setChecked(True)
    assert app.store.get("face_swap_eyes") is True


def test_tray_dashboard_and_window_know_face_only(app, qapp):
    from ui.dashboard import DashboardPage
    from ui.main_window import MainWindow
    from ui.tray import Tray
    t = Tray()
    assert "FACE_ONLY" in t._mode_actions
    t.set_mode("FACE_ONLY")
    assert [k for k, a in t._mode_actions.items() if a.isChecked()] == ["FACE_ONLY"] and "Face Only" in t.icon.toolTip()
    d = DashboardPage()
    got = []
    d.mode_requested.connect(got.append)
    d._mode_buttons["FACE_ONLY"].click()
    assert got == ["FACE_ONLY"]
    w = MainWindow(app.store, app.c)
    assert "Face Expressions" in MainWindow.PAGES and w.stack.count() == len(MainWindow.PAGES)
    w.show()
    from gestures.types import GestureStatus
    app.c.camera_on = True
    w.face_page.set_mode("FACE_ONLY")
    w.on_gesture("status", GestureStatus(pipeline="face", face=True, right_eye=0.3, left_eye=1.0))
    assert w.face_page.right_bar.value() == 30
    w.on_gesture("status", GestureStatus(pipeline="hand", hand=True, gesture="PEACE"))
    assert "PEACE" in w.gesture_page.big.text()
    w.apply_state("mode", "FACE_ONLY")
    assert w.header.mode_text.text() == "Mode: Face Only"
    w._really_quit = True
    w.close()


def test_download_script_knows_the_face_model():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "download_models", os.path.join(os.path.dirname(__file__), "..", "scripts", "download_models.py"))
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    assert mod.FACE_URL.startswith("https://storage.googleapis.com/") and mod.FACE_URL.endswith("face_landmarker.task")
    assert mod.FACE_FILE.name == "face_landmarker.task"
