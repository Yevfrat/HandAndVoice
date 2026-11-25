"""A synthetic hand skeleton (21 MediaPipe landmarks) used to build test fixtures.

These are NOT recordings of a real hand. They are geometric models that satisfy the rules of
spec 4.1/4.2 with a safe margin, so the classifier can be tested without a camera. How well the
rules fit REAL hands is checked by the user on the Hand Gestures page (display-only phase).

Units: x to the right, y downwards (image coordinates), wrist at (0, 0),
hand_size = |wrist - middle MCP| = 0.18.
"""
from __future__ import annotations

import math

MCP = {"index": (-0.045, -0.17), "middle": (0.0, -0.18), "ring": (0.04, -0.17), "pinky": (0.075, -0.15)}
THUMB_CMC, THUMB_MCP = (-0.05, -0.04), (-0.08, -0.08)
SEG = (0.06, 0.035, 0.03)           # PIP, DIP, TIP segment lengths

# (IP, tip) of the thumb for each pose
THUMB_POSES = {
    "across": ((-0.05, -0.10), (-0.02, -0.12)),     # folded over the palm: not extended
    "out": ((-0.14, -0.10), (-0.20, -0.12)),        # sticking out to the side
    "up": ((-0.085, -0.20), (-0.08, -0.33)),
    "down": ((-0.085, 0.00), (-0.08, 0.13)),
    "side": ((-0.14, -0.08), (-0.20, -0.10)),       # fist with the thumb sideways
    "pinch": ((-0.09, -0.14), (-0.075, -0.21)),     # touching the index tip (OK)
}


def _rot(v, deg):
    a = math.radians(deg)
    return (v[0] * math.cos(a) - v[1] * math.sin(a), v[0] * math.sin(a) + v[1] * math.cos(a))


def _finger(mcp, extended: bool, tip_override=None):
    up = (0.0, -1.0)
    pip = (mcp[0] + SEG[0] * up[0], mcp[1] + SEG[0] * up[1])
    if extended:
        d = (mcp[0] + (SEG[0] + SEG[1]) * up[0], mcp[1] + (SEG[0] + SEG[1]) * up[1])
        t = (mcp[0] + sum(SEG) * up[0], mcp[1] + sum(SEG) * up[1])
    else:                                            # curled towards the palm
        s2, s3 = _rot(up, 120), _rot(up, 200)
        d = (pip[0] + SEG[1] * s2[0], pip[1] + SEG[1] * s2[1])
        t = (d[0] + SEG[2] * s3[0], d[1] + SEG[2] * s3[1])
    return [mcp, pip, d, tip_override or t]


# gesture -> (index, middle, ring, pinky extended?, thumb pose, hand rotation in degrees)
SPECS = {
    "THUMBS_UP": ((0, 0, 0, 0), "up", 0),
    "THUMBS_DOWN": ((0, 0, 0, 0), "down", 0),
    "FIST": ((0, 0, 0, 0), "across", 0),
    "OPEN_PALM": ((1, 1, 1, 1), "out", 0),
    "PEACE": ((1, 1, 0, 0), "across", 0),
    "INDEX_THUMB": ((1, 0, 0, 0), "out", 0),
    "POINT_UP": ((1, 0, 0, 0), "across", 0),
    "POINT_DOWN": ((1, 0, 0, 0), "across", 180),
    "POINT_RIGHT": ((1, 0, 0, 0), "across", 90),
    "POINT_LEFT": ((1, 0, 0, 0), "across", -90),
    "OK": ((0, 1, 1, 1), "pinch", 0),
    "ROCK": ((1, 0, 0, 1), "across", 0),
    "SHAKA": ((0, 0, 0, 1), "out", 0),
    "ILY": ((1, 0, 0, 1), "out", 0),
    # poses that must NOT trigger anything
    "NONE_three_fingers": ((1, 1, 1, 0), "across", 0),
    "NONE_pinky_only": ((0, 0, 0, 1), "across", 0),
    "NONE_middle_finger_only": ((0, 1, 0, 0), "across", 0),
    "NONE_index_middle_thumb": ((1, 1, 0, 0), "out", 0),
    "NONE_thumb_sideways_fist": ((0, 0, 0, 0), "side", 0),
    "NONE_ring_only": ((0, 0, 1, 0), "across", 0),
}


def build(name: str) -> list[list[float]]:
    """21 landmarks [x, y, z] in hand units (wrist at the origin)."""
    (idx, mid, ring, pinky), thumb, rotation = SPECS[name]
    pts = [(0.0, 0.0), THUMB_CMC, THUMB_MCP, *THUMB_POSES[thumb]]
    for key, ext in (("index", idx), ("middle", mid), ("ring", ring), ("pinky", pinky)):
        override = (-0.07, -0.22) if (key == "index" and thumb == "pinch") else None
        pts += _finger(MCP[key], bool(ext), override)
    pts = [_rot(p, rotation) if rotation else p for p in pts]
    return [[round(x, 5), round(y, 5), 0.0] for x, y in pts]


def to_frame(points, scale=1.0, dx=0.5, dy=0.7, aspect=1.0):
    """Place the hand in a frame like MediaPipe does: normalized x and y, where x is
    divided by the frame aspect ratio (width / height)."""
    return [[(p[0] * scale + dx) / aspect, p[1] * scale + dy, p[2]] for p in points]
