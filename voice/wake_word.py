"""Wake-word detector: a tiny recognizer with the grammar ["hand", "[unk]"]."""
from __future__ import annotations

from typing import Protocol

from voice.text_normalizer import strip_unk


class RecognizerLike(Protocol):
    def feed(self, data: bytes) -> str | None: ...
    def partial(self) -> str: ...
    def finalize(self) -> str: ...
    def reset(self) -> None: ...


class WakeWordDetector:
    def __init__(self, recognizer: RecognizerLike, wake_word: str = "hand"):
        self.rec = recognizer
        self.wake_word = wake_word.strip().lower()

    def feed(self, data: bytes) -> bool:
        """True when the wake word was heard in this block (partial or final result).

        Only a whole-word match counts, so "handle" or "handy" never trigger. It must also
        START the utterance: "[unk] hand" is a sound in the middle of other speech (a video,
        people talking), which used to open the command window and launch apps by accident.
        """
        final = self.rec.feed(data)
        text = final if final is not None else self.rec.partial()
        tokens = text.lower().split()
        hit = bool(tokens) and tokens[0] == self.wake_word
        if hit:
            self.rec.reset()
        return hit

    def reset(self) -> None:
        self.rec.reset()
