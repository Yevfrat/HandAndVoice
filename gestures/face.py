"""Face Only mode: eye openness and wink detection from MediaPipe's face landmarks.

Everything is computed from the 478 landmarks (no blendshape model): it is cheaper and the
left/right decision does not depend on how a model names its outputs.

Left/right: the frame is mirrored like a selfie camera, so YOUR right eye is the one on the
RIGHT side of the picture. The side is decided from the landmark positions, not from indices.
"""
from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass
from typing import Callable, Optional, Sequence

import numpy as np

from gestures.stabilizer import GestureStabilizer
from gestures.types import GestureEvent

# MediaPipe face-mesh points of one eye in the usual EAR order: p1 outer corner, p2, p3 upper lid,
# p4 inner corner, p5, p6 lower lid.
EYE_A = (33, 160, 158, 133, 153, 144)     # the eye on the picture's left side in a normal (un-mirrored) face
EYE_B = (362, 385, 387, 263, 373, 380)
MIN_LANDMARKS = 400

WINK_RIGHT = "WINK_RIGHT"
WINK_LEFT = "WINK_LEFT"
FACE_GESTURES = (WINK_RIGHT, WINK_LEFT)

BASELINE_SECONDS = 8.0       # how long the "eye fully open" reference is remembered
CALIBRATION_SAMPLES = 10     # about 1 s: before this no wink is reported
MIN_BASELINE_EAR = 0.12      # a reference below this is not a real open eye (eyes held shut)


@dataclass
class FaceThresholds:
    closed_ratio: float = 0.55    # an eye counts as closed below 55 % of its normal opening
    open_ratio: float = 0.80      # the other eye must stay above 80 % to count as a wink


@dataclass
class FaceFeatures:
    right_ear: float              # eye aspect ratio of YOUR right eye (about 0.30 open, 0.05 closed)
    left_ear: float
    face_size: float


def _pt(landmarks: Sequence, i: int, aspect: float) -> np.ndarray:
    p = landmarks[i]
    x, y = (p.x, p.y) if hasattr(p, "x") else (p[0], p[1])
    return np.array([float(x) * aspect, float(y)])


def eye_aspect_ratio(landmarks: Sequence, idx: tuple, aspect: float = 1.0) -> float:
    p = [_pt(landmarks, i, aspect) for i in idx]
    width = np.linalg.norm(p[0] - p[3])
    if width < 1e-6:
        return 0.0
    return float((np.linalg.norm(p[1] - p[5]) + np.linalg.norm(p[2] - p[4])) / (2.0 * width))


def compute_face_features(landmarks: Sequence, aspect: float = 1.0, swap: bool = False) -> Optional[FaceFeatures]:
    """`swap` flips left and right (for a webcam whose driver already mirrors the picture)."""
    if len(landmarks) < MIN_LANDMARKS:
        return None
    ear_a = eye_aspect_ratio(landmarks, EYE_A, aspect)
    ear_b = eye_aspect_ratio(landmarks, EYE_B, aspect)
    x_a = float(np.mean([_pt(landmarks, i, 1.0)[0] for i in EYE_A]))
    x_b = float(np.mean([_pt(landmarks, i, 1.0)[0] for i in EYE_B]))
    # mirrored frame: the eye further to the right of the picture is YOUR right eye
    right_ear, left_ear = (ear_a, ear_b) if x_a > x_b else (ear_b, ear_a)
    if swap:
        right_ear, left_ear = left_ear, right_ear
    top, chin = _pt(landmarks, 10, aspect), _pt(landmarks, 152, aspect)
    return FaceFeatures(right_ear, left_ear, float(np.linalg.norm(top - chin)))


class EyeBaseline:
    """Learns how wide each eye is open, so narrow and wide eyes both work (adapts over time)."""

    def __init__(self, frames: int = 96):
        self._right: deque[float] = deque(maxlen=frames)
        self._left: deque[float] = deque(maxlen=frames)

    def update(self, right_ear: float, left_ear: float) -> None:
        self._right.append(right_ear)
        self._left.append(left_ear)

    @property
    def calibrated(self) -> bool:
        return min(len(self._right), len(self._left)) >= CALIBRATION_SAMPLES

    def reference(self, samples: deque) -> float:
        # a high percentile = the "fully open" level, ignoring blinks and winks
        return float(np.percentile(np.array(samples), 90)) if samples else 0.0

    def ratios(self, right_ear: float, left_ear: float) -> tuple[float, float]:
        r_ref, l_ref = self.reference(self._right), self.reference(self._left)
        r = min(1.5, right_ear / r_ref) if r_ref >= MIN_BASELINE_EAR else 1.0
        l = min(1.5, left_ear / l_ref) if l_ref >= MIN_BASELINE_EAR else 1.0
        return r, l


def classify_wink(right_ratio: float, left_ratio: float, t: Optional[FaceThresholds] = None) -> str:
    """WINK_RIGHT / WINK_LEFT when exactly one eye is closed; a blink (both) or open eyes: NONE."""
    t = t or FaceThresholds()
    if right_ratio <= t.closed_ratio and left_ratio >= t.open_ratio:
        return WINK_RIGHT
    if left_ratio <= t.closed_ratio and right_ratio >= t.open_ratio:
        return WINK_LEFT
    return "NONE"


class FaceProcessor:
    """One camera frame in, status and wink events out (used by the gesture engine)."""

    def __init__(self, settings: dict, clock: Callable[[], float] = time.monotonic):
        self.settings = settings
        hold = float(settings.get("face_hold_seconds", 0.5))
        config = {name: {"hold_seconds": hold, "enabled": True} for name in FACE_GESTURES}
        stab_settings = dict(settings)
        stab_settings["cooldown_seconds"] = 0.6          # winks may follow each other quickly
        self.stabilizer = GestureStabilizer(stab_settings, config, clock)
        self.baseline = EyeBaseline()
        self.thresholds = FaceThresholds()
        self.swap = bool(settings.get("face_swap_eyes", False))

    def process(self, det) -> tuple[list[GestureEvent], dict]:
        """`det` is a FaceDetection or None. Returns (events, fields for GestureStatus)."""
        feats = compute_face_features(det.landmarks, det.aspect, self.swap) if det is not None else None
        if feats is None:
            return self.stabilizer.update("NONE"), {"face": False}
        self.baseline.update(feats.right_ear, feats.left_ear)
        r, l = self.baseline.ratios(feats.right_ear, feats.left_ear)
        calibrating = not self.baseline.calibrated
        name = "NONE" if calibrating else classify_wink(r, l, self.thresholds)
        events = self.stabilizer.update(name, None, None)
        st = self.stabilizer.status
        return events, {
            "face": True, "gesture": name, "right_eye": max(0.0, min(1.0, r)),
            "left_eye": max(0.0, min(1.0, l)), "calibrating": calibrating,
            "progress": st.progress, "started": st.started,
        }
