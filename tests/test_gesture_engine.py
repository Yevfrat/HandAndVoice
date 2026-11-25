import json
import queue
import threading
import time
from pathlib import Path

import pytest

from gestures.camera import CameraError
from gestures.engine import GestureEngine
from gestures.hand_tracker import Detection, HandModelMissingError
from gestures.types import Gesture
from services.config_store import ConfigStore
from tests.hand_model import build, to_frame

GESTURES = json.loads((Path(__file__).parent.parent / "config" / "gestures.json").read_text())


class FakeCamera:
    instances = []

    def __init__(self, s, fail_open=False, fail_after=None):
        self.fail_open, self.fail_after = fail_open, fail_after
        self.opened = self.closed = False
        self.reads = 0
        FakeCamera.instances.append(self)

    def set_fps(self, fps):
        self.fps_calls = getattr(self, "fps_calls", []) + [fps]

    def open(self):
        if self.fail_open:
            raise CameraError("Camera 0 could not be opened.")
        self.opened = True

    def read_due(self, now=None):
        time.sleep(0.004)                       # a real camera blocks until the next frame
        self.reads += 1
        if self.fail_after and self.reads > self.fail_after:
            raise CameraError("The camera stopped delivering frames")
        return object()

    def close(self):
        self.closed = True


class FakeTracker:
    def __init__(self, name="OPEN_PALM", label="Right", score=0.95):
        self.name, self.label, self.score = name, label, score
        self.closed = False

    def detect(self, frame, ts):
        if self.name is None:
            return None
        return Detection([tuple(p) for p in to_frame(build(self.name), scale=0.9)], self.label, self.score, 1.0)

    def close(self):
        self.closed = True


def make_engine(tmp_path, tracker=None, camera=None, settings=None):
    store = ConfigStore(data_dir=tmp_path / "d"); store.ensure_initialized()
    store.update({"default_hold_seconds": 0.05, "stable_frames": 2, **(settings or {})})
    events = queue.Queue()
    FakeCamera.instances.clear()
    eng = GestureEngine(store, lambda: {n: dict(c, hold_seconds=0.05) for n, c in GESTURES.items()}, events,
                        camera_factory=camera or (lambda s: FakeCamera(s)),
                        tracker_factory=lambda s: tracker or FakeTracker())
    return eng, events


def drain(events):
    out = []
    while True:
        try:
            out.append(events.get_nowait())
        except queue.Empty:
            return out


def wait_for(cond, timeout=3.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.01)
    return False


def collect(events, until, timeout=3.0):
    got = []
    ok = wait_for(lambda: (got.extend(drain(events)), until(got))[1], timeout)
    return ok, got


def test_disabled_engine_never_opens_the_camera(tmp_path):
    eng, events = make_engine(tmp_path)
    eng.start()
    try:
        time.sleep(0.3)
        assert FakeCamera.instances == []                   # camera stays off until gestures are needed
    finally:
        eng.shutdown()


def test_recognizes_stabilizes_and_reports(tmp_path):
    eng, events = make_engine(tmp_path)
    eng.start(); eng.set_enabled(True)
    try:
        ok, got = collect(events, lambda g: any(m.kind == "event" and m.payload.phase == "start" for m in g))
        assert ok
        assert any(m.kind == "camera" and m.payload is True for m in got)
        status = [m.payload for m in got if m.kind == "status"][-1]
        assert status.gesture == "OPEN_PALM" and status.hand and status.confidence == pytest.approx(0.95)
        assert status.fps > 0
        starts = [m.payload for m in got if m.kind == "event" and m.payload.phase == "start"]
        assert starts[0].name == "OPEN_PALM"
    finally:
        eng.shutdown()


def test_turning_gestures_off_releases_the_camera(tmp_path):
    eng, events = make_engine(tmp_path)
    eng.start(); eng.set_enabled(True)
    try:
        assert wait_for(lambda: FakeCamera.instances and FakeCamera.instances[0].opened)
        eng.set_enabled(False)
        assert wait_for(lambda: FakeCamera.instances[0].closed)           # really released
        ok, got = collect(events, lambda g: any(m.kind == "camera" and m.payload is False for m in g))
        assert ok
    finally:
        eng.shutdown()


def test_camera_slows_down_without_a_hand_and_speeds_up_at_once_when_one_appears(tmp_path, monkeypatch):
    import gestures.engine as ge
    monkeypatch.setattr(ge, "IDLE_AFTER_SECONDS", 0.2)
    tracker = FakeTracker(name=None)
    eng, events = make_engine(tmp_path, tracker=tracker, settings={"camera_fps": 12})
    eng.start(); eng.set_enabled(True)
    try:
        assert wait_for(lambda: FakeCamera.instances and getattr(FakeCamera.instances[0], "fps_calls", []) == [ge.IDLE_FPS])
        ok, got = collect(events, lambda g: any(m.kind == "status" and m.payload.idle for m in g))
        assert ok                                           # the UI is told it is in idle mode
        tracker.name = "OPEN_PALM"                          # a hand appears
        assert wait_for(lambda: FakeCamera.instances[0].fps_calls == [ge.IDLE_FPS, 12.0])
        ok, got = collect(events, lambda g: any(m.kind == "status" and m.payload.hand and not m.payload.idle for m in g))
        assert ok
        tracker.name = None                                 # and leaves again
        assert wait_for(lambda: FakeCamera.instances[0].fps_calls[-1] == ge.IDLE_FPS)
    finally:
        eng.shutdown()


def test_a_visible_hand_never_triggers_idle_mode(tmp_path, monkeypatch):
    import gestures.engine as ge
    monkeypatch.setattr(ge, "IDLE_AFTER_SECONDS", 0.1)
    eng, events = make_engine(tmp_path, tracker=FakeTracker("PEACE"))
    eng.start(); eng.set_enabled(True)
    try:
        time.sleep(0.8)
        assert getattr(FakeCamera.instances[0], "fps_calls", []) == []
    finally:
        eng.shutdown()


def test_enabling_an_already_running_camera_does_not_restart_it(tmp_path):
    eng, events = make_engine(tmp_path)
    eng.start(); eng.set_enabled(True)
    try:
        assert wait_for(lambda: FakeCamera.instances and FakeCamera.instances[0].opened)
        for _ in range(5):                                   # e.g. Hybrid -> Gesture Only keeps the camera on
            eng.set_enabled(True)
        time.sleep(0.4)
        assert len(FakeCamera.instances) == 1 and not FakeCamera.instances[0].closed
        eng.set_enabled(False)
        assert wait_for(lambda: FakeCamera.instances[0].closed)
        eng.set_enabled(True)                                # a real off -> on change opens a new one
        assert wait_for(lambda: len(FakeCamera.instances) == 2 and FakeCamera.instances[1].opened)
    finally:
        eng.shutdown()


def test_shutdown_releases_the_camera(tmp_path):
    eng, events = make_engine(tmp_path)
    eng.start(); eng.set_enabled(True)
    assert wait_for(lambda: FakeCamera.instances and FakeCamera.instances[0].opened)
    eng.shutdown()
    assert FakeCamera.instances[0].closed


def test_quitting_while_the_tracker_is_still_loading_still_releases_the_camera(tmp_path):
    release = threading.Event()

    def slow_tracker(s):
        release.wait(6)                                  # MediaPipe taking its time to load
        return FakeTracker()
    store = ConfigStore(data_dir=tmp_path / "d"); store.ensure_initialized()
    events = queue.Queue()
    FakeCamera.instances.clear()
    eng = GestureEngine(store, lambda: GESTURES, events, camera_factory=lambda s: FakeCamera(s),
                        tracker_factory=slow_tracker)
    eng.start(); eng.set_enabled(True)
    assert wait_for(lambda: FakeCamera.instances and FakeCamera.instances[0].opened)
    t = time.time()
    eng.shutdown()                                       # the thread is stuck: join times out after 3 s
    assert time.time() - t < 4.5
    assert FakeCamera.instances[0].closed                # closed from shutdown(), not left open
    release.set()
    time.sleep(0.2)
    assert not [m for m in drain(events) if m.kind == "error"]      # and no bogus error message


def test_missing_camera_is_reported_not_raised(tmp_path):
    eng, events = make_engine(tmp_path, camera=lambda s: FakeCamera(s, fail_open=True))
    eng.start(); eng.set_enabled(True)
    try:
        ok, got = collect(events, lambda g: any(m.kind == "error" for m in g))
        assert ok and "could not be opened" in [m.payload for m in got if m.kind == "error"][0]
        time.sleep(0.4)
        assert len([m for m in drain(events) if m.kind == "error"]) == 0   # no error spam
    finally:
        eng.shutdown()


def test_camera_dying_midway_is_reported(tmp_path):
    eng, events = make_engine(tmp_path, camera=lambda s: FakeCamera(s, fail_after=10))
    eng.start(); eng.set_enabled(True)
    try:
        ok, got = collect(events, lambda g: any(m.kind == "error" for m in g))
        assert ok
        assert wait_for(lambda: FakeCamera.instances[0].closed)
    finally:
        eng.shutdown()


def test_missing_hand_model_is_reported(tmp_path):
    store = ConfigStore(data_dir=tmp_path / "d"); store.ensure_initialized()
    events = queue.Queue()

    def bad_tracker(s):
        raise HandModelMissingError("The hand model is missing. Run: python scripts/download_models.py --hand")
    eng = GestureEngine(store, lambda: GESTURES, events, camera_factory=lambda s: FakeCamera(s),
                        tracker_factory=bad_tracker)
    eng.start(); eng.set_enabled(True)
    try:
        ok, got = collect(events, lambda g: any(m.kind == "error" for m in g))
        assert ok and "hand model" in [m.payload for m in got if m.kind == "error"][0]
        assert wait_for(lambda: FakeCamera.instances and FakeCamera.instances[-1].closed)
    finally:
        eng.shutdown()


def test_hand_setting_ignores_the_other_hand(tmp_path):
    eng, events = make_engine(tmp_path, tracker=FakeTracker(label="Left"), settings={"hand": "Right"})
    eng.start(); eng.set_enabled(True)
    try:
        ok, got = collect(events, lambda g: len([m for m in g if m.kind == "status"]) > 8)
        assert ok
        assert all(m.payload.gesture == "NONE" for m in got if m.kind == "status")
        assert not [m for m in got if m.kind == "event"]
    finally:
        eng.shutdown()


def test_low_confidence_frames_are_ignored(tmp_path):
    eng, events = make_engine(tmp_path, tracker=FakeTracker(score=0.5))
    eng.start(); eng.set_enabled(True)
    try:
        ok, got = collect(events, lambda g: len([m for m in g if m.kind == "status"]) > 8)
        assert ok and all(m.payload.gesture == "NONE" for m in got if m.kind == "status")
        assert not [m for m in got if m.kind == "event"]
    finally:
        eng.shutdown()


def test_no_hand_gives_no_events(tmp_path):
    eng, events = make_engine(tmp_path, tracker=FakeTracker(name=None))
    eng.start(); eng.set_enabled(True)
    try:
        ok, got = collect(events, lambda g: len([m for m in g if m.kind == "status"]) > 8)
        assert ok and not any(m.payload.hand for m in got if m.kind == "status")
        assert not [m for m in got if m.kind == "event"]
    finally:
        eng.shutdown()
