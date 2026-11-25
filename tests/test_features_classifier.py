import json
import random
from pathlib import Path

import pytest

from gestures.classifier import classify, classify_landmarks
from gestures.features import Thresholds, compute_features
from gestures.types import Gesture
from tests.hand_model import SPECS, build, to_frame

FIXTURES = Path(__file__).parent / "fixtures" / "landmarks"
REAL = [n for n in SPECS if not n.startswith("NONE")]
NEGATIVE = [n for n in SPECS if n.startswith("NONE")]


def load(name):
    return json.loads((FIXTURES / f"{name}.json").read_text())["landmarks"]


def test_all_14_gestures_have_fixtures():
    assert len(REAL) == 14
    assert {g.value for g in Gesture} - {"NONE"} == set(REAL)


def test_fixtures_are_up_to_date_with_the_hand_model():
    for name in SPECS:
        assert load(name) == build(name), f"run scripts/make_landmark_fixtures.py ({name})"


@pytest.mark.parametrize("name", REAL)
def test_each_gesture_is_recognized(name):
    assert classify_landmarks(load(name)).value == name


@pytest.mark.parametrize("name", NEGATIVE)
def test_unrecognized_poses_give_none(name):
    assert classify_landmarks(load(name)) == Gesture.NONE


@pytest.mark.parametrize("name", REAL)
@pytest.mark.parametrize("scale", [0.4, 1.0, 2.5])
def test_result_does_not_depend_on_distance_to_the_camera(name, scale):
    pts = to_frame(load(name), scale=scale, dx=0.4, dy=0.55)
    assert classify_landmarks(pts).value == name


@pytest.mark.parametrize("name", REAL)
def test_wide_frames_are_handled_with_the_aspect_ratio(name):
    aspect = 640 / 360
    pts = to_frame(load(name), scale=0.8, aspect=aspect)       # MediaPipe-style normalized x
    assert classify_landmarks(pts, aspect=aspect).value == name


@pytest.mark.parametrize("name", REAL)
def test_small_landmark_jitter_does_not_change_the_result(name):
    rng = random.Random(7)
    base = load(name)
    for _ in range(60):
        noisy = [[x + rng.uniform(-0.004, 0.004), y + rng.uniform(-0.004, 0.004), z] for x, y, z in base]
        assert classify_landmarks(noisy).value == name


def test_feature_values():
    f = compute_features(load("THUMBS_UP"))
    assert f.hand_size == pytest.approx(0.18, abs=1e-6)
    assert not any(f.fingers.values()) and f.thumb_extended and f.thumb_direction == "up"
    assert compute_features(load("THUMBS_DOWN")).thumb_direction == "down"
    assert compute_features(load("OPEN_PALM")).fingers == {"index": True, "middle": True, "ring": True, "pinky": True}
    assert compute_features(load("POINT_RIGHT")).index_direction == "right"
    assert compute_features(load("POINT_LEFT")).index_direction == "left"
    assert compute_features(load("POINT_UP")).index_direction == "up"
    assert compute_features(load("POINT_DOWN")).index_direction == "down"
    assert compute_features(load("OK")).pinch < 0.25
    assert compute_features(load("FIST")).pinch > 0.25


def test_rules_use_the_numbers_from_the_spec():
    t = Thresholds()
    assert (t.finger_ratio, t.thumb_extended, t.thumb_direction, t.index_direction, t.ok_pinch) == (
        1.1, 0.8, 0.25, 0.6, 0.25)


def test_mediapipe_landmark_objects_are_accepted():
    from types import SimpleNamespace
    objs = [SimpleNamespace(x=x, y=y, z=z) for x, y, z in load("PEACE")]
    assert classify_landmarks(objs) == Gesture.PEACE


def test_bad_input_never_crashes_or_triggers():
    assert compute_features([]) is None and classify(None) == Gesture.NONE
    assert compute_features([[0.5, 0.5, 0.0]] * 21) is None          # all points identical
    assert classify_landmarks([[0.5, 0.5, 0.0]] * 21) == Gesture.NONE
