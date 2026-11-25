"""Downloads the offline models into models/ (run once, needs internet).

    python scripts/download_models.py             # both models
    python scripts/download_models.py --vosk      # speech model only
    python scripts/download_models.py --hand      # hand landmarker only
    python scripts/download_models.py --face      # face landmarker only (for Face Only mode)
    python scripts/download_models.py --better    # optional, more accurate speech model (128 MB);
                                                  # the app uses it automatically when present

This is the only part of Hand&Voice that uses the network. The app itself never does.
"""
from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MODELS = ROOT / "models"

VOSK_NAME = "vosk-model-small-en-us-0.15"
VOSK_URL = f"https://alphacephei.com/vosk/models/{VOSK_NAME}.zip"
HAND_URL = ("https://storage.googleapis.com/mediapipe-models/hand_landmarker/"
            "hand_landmarker/float16/latest/hand_landmarker.task")
HAND_FILE = MODELS / "hand_landmarker.task"


def download(url: str, dest: Path) -> None:
    if not url.startswith("https://"):
        raise ValueError("Only https downloads are allowed")
    print(f"Downloading {url}")
    with urllib.request.urlopen(url, timeout=60) as resp, open(dest, "wb") as out:
        total = int(resp.headers.get("Content-Length") or 0)
        done = 0
        while True:
            chunk = resp.read(1 << 16)
            if not chunk:
                break
            out.write(chunk)
            done += len(chunk)
            if total:
                print(f"\r  {done / 1e6:6.1f} / {total / 1e6:.1f} MB", end="", flush=True)
    print()


def safe_extract(zip_path: Path, target: Path) -> None:
    """Extract, refusing any entry that would land outside the target folder."""
    target = target.resolve()
    with zipfile.ZipFile(zip_path) as zf:
        for member in zf.infolist():
            dest = (target / member.filename).resolve()
            if target != dest and target not in dest.parents:
                raise RuntimeError(f"Unsafe path in archive: {member.filename}")
        zf.extractall(target)


BETTER_NAME = "vosk-model-en-us-0.22-lgraph"


def get_vosk(name: str = VOSK_NAME) -> None:
    final = MODELS / name
    if (final / "am").exists():
        print(f"Vosk model already installed: {final}")
        return
    with tempfile.TemporaryDirectory() as tmp:
        zip_path = Path(tmp) / "vosk.zip"
        download(f"https://alphacephei.com/vosk/models/{name}.zip", zip_path)
        print("Extracting...")
        extract_dir = Path(tmp) / "x"
        safe_extract(zip_path, extract_dir)
        inner = extract_dir / name
        if not inner.is_dir():
            raise RuntimeError("Unexpected archive layout")
        if final.exists():
            shutil.rmtree(final)
        shutil.move(str(inner), str(final))
    print(f"Vosk model installed: {final}")


def get_hand() -> None:
    if HAND_FILE.is_file():
        print(f"Hand landmarker already installed: {HAND_FILE}")
        return
    tmp = HAND_FILE.with_suffix(".download")
    download(HAND_URL, tmp)
    tmp.replace(HAND_FILE)
    print(f"Hand landmarker installed: {HAND_FILE}")


FACE_URL = ("https://storage.googleapis.com/mediapipe-models/face_landmarker/"
            "face_landmarker/float16/latest/face_landmarker.task")
FACE_FILE = MODELS / "face_landmarker.task"


def get_face() -> None:
    if FACE_FILE.is_file():
        print(f"Face landmarker already installed: {FACE_FILE}")
        return
    tmp = FACE_FILE.with_suffix(".download")
    download(FACE_URL, tmp)
    tmp.replace(FACE_FILE)
    print(f"Face landmarker installed: {FACE_FILE}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--vosk", action="store_true", help="only the speech model")
    parser.add_argument("--hand", action="store_true", help="only the hand landmarker")
    parser.add_argument("--face", action="store_true", help="only the face landmarker (Face Only mode, about 4 MB)")
    parser.add_argument("--better", action="store_true", help="the more accurate 128 MB speech model")
    args = parser.parse_args()
    MODELS.mkdir(exist_ok=True)
    both = not (args.vosk or args.hand or args.face or args.better)
    try:
        if args.better:
            get_vosk(BETTER_NAME)
        if both or args.vosk:
            get_vosk()
        if both or args.hand:
            get_hand()
        if both or args.face:
            get_face()
    except Exception as exc:
        print(f"\nDownload failed: {exc}")
        return 1
    print("Done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
