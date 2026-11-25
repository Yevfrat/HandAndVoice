"""Performance check for THIS computer (run it on the Intel i3 laptop).

    python scripts/benchmark.py              # models only, no camera
    python scripts/benchmark.py --camera     # also uses the webcam for 5 seconds (nothing is saved)

Budgets from the specification: the gesture pipeline must reach at least 10 FPS, and the
microphone thread must stay low while idle. Hold your hand in front of the camera for the
--camera test to measure the active (hand visible) case.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

FPS_BUDGET = 10.0           # frames per second, gesture pipeline (spec section 13, phase 10)
VOICE_IDLE_BUDGET = 25.0    # % of ONE core for the always-on wake-word listener
IDLE_CAMERA_BUDGET = 40.0   # % of one core with the camera on and no hand in view

results: list[tuple[str, str, bool]] = []


def verdict(name: str, text: str, ok: bool) -> None:
    results.append((name, text, ok))
    print(f"  [{'OK  ' if ok else 'SLOW'}] {name}: {text}")


def bench_pipeline() -> None:
    import numpy as np

    from gestures.hand_tracker import MODEL_FILE, HandTracker

    print("Gesture pipeline (worst case: MediaPipe searches the whole frame every time)")
    if not MODEL_FILE.is_file():
        print("  skipped: hand model missing (python scripts/download_models.py --hand)")
        return
    tracker = HandTracker(0.7)
    frame = np.zeros((360, 640, 3), dtype=np.uint8)
    for i in range(5):
        tracker.detect(frame, 1000 + i * 100)                  # warm-up
    n = 40
    t = time.perf_counter()
    for i in range(n):
        tracker.detect(frame, 2000 + i * 100)
    ms = (time.perf_counter() - t) / n * 1000
    tracker.close()
    fps = 1000.0 / ms
    verdict("pipeline speed", f"{ms:.0f} ms per frame, up to {fps:.0f} FPS (budget: at least {FPS_BUDGET:.0f})",
            fps >= FPS_BUDGET)
    idle = 4.0 * ms / 10.0
    verdict("camera idle cost (no hand, 4 FPS scan)", f"about {idle:.0f}% of one core (budget: under {IDLE_CAMERA_BUDGET:.0f}%)",
            idle < IDLE_CAMERA_BUDGET)


def bench_face() -> None:
    import numpy as np

    from gestures.face_tracker import MODEL_FILE, FaceTracker

    print("Face Only pipeline (face landmarks + eye aspect ratio; worst case: no face, full search)")
    if not MODEL_FILE.is_file():
        print("  skipped: face model missing (python scripts/download_models.py --face)")
        return
    tracker = FaceTracker(0.5)
    frame = np.zeros((360, 640, 3), dtype=np.uint8)
    for i in range(5):
        tracker.detect(frame, 1000 + i * 100)                  # warm-up
    n = 40
    t = time.perf_counter()
    for i in range(n):
        tracker.detect(frame, 2000 + i * 100)
    ms = (time.perf_counter() - t) / n * 1000
    tracker.close()
    fps = 1000.0 / ms
    verdict("face pipeline speed", f"{ms:.0f} ms per frame, up to {fps:.0f} FPS (budget: at least {FPS_BUDGET:.0f})",
            fps >= FPS_BUDGET)


def bench_voice() -> None:
    import os

    from voice.grammar import PhraseSources, build_command_phrases, build_wake_grammar
    from voice.recognizer import SpeechRecognizer, get_model, model_installed

    print("Microphone thread while idle (wake-word listener on silence/room noise)")
    if not model_installed():
        print("  skipped: speech model missing (python scripts/download_models.py)")
        return
    import json

    cmds = list(json.loads((ROOT / "config" / "voice_commands.json").read_text()).keys())
    model = get_model()
    wake = SpeechRecognizer(build_wake_grammar("hand"), model)
    command = SpeechRecognizer(build_command_phrases(PhraseSources(cmds)).to_json(), model)
    block = (b"\x00\x00" * 4000)                               # 250 ms of silence
    noise = os.urandom(8000)                                   # 250 ms of noise
    for rec in (wake, command):
        rec.feed(block)
    n = 60
    t = time.process_time()
    for i in range(n):
        data = noise if i % 3 == 0 else block
        if wake.feed(data) is None:
            wake.partial()                                     # what the wake-word detector does each block
    per_block = (time.process_time() - t) / n
    pct = per_block / 0.25 * 100
    verdict("wake-word listener", f"{per_block * 1000:.1f} ms per 250 ms block = {pct:.0f}% of one core "
                                  f"(budget: under {VOICE_IDLE_BUDGET:.0f}%)", pct < VOICE_IDLE_BUDGET)


def bench_camera() -> None:
    import psutil

    from gestures.camera import Camera, CameraError
    from gestures.hand_tracker import MODEL_FILE, HandTracker

    print("Real camera, 5 seconds at 12 FPS (frames are processed in memory and thrown away)")
    if not MODEL_FILE.is_file():
        print("  skipped: hand model missing")
        return
    tracker = HandTracker(0.7)
    cam = Camera(0, 640, 360, 12)
    try:
        cam.open()
    except CameraError as exc:
        print(f"  skipped: {exc}")
        tracker.close()
        return
    proc = psutil.Process()
    frames = hands = 0
    cpu0 = sum(proc.cpu_times()[:2])
    start = time.perf_counter()
    while time.perf_counter() - start < 5.0:
        frame = cam.read_due()
        if frame is None:
            continue
        frames += 1
        hands += tracker.detect(frame, int(time.monotonic() * 1000)) is not None
    wall = time.perf_counter() - start
    cpu = (sum(proc.cpu_times()[:2]) - cpu0) / wall * 100
    cam.close()
    tracker.close()
    fps = frames / wall
    state = "with a hand in view" if hands > frames / 2 else "no hand in view (slower search every frame)"
    verdict("camera pipeline", f"{fps:.1f} FPS, {cpu:.0f}% of one core, {state} (budget: at least {FPS_BUDGET:.0f} FPS)",
            fps >= FPS_BUDGET - 0.5)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--camera", action="store_true", help="also test the real webcam for 5 seconds")
    args = ap.parse_args()
    try:
        import psutil
        print(f"This computer: {psutil.cpu_count(logical=False)} cores / {psutil.cpu_count()} threads\n")
    except Exception:
        pass
    bench_pipeline()
    print()
    bench_face()
    print()
    bench_voice()
    if args.camera:
        print()
        bench_camera()
    print()
    bad = [r for r in results if not r[2]]
    if not results:
        print("Nothing could be measured (models missing).")
        return 1
    print("RESULT:", "all budgets met" if not bad else f"{len(bad)} budget(s) missed: " + ", ".join(b[0] for b in bad))
    if bad:
        print("Tips: close other heavy programs; lower the camera FPS in Settings (the app then skips frames);"
              " use Voice Only mode when you do not need gestures.")
    return 0 if not bad else 2


if __name__ == "__main__":
    sys.exit(main())
