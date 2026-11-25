"""Shared gesture types."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Gesture(str, Enum):
    THUMBS_UP = "THUMBS_UP"
    THUMBS_DOWN = "THUMBS_DOWN"
    FIST = "FIST"
    OPEN_PALM = "OPEN_PALM"
    PEACE = "PEACE"
    INDEX_THUMB = "INDEX_THUMB"
    POINT_UP = "POINT_UP"
    POINT_DOWN = "POINT_DOWN"
    POINT_RIGHT = "POINT_RIGHT"
    POINT_LEFT = "POINT_LEFT"
    OK = "OK"
    ROCK = "ROCK"
    SHAKA = "SHAKA"
    ILY = "ILY"
    NONE = "NONE"


EMOJI = {
    Gesture.THUMBS_UP: "👍", Gesture.THUMBS_DOWN: "👎", Gesture.FIST: "✊", Gesture.OPEN_PALM: "✋",
    Gesture.PEACE: "✌️", Gesture.INDEX_THUMB: "👆", Gesture.POINT_UP: "☝️", Gesture.POINT_DOWN: "👇",
    Gesture.POINT_RIGHT: "👉", Gesture.POINT_LEFT: "👈", Gesture.OK: "👌", Gesture.ROCK: "🤘",
    Gesture.SHAKA: "🤙", Gesture.ILY: "🤟", Gesture.NONE: "",
}

# Gestures that keep producing "tick" events while they are held. Scroll and volume need them;
# POINT_RIGHT/LEFT use them to step repeatedly inside the Switch Window and Browser modes.
CONTINUOUS = {Gesture.POINT_UP, Gesture.POINT_DOWN, Gesture.SHAKA, Gesture.POINT_RIGHT, Gesture.POINT_LEFT}


@dataclass
class GestureEvent:
    name: str
    phase: str            # "start" (hold reached) | "tick" (continuous gestures) | "end" (released)
    x: float = 0.0        # wrist position of the frame (volume mode follows its movement)
    y: float = 0.0
    size: float = 0.0     # hand size of the frame


@dataclass
class GestureStatus:
    gesture: str = "NONE"        # what is recognized right now (per frame)
    confidence: float = 0.0      # MediaPipe hand confidence of this frame
    fps: float = 0.0
    progress: float = 0.0        # 0..1 hold progress of the stable gesture
    hand: bool = False           # a hand is visible
    started: bool = False        # the hold was reached (the action ran)
    idle: bool = False           # no hand for a while: running at the slow idle rate to save CPU
    # --- face pipeline (Face Only mode) ---
    pipeline: str = "hand"       # "hand" | "face"
    face: bool = False           # a face is visible
    right_eye: float = 1.0       # YOUR right eye: 1.0 = fully open ... 0.0 = closed
    left_eye: float = 1.0
    calibrating: bool = False    # still learning how wide your eyes are open (first ~1 s)
