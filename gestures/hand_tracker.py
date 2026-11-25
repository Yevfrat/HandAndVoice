"""MediaPipe Tasks HandLandmarker wrapper (VIDEO running mode, one hand)."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

MODEL_FILE = Path(__file__).resolve().parent.parent / "models" / "hand_landmarker.task"


class HandModelMissingError(RuntimeError):
    pass


@dataclass
class Detection:
    landmarks: list[tuple[float, float, float]]   # 21 x (x, y, z), x/y normalized to the frame
    label: str                                    # "Left" | "Right" (the user's real hand, see below)
    score: float                                  # handedness confidence of MediaPipe (0..1)
    aspect: float                                 # frame width / height


class HandTracker:
    def __init__(self, min_confidence: float = 0.7, model_file: Path = MODEL_FILE):
        if not Path(model_file).is_file():
            raise HandModelMissingError(
                "The hand model is missing. Run: python scripts/download_models.py --hand")
        import mediapipe as mp
        from mediapipe.tasks import python as mp_python
        from mediapipe.tasks.python import vision

        self._mp = mp
        # Read the file ourselves: MediaPipe's own path handling fails on non-ASCII folder names.
        buffer = Path(model_file).read_bytes()
        options = vision.HandLandmarkerOptions(
            base_options=mp_python.BaseOptions(model_asset_buffer=buffer),
            running_mode=vision.RunningMode.VIDEO,
            num_hands=1,
            min_hand_detection_confidence=min_confidence,
            min_hand_presence_confidence=min_confidence,
            min_tracking_confidence=min_confidence,
        )
        self._landmarker = vision.HandLandmarker.create_from_options(options)
        self._last_ts = -1

    def detect(self, frame_bgr, timestamp_ms: int) -> Optional[Detection]:
        import cv2

        h, w = frame_bgr.shape[:2]
        # Mirror the frame (selfie view): MediaPipe's Left/Right labels assume it, and
        # pointing to YOUR right then points right on the screen too.
        rgb = cv2.cvtColor(cv2.flip(frame_bgr, 1), cv2.COLOR_BGR2RGB)
        image = self._mp.Image(image_format=self._mp.ImageFormat.SRGB, data=rgb)
        ts = max(int(timestamp_ms), self._last_ts + 1)       # VIDEO mode needs strictly rising time
        self._last_ts = ts
        result = self._landmarker.detect_for_video(image, ts)
        if not result.hand_landmarks:
            return None
        lm = result.hand_landmarks[0]
        cat = result.handedness[0][0]
        return Detection([(p.x, p.y, p.z) for p in lm], cat.category_name, float(cat.score), w / h)

    def close(self) -> None:
        try:
            self._landmarker.close()
        except Exception:
            log.debug("HandLandmarker close failed")
