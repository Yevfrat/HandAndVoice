"""Lowercase, trim, strip Vosk's [unk] token, number words -> digits."""
from __future__ import annotations

import re

UNK = "[unk]"

NUMBER_WORDS = {
    "one": "1", "two": "2", "three": "3", "four": "4", "five": "5",
    "six": "6", "seven": "7", "eight": "8", "nine": "9",
}


def strip_unk(text: str) -> str:
    """Lowercase and remove [unk] tokens, collapsing whitespace."""
    return " ".join(t for t in text.lower().split() if t != UNK)


def normalize(text: str) -> str:
    """Normalize recognized or configured text for matching.

    "Double-U Alim" -> "double u alim"; "File chapter one" -> "file chapter 1".
    """
    t = strip_unk(text)
    t = t.replace("-", " ")
    t = re.sub(r"[^a-z0-9' ]+", " ", t)
    tokens = [NUMBER_WORDS.get(tok, tok) for tok in t.split()]
    return " ".join(tokens)


def strip_wake_word(text: str, wake_word: str) -> str:
    """Remove the wake word from the start of already-normalized text (once)."""
    wake = wake_word.strip().lower()
    tokens = text.split()
    if tokens and tokens[0] == wake:
        tokens = tokens[1:]
    return " ".join(tokens)
