import time

import pytest

from gestures import camera as camera_module
from gestures.camera import Camera, CameraError


class FakeCap:
    """Stands in for cv2.VideoCapture. grab() raises: the camera must never spin on it."""
    def __init__(self, fail=False):
        self.reads, self.fail, self.released = 0, fail, False

    def grab(self):
        raise AssertionError("grab() returns instantly with DirectShow: a grab loop burns 100% CPU")

    def read(self):
        self.reads += 1
        return (False, None) if self.fail else (True, object())

    def release(self):
        self.released = True


def make(fps, cap=None):
    cam = Camera(0, 640, 360, fps)
    cam._cap = cap or FakeCap()
    return cam


def test_reads_are_paced_not_spun():
    cam = make(20)
    t, frames = time.perf_counter(), 0
    while time.perf_counter() - t < 0.5:
        if cam.read_due() is not None:
            frames += 1
    assert 8 <= frames <= 11                                  # about 20 FPS x 0.5 s, not thousands
    assert cam._cap.reads == frames


def test_process_cpu_stays_low_while_waiting_for_frames():
    cam = make(5)
    c0, t0 = time.process_time(), time.perf_counter()
    while time.perf_counter() - t0 < 0.6:
        cam.read_due()
    cpu = (time.process_time() - c0) / (time.perf_counter() - t0)
    assert cpu < 0.15                                         # a spin loop would be ~1.0


def test_the_rate_can_be_changed_on_the_fly():
    cam = make(20)
    cam.read_due()
    cam.set_fps(4)
    t = time.perf_counter()
    cam.read_due(); cam.read_due()
    assert time.perf_counter() - t >= 0.4                     # two frames at 4 FPS = 0.5 s
    cam.set_fps(0)                                            # nonsense is clamped, never divides by zero
    assert cam.interval == 1.0


def test_a_dead_camera_raises_after_a_couple_of_seconds(monkeypatch):
    monkeypatch.setattr(camera_module.time, "sleep", lambda s: None)
    cam = make(12, FakeCap(fail=True))
    with pytest.raises(CameraError, match="stopped delivering"):
        for _ in range(camera_module.MAX_FAILED_READS + 5):
            cam.read_due()
    assert cam._failures >= camera_module.MAX_FAILED_READS


def test_one_good_frame_resets_the_failure_count(monkeypatch):
    monkeypatch.setattr(camera_module.time, "sleep", lambda s: None)
    cap = FakeCap(fail=True)
    cam = make(12, cap)
    for _ in range(10):
        assert cam.read_due() is None
    cap.fail = False
    assert cam.read_due() is not None and cam._failures == 0


def test_close_releases_the_device_and_is_safe_twice():
    cam = make(12)
    cap = cam._cap
    cam.close(); cam.close()
    assert cap.released and not cam.is_open
    with pytest.raises(CameraError):
        cam.read_due()
