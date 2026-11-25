"""Gesture engine: one background thread that owns the camera and the hand tracker.

Pipeline per frame: camera -> MediaPipe landmarks -> features -> classifier -> stabilizer.
It never touches Qt. Results go to a queue.Queue of GestureMessage that the UI polls.
In Phase 7 the events are only displayed; no action is executed.
"""
from __future__ import annotations

import logging
import queue
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Any, Callable, Optional

from gestures.camera import Camera, CameraError
from gestures.classifier import classify
from gestures.features import compute_features
from gestures.face import FaceProcessor
from gestures.face_tracker import FaceModelMissingError, FaceTracker
from gestures.hand_tracker import HandModelMissingError, HandTracker
from gestures.stabilizer import GestureStabilizer
from gestures.types import Gesture, GestureStatus

log = logging.getLogger(__name__)

# CPU saving: with no hand in view the frames are only scanned a few times per second;
# as soon as a hand shows up the configured rate (default 12 FPS) is back immediately.
IDLE_FPS = 4.0
IDLE_AFTER_SECONDS = 1.5


@dataclass
class GestureMessage:
    kind: str          # camera | error | status | event
    payload: Any = None


class GestureEngine:
    def __init__(self, store, gestures_provider: Callable[[], dict],
                 events: "queue.Queue[GestureMessage]",
                 camera_factory: Optional[Callable[[dict], Any]] = None,
                 tracker_factory: Optional[Callable[[dict], Any]] = None,
                 clock: Callable[[], float] = time.monotonic,
                 face_tracker_factory: Optional[Callable[[dict], Any]] = None):
        self.store = store
        self.gestures_provider = gestures_provider
        self.events = events
        self._camera_factory = camera_factory or self._default_camera
        self._tracker_factory = tracker_factory or self._default_tracker
        self._face_tracker_factory = face_tracker_factory or self._default_face_tracker
        self.pipeline = "hand"                  # "hand" (gestures) or "face" (Face Only mode)
        self._clock = clock
        self._stop = threading.Event()
        self._enabled = threading.Event()
        self._version = 0
        self._thread: Optional[threading.Thread] = None
        self._camera_on: Optional[bool] = None
        self._camera_obj: Any = None            # for shutdown() if the thread is stuck
        self._last_hand = 0.0                   # when a hand was last seen (idle throttle)
        self._idle = False
        # written by the gesture controller (UI thread), read by the stabilizer (this thread)
        self.hold_overrides: dict[str, float] = {}

    # ---------- public API (UI thread) ----------
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="gesture-engine", daemon=True)
        self._thread.start()

    def request_stop(self) -> None:
        """Ask the thread to finish without waiting (so several threads can stop in parallel)."""
        self._stop.set()

    def shutdown(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3)
            if self._thread.is_alive():
                # The thread is stuck in a slow start (MediaPipe still loading). Do not leave the
                # camera open: close it from here; the thread cleans up when it gets back to its loop.
                cam = self._camera_obj
                if cam is not None:
                    try:
                        cam.close()
                    except Exception:
                        log.exception("Could not close the camera")
                    self._camera_state(False)

    def set_enabled(self, enabled: bool) -> None:
        if enabled:
            if not self._enabled.is_set():        # only a real off -> on change restarts the camera
                self._version += 1
                self._enabled.set()
        else:
            self._enabled.clear()

    def reload(self) -> None:
        """Settings (hand, FPS, camera, hold times) changed."""
        self._version += 1

    def set_pipeline(self, name: str) -> None:
        """Switch between hand gestures and face expressions (rebuilds the tracker)."""
        name = "face" if name == "face" else "hand"
        if name != self.pipeline:
            self.pipeline = name
            self._version += 1

    # ---------- factories ----------
    @staticmethod
    def _default_camera(s: dict) -> Camera:
        return Camera(s["camera_index"], s["camera_width"], s["camera_height"], s["camera_fps"])

    @staticmethod
    def _default_tracker(s: dict) -> HandTracker:
        return HandTracker(s["min_hand_confidence"])

    @staticmethod
    def _default_face_tracker(s: dict) -> FaceTracker:
        return FaceTracker(0.5)

    # ---------- internals ----------
    def _put(self, kind: str, payload: Any = None) -> None:
        self.events.put(GestureMessage(kind, payload))

    def _camera_state(self, on: bool) -> None:
        if on != self._camera_on:
            self._camera_on = on
            self._put("camera", on)

    def _run(self) -> None:
        camera = tracker = stabilizer = None
        seen, failed_version = -1, -2
        stamps: deque[float] = deque(maxlen=60)
        settings: dict = {}
        try:
            while not self._stop.is_set():
                if not self._enabled.is_set():
                    camera, tracker = self._release(camera, tracker)
                    stabilizer = None
                    seen = -1
                    self._camera_state(False)
                    self._stop.wait(0.1)
                    continue

                if camera is None or seen != self._version:
                    if failed_version == self._version:
                        self._camera_state(False)
                        self._stop.wait(0.2)
                        continue
                    seen = self._version
                    camera, tracker = self._release(camera, tracker)
                    try:
                        settings = self.store.settings
                        camera = self._camera_factory(settings)
                        self._camera_obj = camera
                        camera.open()
                        pipeline = self.pipeline
                        if pipeline == "face":
                            tracker = self._face_tracker_factory(settings)
                        else:
                            tracker = self._tracker_factory(settings)
                        if self._stop.is_set():          # quit while MediaPipe was loading
                            raise CameraError("stopping")
                        if pipeline == "face":
                            stabilizer = FaceProcessor(settings, self._clock)
                        else:
                            stabilizer = GestureStabilizer(settings, self.gestures_provider(), self._clock,
                                                           self.hold_overrides)
                    except (CameraError, HandModelMissingError, FaceModelMissingError) as exc:
                        if self._stop.is_set():          # shutting down, not a real error
                            camera, tracker = self._release(camera, tracker)
                            break
                        log.error("Gestures could not start: %s", exc)
                        self._put("error", str(exc))
                        camera, tracker = self._release(camera, tracker)
                        failed_version = seen
                        self._camera_state(False)
                        continue
                    except Exception as exc:
                        log.exception("Gestures could not start")
                        self._put("error", f"Gestures could not start: {exc}")
                        camera, tracker = self._release(camera, tracker)
                        failed_version = seen
                        self._camera_state(False)
                        continue
                    self._camera_state(True)
                    stamps.clear()
                    self._last_hand, self._idle = self._clock(), False

                try:
                    frame = camera.read_due()
                    if frame is None:
                        continue
                    now = self._clock()
                    stamps.append(now)
                    self._process(frame, now, settings, tracker, stabilizer, stamps, camera)
                except CameraError as exc:
                    log.error("%s", exc)
                    self._put("error", str(exc))
                    camera, tracker = self._release(camera, tracker)
                    failed_version = seen
                    self._camera_state(False)
                except Exception as exc:
                    log.exception("Gesture engine error")
                    self._put("error", f"Gesture engine error: {exc}")
                    camera, tracker = self._release(camera, tracker)
                    failed_version = seen
                    self._camera_state(False)
        finally:
            self._release(camera, tracker)
            self._camera_state(False)

    def _release(self, camera, tracker):
        self._camera_obj = None
        for obj in (camera, tracker):
            if obj is not None:
                try:
                    obj.close()
                except Exception:
                    log.debug("close failed")
        return None, None

    def _process_face(self, frame, now, settings, tracker, processor, stamps, camera) -> None:
        det = tracker.detect(frame, int(now * 1000))
        events, fields = processor.process(det)
        for e in events:
            self._put("event", e)
        self._throttle(camera, bool(fields.get("face")), now, settings)
        fps = 0.0
        if len(stamps) >= 2 and stamps[-1] > stamps[0]:
            fps = (len(stamps) - 1) / (stamps[-1] - stamps[0])
        self._put("status", GestureStatus(fps=fps, idle=self._idle, pipeline="face", **fields))

    def _process(self, frame, now, settings, tracker, stabilizer, stamps, camera=None) -> None:
        if isinstance(stabilizer, FaceProcessor):
            return self._process_face(frame, now, settings, tracker, stabilizer, stamps, camera)
        det = tracker.detect(frame, int(now * 1000))
        wanted = settings.get("hand", "Any")
        gesture, confidence, feats = Gesture.NONE, 0.0, None
        if det is not None:
            confidence = det.score
            usable = det.score >= float(settings.get("min_hand_confidence", 0.7)) \
                and (wanted == "Any" or det.label == wanted)
            if usable:
                feats = compute_features(det.landmarks, det.aspect)
                gesture = classify(feats)
        if feats is not None:
            events = stabilizer.update(gesture, feats.wrist, feats.hand_size)
        else:
            events = stabilizer.update(Gesture.NONE)
        for e in events:
            self._put("event", e)
        st = stabilizer.status
        hand = det is not None and feats is not None
        self._throttle(camera, hand, now, settings)
        fps = 0.0
        if len(stamps) >= 2 and stamps[-1] > stamps[0]:
            fps = (len(stamps) - 1) / (stamps[-1] - stamps[0])
        self._put("status", GestureStatus(
            gesture=gesture.value, confidence=confidence, fps=fps, progress=st.progress,
            hand=hand, started=st.started, idle=self._idle))

    def _throttle(self, camera, hand: bool, now: float, settings: dict) -> None:
        """No hand for IDLE_AFTER_SECONDS -> scan slowly; a hand appears -> full speed at once."""
        if hand:
            self._last_hand = now
        idle = (now - self._last_hand) >= IDLE_AFTER_SECONDS
        if idle != self._idle:
            self._idle = idle
            set_fps = getattr(camera, "set_fps", None)
            if set_fps is not None:
                set_fps(IDLE_FPS if idle else float(settings.get("camera_fps", 12)))
