"""Turns per-frame gestures into stable events (spec 4.3).

* a gesture counts only after it was the same for `stable_frames` frames in a row;
* it must then be HELD for its hold time (default 1 s, FIST 2 s, scroll gestures 0.3 s);
* changing the gesture or moving the wrist by more than 0.15 x hand size resets the hold;
* after a gesture fired, the same gesture is ignored for the cooldown (1 s);
* continuous gestures (scroll, volume) keep sending "tick" events while held.
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import Callable, Optional

from gestures.types import CONTINUOUS, Gesture, GestureEvent

CONTINUOUS_NAMES = {g.value for g in CONTINUOUS}      # plain names: face gestures are not Gesture members
FIST_NAME = Gesture.FIST.value
SCROLL_NAMES = {Gesture.POINT_UP.value, Gesture.POINT_DOWN.value}


@dataclass
class StabilizerStatus:
    candidate: str = "NONE"       # what the last frames show
    stable: bool = False          # same for enough frames
    progress: float = 0.0         # 0..1 hold progress
    started: bool = False         # hold reached, "start" was emitted


class GestureStabilizer:
    def __init__(self, settings: dict, gestures: dict, clock: Callable[[], float] = time.monotonic,
                 hold_overrides: Optional[dict] = None):
        self.settings = settings
        self.gestures = gestures              # gestures.json: name -> {hold_seconds, enabled, ...}
        # shared with the gesture controller: shorter holds while inside Switch Window / Browser mode
        self.hold_overrides: dict = hold_overrides if hold_overrides is not None else {}
        self._clock = clock
        self._candidate = Gesture.NONE.value
        self._count = 0
        self._first_frame = 0.0                   # when the current candidate was first seen
        self._hold_start: Optional[float] = None  # set once it is stable; the hold counts from the first frame
        self._anchor: Optional[tuple[float, float]] = None
        self._started = False
        self._cooldown_until: dict[str, float] = {}

    # ---------- configuration ----------
    def hold_seconds(self, name: str) -> float:
        if name in self.hold_overrides:
            return float(self.hold_overrides[name])
        cfg = self.gestures.get(name, {})
        if "hold_seconds" in cfg:
            return float(cfg["hold_seconds"])
        if name == FIST_NAME:
            return float(self.settings.get("sleep_hold_seconds", 2.0))
        if name in SCROLL_NAMES:
            return float(self.settings.get("scroll_hold_seconds", 0.3))
        return float(self.settings.get("default_hold_seconds", 1.0))

    def enabled(self, name: str) -> bool:
        return bool(self.gestures.get(name, {}).get("enabled", True))

    # ---------- state ----------
    @property
    def status(self) -> StabilizerStatus:
        stable = self._count >= int(self.settings.get("stable_frames", 5))
        progress = 0.0
        if stable and self._candidate != Gesture.NONE.value and self._hold_start is not None:
            hold = max(self.hold_seconds(self._candidate), 1e-6)
            progress = min(1.0, (self._clock() - self._hold_start) / hold)
        return StabilizerStatus(self._candidate, stable, progress, self._started)

    def reset(self) -> list[GestureEvent]:
        """Forget everything (camera off / hand lost). Returns an "end" if one was running."""
        events = self._end_events()
        self._candidate, self._count = Gesture.NONE.value, 0
        self._hold_start, self._anchor, self._started = None, None, False
        return events

    def _end_events(self) -> list[GestureEvent]:
        if self._started and self._candidate != Gesture.NONE.value:
            return [GestureEvent(self._candidate, "end")]
        return []

    # ---------- per frame ----------
    def update(self, gesture: Gesture | str, wrist: Optional[tuple[float, float]] = None,
               hand_size: Optional[float] = None) -> list[GestureEvent]:
        now = self._clock()
        name = gesture.value if isinstance(gesture, Gesture) else str(gesture)
        events: list[GestureEvent] = []

        if name != self._candidate:                       # changed (or hand lost): start over
            events += self._end_events()
            self._candidate, self._count, self._first_frame = name, 1, now
            self._hold_start, self._anchor, self._started = None, None, False
        else:
            self._count += 1

        if name == Gesture.NONE.value:
            return events
        if self._count < int(self.settings.get("stable_frames", 5)):
            return events

        if self._hold_start is None:                      # just became stable
            self._hold_start, self._anchor = self._first_frame, wrist

        x, y = wrist if wrist is not None else (0.0, 0.0)
        size = hand_size or 0.0

        # Movement tolerance: a moving hand does not count as a HOLD. Once the gesture has
        # started the hand may move (volume mode follows the wrist; a started action never repeats).
        if not self._started and wrist is not None and self._anchor is not None and hand_size:
            drift = math.hypot(wrist[0] - self._anchor[0], wrist[1] - self._anchor[1])
            if drift > float(self.settings.get("movement_tolerance", 0.15)) * hand_size:
                self._hold_start, self._anchor = now, wrist
                return events

        if not self.enabled(name):
            return events

        continuous = name in CONTINUOUS_NAMES
        if not self._started:
            if now - self._hold_start >= self.hold_seconds(name):
                # continuous gestures (scroll, volume) are not subject to the cooldown
                if continuous or now >= self._cooldown_until.get(name, 0.0):
                    self._started = True
                    if not continuous:
                        self._cooldown_until[name] = now + float(self.settings.get("cooldown_seconds", 1.0))
                    events.append(GestureEvent(name, "start", x, y, size))
        elif continuous:
            events.append(GestureEvent(name, "tick", x, y, size))
        return events
