"""Builds the Vosk JSON grammars.

Vosk can only recognize words that exist in the model's vocabulary, so names that
contain digits, non-ASCII letters or out-of-vocabulary words are skipped and
reported (see GrammarResult.skipped). Give such apps a spoken alias made of common
words on the Applications page.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Iterable, Optional

from voice.text_normalizer import UNK

PREFIXES = ("file", "f", "ef", "web", "w", "double u", "search")
WEB_PREFIXES = ("web", "w", "double u")
NUMBER_WORDS = ("one", "two", "three", "four", "five", "six", "seven", "eight", "nine")


@dataclass
class PhraseSources:
    command_phrases: list[str]
    app_names: list[str] = field(default_factory=list)
    web_aliases: list[str] = field(default_factory=list)
    extra_phrases: list[str] = field(default_factory=list)   # extra spoken phrases (reported if unspeakable)
    file_words: list[str] = field(default_factory=list)      # words from indexed file names (never reported)


@dataclass
class GrammarResult:
    phrases: list[str]
    skipped: list[str]

    def to_json(self) -> str:
        return json.dumps(self.phrases)


def clean_phrase(text: str) -> Optional[str]:
    """Lowercase letters/apostrophes/spaces only. None if it cannot be spoken."""
    t = text.strip().lower()
    if not t or not t.isascii() or re.search(r"\d", t):
        return None
    t = re.sub(r"[^a-z' ]+", " ", t)
    t = " ".join(t.split())
    return t or None


def build_wake_grammar(wake_word: str) -> str:
    return json.dumps([wake_word.strip().lower(), UNK])


def validate_wake_word(wake_word: str, command_phrases: Iterable[str]) -> None:
    """The wake word must never be a command phrase (spec 3.1)."""
    wake = clean_phrase(wake_word)
    if not wake or " " in wake:
        raise ValueError(f"Wake word must be a single plain word, got {wake_word!r}")
    reserved = {clean_phrase(p) for p in command_phrases} | set(PREFIXES) | set(NUMBER_WORDS)
    if wake in reserved:
        raise ValueError(f"Wake word {wake!r} is also a command word")


def build_command_phrases(
    sources: PhraseSources,
    wake_word: Optional[str] = None,
    vocabulary=None,                      # anything supporting `word in vocabulary`
    absorb_wake_word: Optional[str] = None,
) -> GrammarResult:
    """Build the command grammar. With wake_word set (ALWAYS mode) every phrase
    starts with the wake word so it is recognized as one utterance."""
    phrases: list[str] = []
    skipped: list[str] = []
    seen: set[str] = set()

    def speakable(cp: Optional[str]) -> bool:
        if cp is None:
            return False
        return vocabulary is None or all(w in vocabulary for w in cp.split())

    def add(text: str, report: bool = False) -> None:
        cp = clean_phrase(text)
        if not speakable(cp):
            if report:
                skipped.append(text)
            return
        if cp not in seen:
            seen.add(cp)
            phrases.append(cp)

    for p in sources.command_phrases:
        add(p)
    for p in PREFIXES:
        add(p)
    for n in NUMBER_WORDS:
        add(n)
    for name in sources.app_names:
        add(name, report=True)
    for alias in sources.web_aliases:
        cp = clean_phrase(alias)
        if not speakable(cp):
            skipped.append(alias)
            continue
        for prefix in WEB_PREFIXES:
            add(f"{prefix} {cp}")
    for p in sources.extra_phrases:
        add(p, report=True)
    # "file <word>" only. Never the bare words: standalone words loosen the grammar so much that
    # the wake word itself is heard as "and"/"pan" and chatter turns into fake commands.
    for word in sources.file_words:
        for prefix in ("file", "f", "ef"):
            add(f"{prefix} {word}")

    if absorb_wake_word:
        # The wake-word audio is replayed into this recognizer ("Hand, Claude" in one breath).
        # Without the word in the grammar it is heard as "and"/"headed". It is only absorbed:
        # the parser strips it and it is never a command.
        phrases.insert(0, absorb_wake_word.strip().lower())
    if wake_word:
        wake = wake_word.strip().lower()
        phrases = [f"{wake} {p}" for p in phrases]
    phrases.append(UNK)
    # dedupe skipped, keep order
    skipped = list(dict.fromkeys(skipped))
    return GrammarResult(phrases, skipped)
