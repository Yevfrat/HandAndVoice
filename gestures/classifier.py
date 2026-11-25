"""Rule-based static gesture classification (spec 4.2). NONE never triggers an action."""
from __future__ import annotations

from typing import Optional, Sequence

from gestures.features import HandFeatures, Thresholds, compute_features
from gestures.types import Gesture


def classify(f: Optional[HandFeatures], t: Optional[Thresholds] = None) -> Gesture:
    if f is None:
        return Gesture.NONE
    t = t or Thresholds()
    idx, mid, ring, pinky = (f.fingers[k] for k in ("index", "middle", "ring", "pinky"))
    thumb = f.thumb_extended
    n_extended = sum((idx, mid, ring, pinky))
    all_four_curled = n_extended == 0

    # OK first: a pinch also makes the thumb look "extended" and the index curled.
    if f.pinch < t.ok_pinch and mid and ring and pinky:
        return Gesture.OK
    if thumb and idx and mid and ring and pinky:
        return Gesture.OPEN_PALM
    if all_four_curled:
        if thumb and f.thumb_direction == "up":
            return Gesture.THUMBS_UP
        if thumb and f.thumb_direction == "down":
            return Gesture.THUMBS_DOWN
        if not thumb:
            return Gesture.FIST
        return Gesture.NONE
    if idx and mid and not ring and not pinky and not thumb:
        return Gesture.PEACE
    if idx and thumb and not mid and not ring and not pinky:
        return Gesture.INDEX_THUMB
    if idx and pinky and not mid and not ring:
        if thumb:
            return Gesture.ILY
        return Gesture.ROCK
    if thumb and pinky and not idx and not mid and not ring:
        return Gesture.SHAKA
    if idx and not mid and not ring and not pinky and not thumb:
        return {
            "up": Gesture.POINT_UP, "down": Gesture.POINT_DOWN,
            "right": Gesture.POINT_RIGHT, "left": Gesture.POINT_LEFT,
        }.get(f.index_direction, Gesture.NONE)
    return Gesture.NONE


def classify_landmarks(landmarks: Sequence, aspect: float = 1.0,
                       t: Optional[Thresholds] = None) -> Gesture:
    return classify(compute_features(landmarks, aspect, t), t)
