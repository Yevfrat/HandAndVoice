"""Writes tests/fixtures/landmarks/<GESTURE>.json from the synthetic hand model.

    python scripts/make_landmark_fixtures.py

The fixtures are synthetic (see tests/hand_model.py), not recordings of a real hand.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tests.hand_model import SPECS, build  # noqa: E402

OUT = ROOT / "tests" / "fixtures" / "landmarks"


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for name in SPECS:
        (OUT / f"{name}.json").write_text(json.dumps({"gesture": name.split("_")[0] if name.startswith("NONE") else name,
                                                      "synthetic": True, "landmarks": build(name)}, indent=1))
    print(f"wrote {len(SPECS)} fixtures to {OUT}")


if __name__ == "__main__":
    main()
