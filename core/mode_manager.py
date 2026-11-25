"""Application modes and which devices each one uses."""
from __future__ import annotations

from enum import Enum
from typing import Callable


class Mode(str, Enum):
    VOICE_ONLY = "VOICE_ONLY"
    GESTURE_ONLY = "GESTURE_ONLY"
    HYBRID = "HYBRID"
    PAUSED = "PAUSED"
    FACE_ONLY = "FACE_ONLY"      # manual only: face expressions, voice and hand gestures are OFF


class ModeManager:
    def __init__(self, start_mode: str = "HYBRID"):
        mode = Mode(start_mode)
        if mode == Mode.FACE_ONLY:          # Face Only is never a start mode: it is switched on by hand
            mode = Mode.HYBRID
        self._mode = mode
        self._last_active = mode if mode != Mode.PAUSED else Mode.HYBRID
        self._listeners: list[Callable[[Mode], None]] = []

    @property
    def mode(self) -> Mode:
        return self._mode

    @property
    def voice_enabled(self) -> bool:
        return self._mode in (Mode.VOICE_ONLY, Mode.HYBRID)

    @property
    def hands_enabled(self) -> bool:
        return self._mode in (Mode.GESTURE_ONLY, Mode.HYBRID)

    @property
    def face_enabled(self) -> bool:
        return self._mode == Mode.FACE_ONLY

    @property
    def camera_enabled(self) -> bool:
        return self.hands_enabled or self.face_enabled

    def add_listener(self, fn: Callable[[Mode], None]) -> None:
        self._listeners.append(fn)

    def set_mode(self, mode: Mode | str) -> None:
        mode = Mode(mode)
        if mode == self._mode:
            return
        # Resume must never bring back Face Only: it remembers the last ordinary mode only.
        if mode not in (Mode.PAUSED, Mode.FACE_ONLY):
            self._last_active = mode
        self._mode = mode
        for fn in list(self._listeners):
            fn(mode)

    def pause(self) -> None:
        self.set_mode(Mode.PAUSED)

    def resume(self) -> None:
        """Leave Pause or Face Only and go back to the last ordinary mode (Hybrid by default)."""
        if self._mode in (Mode.PAUSED, Mode.FACE_ONLY):
            self.set_mode(self._last_active)
