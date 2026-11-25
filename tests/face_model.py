"""Synthetic face landmarks (478 points) for tests. Only the points the code reads are meaningful:
the two eyes (6 points each) and the forehead / chin used for the face size.

These are NOT recordings of a real face; real-face behaviour is checked by the user on the
Face Expressions page (live eye bars).
"""
from __future__ import annotations

from types import SimpleNamespace

from gestures.face import EYE_A, EYE_B

EYE_WIDTH = 0.06
OPEN_EAR = 0.30
CLOSED_EAR = 0.05


def _eye(cx: float, cy: float, ear: float, width: float = EYE_WIDTH):
    """Six points in the order p1..p6 of gestures.face (outer, upper x2, inner, lower x2)."""
    h = ear * width
    return [
        (cx - width / 2, cy), (cx - width / 6, cy - h / 2), (cx + width / 6, cy - h / 2),
        (cx + width / 2, cy), (cx + width / 6, cy + h / 2), (cx - width / 6, cy + h / 2),
    ]


def make_face(right_ear: float = OPEN_EAR, left_ear: float = OPEN_EAR, *, a_is_right: bool = True,
              aspect: float = 1.0, eye_width: float = EYE_WIDTH):
    """Landmarks of a face seen in a MIRRORED (selfie) picture: your RIGHT eye is on the right side.

    `a_is_right` decides which MediaPipe index group sits on that side; the code must not care.
    """
    pts = [[0.5, 0.5, 0.0] for _ in range(478)]
    pts[10] = [0.5, 0.30, 0.0]          # forehead
    pts[152] = [0.5, 0.75, 0.0]         # chin
    right_pts = _eye(0.58, 0.45, right_ear, eye_width)       # larger x = your right eye
    left_pts = _eye(0.42, 0.45, left_ear, eye_width)
    group_right, group_left = (EYE_A, EYE_B) if a_is_right else (EYE_B, EYE_A)
    for idx, p in zip(group_right, right_pts):
        pts[idx] = [p[0], p[1], 0.0]
    for idx, p in zip(group_left, left_pts):
        pts[idx] = [p[0], p[1], 0.0]
    if aspect != 1.0:                                        # MediaPipe divides x by the frame aspect ratio
        for p in pts:
            p[0] /= aspect
    return pts


def detection(right_ear: float = OPEN_EAR, left_ear: float = OPEN_EAR, aspect: float = 1.0, **kw):
    return SimpleNamespace(landmarks=make_face(right_ear, left_ear, aspect=aspect, **kw), aspect=aspect)
