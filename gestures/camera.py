"""Webcam capture with an FPS limiter. Frames live in memory only, never on disk."""
from __future__ import annotations

import logging
import time
from typing import Optional

log = logging.getLogger(__name__)

MAX_FAILED_READS = 40      # about 3 s at 12 FPS before the camera is declared dead


class CameraError(RuntimeError):
    pass


class Camera:
    def __init__(self, index: int = 0, width: int = 640, height: int = 360, fps: int = 12):
        self.index, self.width, self.height = index, width, height
        self.interval = 1.0 / max(1, fps)
        self._cap = None
        self._last = 0.0
        self._failures = 0

    @property
    def is_open(self) -> bool:
        return self._cap is not None

    def set_fps(self, fps: float) -> None:
        """Change the processing rate on the fly (the camera itself keeps its own speed)."""
        self.interval = 1.0 / max(1.0, float(fps))

    def open(self) -> None:
        if self._cap is not None:
            return
        import cv2

        # DirectShow opens much faster on Windows than the default backend.
        cap = cv2.VideoCapture(self.index, cv2.CAP_DSHOW)
        if not cap.isOpened():
            cap.release()
            raise CameraError(
                f"Camera {self.index} could not be opened. Close other apps that use the camera "
                "(Zoom, Teams, browser tabs) and check Windows Settings > Privacy > Camera."
            )
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        self._cap = cap
        self._failures = 0
        log.info("Camera opened")

    def read_due(self, now: Optional[float] = None):
        """Sleep until the next frame is due, then return the latest camera frame (or None
        when the read failed).

        IMPORTANT: with the DirectShow backend cap.grab() does NOT wait for a new frame, it
        returns at once, so a grab() loop spins at 100 % CPU (measured: over a million calls
        per second). We therefore pace ourselves with sleep() and call read() only when a
        frame is wanted; with a buffer of one the frame returned is at most one frame old.

        `now` is only for tests.
        """
        cap = self._cap
        if cap is None:
            raise CameraError("Camera is not open")
        clock = time.monotonic() if now is None else now
        wait = self._last + self.interval - clock
        if wait > 0:
            time.sleep(wait)
        ok, frame = cap.read()
        if not ok or frame is None:
            time.sleep(0.05)       # about 2 s in total before giving up
            self._failures += 1
            if self._failures >= MAX_FAILED_READS:
                raise CameraError("The camera stopped delivering frames (unplugged or taken by another app).")
            return None
        self._failures = 0
        self._last = time.monotonic() if now is None else now + max(wait, 0.0)
        return frame

    def close(self) -> None:
        cap, self._cap = self._cap, None
        if cap is not None:
            cap.release()          # releases the device: the camera light goes off
            log.info("Camera closed")
