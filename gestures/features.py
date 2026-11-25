"""Hand features from MediaPipe landmarks (spec 4.1).

All distances are divided by hand_size (wrist to middle-finger MCP), so the result does
not depend on how far the hand is from the camera.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence

import numpy as np

# MediaPipe landmark indices
WRIST = 0
THUMB_MCP, THUMB_TIP = 2, 4
INDEX_MCP, INDEX_PIP, INDEX_TIP = 5, 6, 8
MIDDLE_MCP, MIDDLE_PIP, MIDDLE_TIP = 9, 10, 12
RING_MCP, RING_PIP, RING_TIP = 13, 14, 16
PINKY_MCP, PINKY_PIP, PINKY_TIP = 17, 18, 20

FINGERS = {
    "index": (INDEX_PIP, INDEX_TIP),
    "middle": (MIDDLE_PIP, MIDDLE_TIP),
    "ring": (RING_PIP, RING_TIP),
    "pinky": (PINKY_PIP, PINKY_TIP),
}


@dataclass
class Thresholds:
    """The numbers from the spec. They live here so they can be tuned later."""
    finger_ratio: float = 1.1     # finger extended: |tip-wrist| > ratio * |pip-wrist|
    thumb_extended: float = 0.8   # |thumb tip - index MCP| > 0.8 * hand_size
    thumb_direction: float = 0.25 # thumb up/down: vertical offset > 0.25 * hand_size
    index_direction: float = 0.6  # index direction component beyond 0.6
    ok_pinch: float = 0.25        # |thumb tip - index tip| < 0.25 * hand_size


@dataclass
class HandFeatures:
    hand_size: float
    fingers: dict[str, bool]          # index / middle / ring / pinky extended?
    thumb_extended: bool
    thumb_direction: str              # "up" | "down" | "side"
    index_direction: str              # "up" | "down" | "left" | "right" | "none"
    pinch: float                      # |thumb tip - index tip| / hand_size
    wrist: tuple[float, float]        # for the stabilizer's movement tolerance


def _xy(p) -> tuple[float, float]:
    if hasattr(p, "x"):
        return float(p.x), float(p.y)
    return float(p[0]), float(p[1])


def compute_features(landmarks: Sequence, aspect: float = 1.0,
                     t: Optional[Thresholds] = None) -> Optional[HandFeatures]:
    """`aspect` = frame width / height. MediaPipe's x and y are normalized separately, so
    x is scaled by the aspect ratio to make distances equal in both directions."""
    t = t or Thresholds()
    if len(landmarks) < 21:
        return None
    pts = np.array([_xy(p) for p in landmarks[:21]], dtype=float)
    pts[:, 0] *= aspect

    def dist(a: int, b: int) -> float:
        return float(np.linalg.norm(pts[a] - pts[b]))

    hand_size = dist(WRIST, MIDDLE_MCP)
    if hand_size < 1e-6:
        return None

    fingers = {name: dist(tip, WRIST) > t.finger_ratio * dist(pip, WRIST)
               for name, (pip, tip) in FINGERS.items()}
    thumb_extended = dist(THUMB_TIP, INDEX_MCP) > t.thumb_extended * hand_size

    dy = pts[THUMB_TIP][1] - pts[THUMB_MCP][1]          # image y grows downwards
    if dy < -t.thumb_direction * hand_size:
        thumb_direction = "up"
    elif dy > t.thumb_direction * hand_size:
        thumb_direction = "down"
    else:
        thumb_direction = "side"

    vec = pts[INDEX_TIP] - pts[INDEX_MCP]
    norm = float(np.linalg.norm(vec))
    index_direction = "none"
    if norm > 1e-9:
        ux, uy = vec / norm
        if uy < -t.index_direction:
            index_direction = "up"
        elif uy > t.index_direction:
            index_direction = "down"
        elif ux > t.index_direction:
            index_direction = "right"
        elif ux < -t.index_direction:
            index_direction = "left"

    return HandFeatures(
        hand_size=hand_size, fingers=fingers, thumb_extended=thumb_extended,
        thumb_direction=thumb_direction, index_direction=index_direction,
        pinch=dist(THUMB_TIP, INDEX_TIP) / hand_size,
        wrist=(float(pts[WRIST][0]), float(pts[WRIST][1])),
    )
