"""MediaPipe Tasks FaceLandmarker wrapper (VIDEO running mode, one face, landmarks only)."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

MODEL_FILE = Path(__file__).resolve().parent.parent / "models" / "face_landmarker.task"


class FaceModelMissingError(RuntimeError):
    pass


@dataclass
class FaceDetection:
    landmarks: list[tuple[float, float, float]]   # 478 x (x, y, z), x/y normalized to the frame
    aspect: float                                 # frame width / height


class FaceTracker:
    def __init__(self, min_confidence: float = 0.5, model_file: Path = MODEL_FILE):
        if not Path(model_file).is_file():
            raise FaceModelMissingError(
                "The face model is missing. Run: python scripts/download_models.py --face "
                "(or Settings > Download missing models)")
        import mediapipe as mp
        from mediapipe.tasks import python as mp_python
        from mediapipe.tasks.python import vision

        self._mp = mp
        buffer = Path(model_file).read_bytes()          # (plain bytes: non-ASCII folder names are fine)
        options = vision.FaceLandmarkerOptions(
            base_options=mp_python.BaseOptions(model_asset_buffer=buffer),
            running_mode=vision.RunningMode.VIDEO,
            num_faces=1,
            min_face_detection_confidence=min_confidence,
            min_face_presence_confidence=min_confidence,
            min_tracking_confidence=min_confidence,
            output_face_blendshapes=False,               # not needed: cheaper
            output_facial_transformation_matrixes=False,
        )
        self._landmarker = vision.FaceLandmarker.create_from_options(options)
        self._last_ts = -1

    def detect(self, frame_bgr, timestamp_ms: int) -> Optional[FaceDetection]:
        import cv2

        h, w = frame_bgr.shape[:2]
        # Mirrored like a selfie camera: your right eye is on the right side of the picture.
        rgb = cv2.cvtColor(cv2.flip(frame_bgr, 1), cv2.COLOR_BGR2RGB)
        image = self._mp.Image(image_format=self._mp.ImageFormat.SRGB, data=rgb)
        ts = max(int(timestamp_ms), self._last_ts + 1)   # VIDEO mode needs strictly rising time
        self._last_ts = ts
        result = self._landmarker.detect_for_video(image, ts)
        if not result.face_landmarks:
            return None
        return FaceDetection([(p.x, p.y, p.z) for p in result.face_landmarks[0]], w / h)

    def close(self) -> None:
        try:
            self._landmarker.close()
        except Exception:
            log.debug("FaceLandmarker close failed")
