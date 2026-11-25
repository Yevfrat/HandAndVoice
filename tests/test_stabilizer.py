import pytest

from gestures.stabilizer import GestureStabilizer
from gestures.types import Gesture

SETTINGS = {"stable_frames": 5, "default_hold_seconds": 1.0, "sleep_hold_seconds": 2.0,
            "scroll_hold_seconds": 0.3, "cooldown_seconds": 1.0, "movement_tolerance": 0.15}
GESTURES = {
    "OPEN_PALM": {"hold_seconds": 1.0, "enabled": True},
    "FIST": {"hold_seconds": 2.0, "enabled": True},
    "POINT_UP": {"hold_seconds": 0.3, "enabled": True},
    "SHAKA": {"hold_seconds": 1.0, "enabled": True},
    "ROCK": {"hold_seconds": 1.0, "enabled": False},
    "PEACE": {"hold_seconds": 1.0, "enabled": True},
    "OK": {"hold_seconds": 1.0, "enabled": True},
}
FPS = 12
DT = 1 / FPS


class Rig:
    def __init__(self, settings=None, gestures=None):
        self.t = 0.0
        self.s = GestureStabilizer(SETTINGS if settings is None else settings,
                                   GESTURES if gestures is None else gestures, lambda: self.t)
        self.log = []

    def frames(self, gesture, seconds, wrist=(0.5, 0.5), size=0.18, jitter=0.0):
        n = max(1, round(seconds * FPS))
        for i in range(n):
            w = (wrist[0] + (jitter * i), wrist[1])
            for e in self.s.update(gesture, w, size):
                self.log.append((round(self.t, 2), e.name, e.phase))
            self.t += DT

    def phases(self, name=None):
        return [(n, p) for _, n, p in self.log if name in (None, n)]


def test_needs_five_stable_frames_and_the_hold_time():
    r = Rig()
    r.frames(Gesture.OPEN_PALM, 0.9)                       # 0.9 s: hold (1 s) not reached
    assert r.phases() == []
    r.frames(Gesture.OPEN_PALM, 0.3)
    assert r.phases() == [("OPEN_PALM", "start")]


def test_start_fires_once_per_hold():
    r = Rig()
    r.frames(Gesture.OPEN_PALM, 4.0)
    assert r.phases() == [("OPEN_PALM", "start")]
    r.frames(Gesture.NONE, 0.5)
    assert r.phases()[-1] == ("OPEN_PALM", "end")


def test_four_frames_are_not_enough_even_if_held_long():
    r = Rig(settings={**SETTINGS, "default_hold_seconds": 0.0}, gestures={})
    for _ in range(4):
        r.frames(Gesture.OPEN_PALM, DT)
    assert r.phases() == []
    r.frames(Gesture.OPEN_PALM, DT)
    assert r.phases() == [("OPEN_PALM", "start")]


def test_fist_needs_two_seconds():
    r = Rig()
    r.frames(Gesture.FIST, 1.9)
    assert r.phases() == []
    r.frames(Gesture.FIST, 0.3)
    assert r.phases() == [("FIST", "start")]


def test_hold_time_falls_back_to_settings_when_not_in_gestures_json():
    s = GestureStabilizer(SETTINGS, {}, lambda: 0.0)
    assert s.hold_seconds("FIST") == 2.0 and s.hold_seconds("POINT_UP") == 0.3
    assert s.hold_seconds("PEACE") == 1.0


def test_scroll_gesture_ticks_while_held_and_ends_on_release():
    r = Rig()
    r.frames(Gesture.POINT_UP, 1.0)
    names = r.phases("POINT_UP")
    assert names[0] == ("POINT_UP", "start") and names.count(("POINT_UP", "tick")) >= 5
    r.frames(Gesture.NONE, 0.2)
    assert r.phases("POINT_UP")[-1] == ("POINT_UP", "end")


def test_scroll_start_ignores_the_cooldown():
    r = Rig()
    r.frames(Gesture.POINT_UP, 1.0)
    r.frames(Gesture.NONE, 0.2)
    r.frames(Gesture.POINT_UP, 1.0)
    assert r.phases("POINT_UP").count(("POINT_UP", "start")) == 2


def test_changing_the_gesture_resets_the_hold():
    r = Rig()
    r.frames(Gesture.OPEN_PALM, 0.8)
    r.frames(Gesture.PEACE, 0.8)
    r.frames(Gesture.OPEN_PALM, 0.8)
    assert r.phases() == []                               # never held 1 s in a row


def test_a_single_wrong_frame_resets_the_hold():
    r = Rig()
    r.frames(Gesture.OPEN_PALM, 0.9)
    r.frames(Gesture.NONE, DT)
    r.frames(Gesture.OPEN_PALM, 0.9)
    assert r.phases() == []


def test_cooldown_blocks_the_same_gesture_for_one_second():
    s = dict(SETTINGS, stable_frames=1)
    r = Rig(settings=s, gestures={"OPEN_PALM": {"hold_seconds": 0.0, "enabled": True}})
    r.frames(Gesture.OPEN_PALM, DT)
    assert r.phases() == [("OPEN_PALM", "start")]
    r.frames(Gesture.NONE, DT)
    r.frames(Gesture.OPEN_PALM, DT)                       # re-held 0.17 s later: still cooling down
    assert r.phases().count(("OPEN_PALM", "start")) == 1
    r.t += 1.2
    r.frames(Gesture.NONE, DT)
    r.frames(Gesture.OPEN_PALM, DT)
    assert r.phases().count(("OPEN_PALM", "start")) == 2


def test_a_moving_hand_never_counts_as_a_hold():
    r = Rig()
    # the wrist drifts 0.05 per frame = far more than 0.15 x hand size (0.027)
    r.frames(Gesture.OPEN_PALM, 3.0, jitter=0.05)
    assert r.phases() == []


def test_small_drift_is_tolerated():
    r = Rig()
    r.frames(Gesture.OPEN_PALM, 1.5, jitter=0.0003)        # 0.0003 x 18 frames = 0.0054 < 0.027
    assert ("OPEN_PALM", "start") in r.phases()


def test_after_the_start_the_hand_may_move_and_nothing_repeats():
    r = Rig()
    r.frames(Gesture.OPEN_PALM, 1.3)
    assert r.phases() == [("OPEN_PALM", "start")]
    r.frames(Gesture.OPEN_PALM, 3.0, wrist=(0.9, 0.9))     # moved far away while still holding the pose
    assert r.phases() == [("OPEN_PALM", "start")]          # no end, and no second action


def test_continuous_gestures_report_the_wrist_for_volume_mode():
    r = Rig()
    events = []
    for i in range(14):                                          # hold still first: 1 s hold
        events += r.s.update(Gesture.SHAKA, (0.5, 0.6), 0.2)
        r.t += DT
    for i in range(12):                                          # then raise the hand
        events += r.s.update(Gesture.SHAKA, (0.5, 0.6 - i * 0.01), 0.2)
        r.t += DT
    ticks = [e for e in events if e.phase == "tick"]
    assert events[0].phase == "start" and ticks
    assert ticks[-1].y < ticks[0].y and ticks[0].size == 0.2     # the hand went up, y got smaller


def test_shaka_may_move_after_start_without_ending():
    r = Rig()
    for i in range(14):
        r.s.update(Gesture.SHAKA, (0.5, 0.9), 0.2)
        r.t += DT
    assert r.s.status.started
    for i in range(40):
        r.s.update(Gesture.SHAKA, (0.5, 0.9 - i * 0.02), 0.2)    # 0.02 per frame = way over the tolerance
        r.t += DT
    assert r.s.status.started and r.s.status.candidate == "SHAKA"


def test_hold_overrides_shorten_the_hold_inside_modes():
    r = Rig()
    r.s.hold_overrides["POINT_RIGHT"] = 0.4
    r.frames(Gesture.POINT_RIGHT, 0.6)
    assert ("POINT_RIGHT", "start") in r.phases()
    r2 = Rig()
    r2.frames(Gesture.POINT_RIGHT, 0.6)                    # normal mode: 1 s hold
    assert r2.phases() == []


def test_point_left_and_right_tick_while_held():
    r = Rig(gestures={"POINT_RIGHT": {"hold_seconds": 0.4, "enabled": True}})
    r.frames(Gesture.POINT_RIGHT, 2.0)
    assert r.phases("POINT_RIGHT").count(("POINT_RIGHT", "tick")) >= 5


def test_none_never_triggers_anything():
    r = Rig()
    r.frames(Gesture.NONE, 10.0)
    assert r.phases() == []


def test_disabled_gestures_are_recognized_but_never_fire():
    r = Rig()
    r.frames(Gesture.ROCK, 3.0)
    assert r.phases() == []
    assert r.s.status.candidate == "ROCK" and r.s.status.stable


def test_progress_bar_values():
    r = Rig()
    assert r.s.status.progress == 0.0
    r.frames(Gesture.OPEN_PALM, 0.3)                       # still unstable/early
    assert r.s.status.progress < 0.5
    r.frames(Gesture.OPEN_PALM, 0.4)
    assert 0.3 < r.s.status.progress < 0.9
    r.frames(Gesture.OPEN_PALM, 1.0)
    st = r.s.status
    assert st.progress == pytest.approx(1.0) and st.started


def test_reset_reports_an_end_for_a_running_gesture():
    r = Rig()
    r.frames(Gesture.OPEN_PALM, 1.5)
    events = r.s.reset()
    assert [(e.name, e.phase) for e in events] == [("OPEN_PALM", "end")]
    assert r.s.status.candidate == "NONE"
