import pytest

from gestures.face import (
    CALIBRATION_SAMPLES, WINK_LEFT, WINK_RIGHT, EyeBaseline, FaceProcessor, FaceThresholds,
    classify_wink, compute_face_features, eye_aspect_ratio, EYE_A,
)
from tests.face_model import CLOSED_EAR, OPEN_EAR, detection, make_face

SETTINGS = {"stable_frames": 5, "face_hold_seconds": 0.5, "face_swap_eyes": False, "cooldown_seconds": 1.0,
            "movement_tolerance": 0.15}
FPS = 12
DT = 1 / FPS


# ---------------------------------------------------------------- eye aspect ratio / sides
def test_eye_aspect_ratio_of_open_and_closed_eyes():
    f = compute_face_features(make_face())
    assert f.right_ear == pytest.approx(OPEN_EAR, abs=1e-6) and f.left_ear == pytest.approx(OPEN_EAR, abs=1e-6)
    f = compute_face_features(make_face(CLOSED_EAR, OPEN_EAR))
    assert f.right_ear == pytest.approx(CLOSED_EAR, abs=1e-6) and f.left_ear == pytest.approx(OPEN_EAR, abs=1e-6)


def test_right_eye_is_decided_by_position_not_by_landmark_index():
    for a_is_right in (True, False):                       # either index group may sit on the right
        f = compute_face_features(make_face(0.05, 0.30, a_is_right=a_is_right))
        assert f.right_ear < 0.1 < f.left_ear


def test_swap_flips_left_and_right():
    f = compute_face_features(make_face(0.05, 0.30), swap=True)
    assert f.left_ear < 0.1 < f.right_ear


def test_wide_frames_are_handled_with_the_aspect_ratio():
    aspect = 640 / 360
    f1 = compute_face_features(make_face(0.05, 0.30))
    f2 = compute_face_features(make_face(0.05, 0.30, aspect=aspect), aspect=aspect)
    assert f2.right_ear == pytest.approx(f1.right_ear, abs=1e-6) and f2.left_ear == pytest.approx(f1.left_ear, abs=1e-6)
    assert f2.face_size == pytest.approx(f1.face_size, abs=1e-6)


def test_eye_aspect_ratio_never_divides_by_zero():
    pts = [[0.5, 0.5, 0.0]] * 478
    assert eye_aspect_ratio(pts, EYE_A) == 0.0


def test_incomplete_landmarks_are_ignored():
    assert compute_face_features([[0.5, 0.5, 0.0]] * 100) is None
    assert compute_face_features([]) is None


# ---------------------------------------------------------------- baseline and wink rules
def test_wink_rules():
    assert classify_wink(0.2, 1.0) == WINK_RIGHT
    assert classify_wink(1.0, 0.2) == WINK_LEFT
    assert classify_wink(1.0, 1.0) == "NONE"            # eyes open
    assert classify_wink(0.2, 0.2) == "NONE"            # a blink / both eyes closed is not a wink
    assert classify_wink(0.7, 1.0) == "NONE"            # not closed enough
    assert classify_wink(0.2, 0.7) == "NONE"            # the other eye must stay clearly open (squint)
    t = FaceThresholds()
    assert (t.closed_ratio, t.open_ratio) == (0.55, 0.80)


def test_baseline_needs_a_moment_to_learn():
    b = EyeBaseline()
    for _ in range(CALIBRATION_SAMPLES - 1):
        b.update(0.3, 0.3)
    assert not b.calibrated
    b.update(0.3, 0.3)
    assert b.calibrated


def test_narrow_and_wide_eyes_both_work():
    for open_ear in (0.17, 0.30, 0.38):
        b = EyeBaseline()
        for _ in range(40):
            b.update(open_ear, open_ear)
        r, l = b.ratios(open_ear * 0.3, open_ear)
        assert r < 0.5 and l == pytest.approx(1.0, abs=0.01)


def test_blinks_do_not_pull_the_reference_down():
    b = EyeBaseline()
    for i in range(80):
        ear = 0.05 if i % 20 in (5, 6) else 0.30          # a blink every ~1.7 s
        b.update(ear, ear)
    r, l = b.ratios(0.30, 0.30)
    assert r == pytest.approx(1.0, abs=0.05)


def test_eyes_held_shut_from_the_start_never_count_as_a_wink():
    b = EyeBaseline()
    for _ in range(40):
        b.update(0.04, 0.30)                              # right eye closed since the beginning
    assert b.ratios(0.04, 0.30) == (1.0, pytest.approx(1.0, abs=0.01))


# ---------------------------------------------------------------- the whole processor
class Clock:
    def __init__(self): self.t = 0.0
    def __call__(self): return self.t


class Rig:
    def __init__(self, settings=None):
        self.clock = Clock()
        self.p = FaceProcessor(dict(SETTINGS, **(settings or {})), self.clock)
        self.log = []
        self.fields = {}

    def frames(self, seconds, right=OPEN_EAR, left=OPEN_EAR, face=True):
        for _ in range(max(1, round(seconds * FPS))):
            events, self.fields = self.p.process(detection(right, left) if face else None)
            for e in events:
                self.log.append((e.name, e.phase))
            self.clock.t += DT

    def learn(self):
        self.frames(1.5)


def test_status_while_learning_and_after():
    r = Rig()
    r.frames(0.3)
    assert r.fields["calibrating"] is True and r.fields["face"] is True
    r.learn()
    assert r.fields["calibrating"] is False and r.fields["right_eye"] == pytest.approx(1.0, abs=0.01)


def test_closing_the_right_eye_triggers_wink_right_once_after_the_hold():
    r = Rig()
    r.learn()
    r.frames(0.3, right=CLOSED_EAR)
    assert r.log == []                                    # held for less than 0.5 s: nothing yet
    r.frames(0.6, right=CLOSED_EAR)
    assert r.log == [(WINK_RIGHT, "start")]
    r.frames(3.0, right=CLOSED_EAR)
    assert r.log == [(WINK_RIGHT, "start")]               # keeping it closed does not repeat
    r.frames(0.5)
    assert r.log[-1] == (WINK_RIGHT, "end")


def test_closing_the_left_eye_triggers_wink_left():
    r = Rig()
    r.learn()
    r.frames(1.0, left=CLOSED_EAR)
    assert r.log == [(WINK_LEFT, "start")]


def test_normal_blinking_never_triggers():
    r = Rig()
    r.learn()
    for _ in range(10):
        r.frames(0.17, right=CLOSED_EAR, left=CLOSED_EAR)  # a blink (both eyes), about 2 frames
        r.frames(0.6)
    assert r.log == []


def test_both_eyes_closed_for_a_long_time_is_not_a_wink():
    r = Rig()
    r.learn()
    r.frames(3.0, right=CLOSED_EAR, left=CLOSED_EAR)
    assert r.log == []


def test_a_quick_wink_shorter_than_the_hold_is_ignored():
    r = Rig()
    r.learn()
    r.frames(0.33, right=CLOSED_EAR)
    r.frames(1.0)
    assert r.log == []


def test_two_winks_in_a_row_each_trigger_once():
    r = Rig()
    r.learn()
    r.frames(0.8, right=CLOSED_EAR)
    r.frames(1.0)
    r.frames(0.8, right=CLOSED_EAR)
    assert [e for e in r.log if e[1] == "start"] == [(WINK_RIGHT, "start")] * 2


def test_hold_time_comes_from_the_setting():
    r = Rig({"face_hold_seconds": 1.5})
    r.learn()
    r.frames(1.0, right=CLOSED_EAR)
    assert r.log == []
    r.frames(1.0, right=CLOSED_EAR)
    assert r.log == [(WINK_RIGHT, "start")]


def test_swap_setting_exchanges_the_eyes():
    r = Rig({"face_swap_eyes": True})
    r.learn()
    r.frames(1.0, right=CLOSED_EAR)
    assert r.log == [(WINK_LEFT, "start")]


def test_no_face_means_no_events_and_no_false_wink():
    r = Rig()
    r.learn()
    r.frames(2.0, face=False)
    assert r.log == [] and r.fields == {"face": False}
    r.frames(1.0, right=CLOSED_EAR)                        # the face is back with one eye closed
    assert r.log == [(WINK_RIGHT, "start")]


def test_losing_the_face_during_a_wink_ends_it():
    r = Rig()
    r.learn()
    r.frames(1.0, right=CLOSED_EAR)
    r.frames(0.5, face=False)
    assert r.log == [(WINK_RIGHT, "start"), (WINK_RIGHT, "end")]


def test_bars_follow_the_eyes():
    r = Rig()
    r.learn()
    r.frames(0.2, right=CLOSED_EAR)
    assert r.fields["right_eye"] < 0.3 and r.fields["left_eye"] > 0.9
