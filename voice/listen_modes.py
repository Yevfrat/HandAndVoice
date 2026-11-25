"""WAKE_WORD, ALWAYS and PUSH_TO_TALK behavior. Pure logic, no audio device access,
so it can be tested with fake recognizers."""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Optional

from voice.text_normalizer import UNK, strip_unk
from voice.wake_word import RecognizerLike, WakeWordDetector

# Words after which the user speaks free text (a file name or a search query).
OPEN_VOCAB_PREFIXES = ("file", "f", "ef", "search")


@dataclass
class VoiceEvent:
    kind: str       # mic_state | command_text | error | info | grammar_skipped | hotkey | registry_ready | index_ready
    text: str = ""  # reading from the small command grammar
    alt: str = ""   # second reading with the full English vocabulary (may be empty)


class OpenVocab:
    """Second pass with the full English vocabulary.

    The command grammar is small on purpose (a big grammar makes Vosk force every
    sound into some app name). Anything outside it comes out as [unk]. Then the audio
    of the current utterance (kept in memory only, never on disk) is decoded again with
    an open-vocabulary recognizer. That gives:
      * the free words of "file <name>" / "search <words>", and
      * an alternative reading (`alt`) the dispatcher tries when the grammar reading is
        not a valid command, e.g. apps that are not in the small grammar.
    Without a recognizer this is a no-op.
    """

    def __init__(self, recognizer: Optional[RecognizerLike] = None, max_blocks: int = 40,
                 known: Optional[set[str]] = None, wake_word: str = "hand"):
        self.rec = recognizer
        self.known = known              # phrases the grammar understands exactly
        self.wake_word = wake_word.strip().lower()
        self.blocks: deque[bytes] = deque(maxlen=max_blocks)   # 10 s of audio at most

    def add(self, data: bytes) -> None:
        if self.rec is not None:
            self.blocks.append(data)

    def clear(self) -> None:
        self.blocks.clear()

    def _strip(self, text: str) -> str:
        tokens = strip_unk(text).split()
        while tokens and tokens[0] == self.wake_word:
            tokens.pop(0)
        return " ".join(tokens)

    def resolve(self, raw_text: str) -> tuple[str, str]:
        """(text, alt) for one finished utterance. Clears the audio buffer."""
        try:
            return self._resolve(raw_text)
        finally:
            self.blocks.clear()

    def _resolve(self, raw_text: str) -> tuple[str, str]:
        base = self._strip(raw_text)
        if self.rec is None or not self.blocks:
            return base, ""
        tokens = raw_text.lower().split()
        idx = next((i for i, t in enumerate(tokens) if t in OPEN_VOCAB_PREFIXES), None)
        prefix = tokens[idx] if idx is not None else None
        has_unk = UNK in tokens
        if self.known is not None and base in self.known and not has_unk and prefix != "search":
            return base, ""                      # an exact phrase of the grammar: trust it
        free_text = prefix is not None and (prefix == "search" or UNK in tokens[idx + 1:])
        if not (has_unk or free_text or (self.known is not None and base not in self.known)):
            return base, ""

        open_tokens = self._decode()
        text = base
        if free_text:
            j = next((i for i, t in enumerate(open_tokens) if t == prefix), None)
            if j is not None and j < len(open_tokens) - 1:
                text = " ".join(open_tokens[j:])
        while open_tokens and open_tokens[0] == self.wake_word:
            open_tokens = open_tokens[1:]
        alt = " ".join(open_tokens)
        return text, (alt if alt != text else "")

    def _decode(self) -> list[str]:
        self.rec.reset()
        parts = []
        for block in self.blocks:
            final = self.rec.feed(block)
            if final:
                parts.append(final)
        tail = self.rec.finalize()
        if tail:
            parts.append(tail)
        self.rec.reset()
        return " ".join(parts).lower().split()


class ListenMode:
    wants_mic: bool = True

    @property
    def mic_state(self) -> str:
        return "listening"

    def process(self, data: bytes, now: float) -> list[VoiceEvent]:
        return []

    def finish(self) -> list[VoiceEvent]:
        return []


class WakeWordMode(ListenMode):
    """Mic always on. The wake word opens a command window (default 5 s)."""

    PREBUFFER_BLOCKS = 2  # replayed so "Hand, Claude" said in one breath is not cut
    STABLE_BLOCKS = 4     # 1 s without the heard text changing = the user has finished speaking

    def __init__(self, wake_recognizer: RecognizerLike, command_recognizer: RecognizerLike,
                 wake_word: str = "hand", window_seconds: float = 5.0,
                 open_recognizer: Optional[RecognizerLike] = None,
                 known: Optional[set[str]] = None):
        self.detector = WakeWordDetector(wake_recognizer, wake_word)
        self.wake_word = wake_word.strip().lower()
        self.cmd = command_recognizer
        self.open = OpenVocab(open_recognizer, known=known, wake_word=wake_word)
        self.window_seconds = window_seconds
        self._deadline: Optional[float] = None
        self._prebuf: deque[bytes] = deque(maxlen=self.PREBUFFER_BLOCKS)
        self._last_partial = ""
        self._stable = 0

    def _speech_finished(self) -> bool:
        """Vosk 0.3.x has no endpointer settings and sometimes never reports the end of a
        short phrase. Treat "text present and unchanged for 1 s" as the end of the phrase."""
        text = strip_unk(self.cmd.partial()).split()
        while text and text[0] == self.wake_word:
            text.pop(0)
        partial = " ".join(text)
        if partial and partial == self._last_partial:
            self._stable += 1
        else:
            self._stable = 0
            self._last_partial = partial
        return bool(partial) and self._stable >= self.STABLE_BLOCKS

    @property
    def window_open(self) -> bool:
        return self._deadline is not None

    @property
    def mic_state(self) -> str:
        return "window" if self.window_open else "listening"

    def process(self, data: bytes, now: float) -> list[VoiceEvent]:
        if self.window_open:
            return self._feed_command(data, now)
        self._prebuf.append(data)
        if not self.detector.feed(data):
            return []
        self._deadline = now + self.window_seconds
        self._last_partial, self._stable = "", 0
        self.cmd.reset()
        self.open.clear()
        events: list[VoiceEvent] = []
        for block in list(self._prebuf):
            events += self._feed_command(block, now, check_deadline=False)
            if not self.window_open:
                break
        self._prebuf.clear()
        return events

    def _feed_command(self, data: bytes, now: float, check_deadline: bool = True) -> list[VoiceEvent]:
        self.open.add(data)
        final = self.cmd.feed(data)
        if final is not None:
            text, alt = self.open.resolve(final)
            if text or alt:
                self._close()
                return [VoiceEvent("command_text", text, alt)]
            # only the wake word or silence: keep the window open
        elif check_deadline and self._speech_finished():
            text, alt = self.open.resolve(self.cmd.finalize())
            if text or alt:
                self._close()
                return [VoiceEvent("command_text", text, alt)]
        if check_deadline and self._deadline is not None and now >= self._deadline:
            text, alt = self.open.resolve(self.cmd.finalize())
            self._close()
            if text or alt:
                return [VoiceEvent("command_text", text, alt)]
            return [VoiceEvent("window_timeout")]      # the user is told nothing was understood
        return []

    def _close(self) -> None:
        self._last_partial, self._stable = "", 0
        self._deadline = None
        self._prebuf.clear()
        self.open.clear()
        self.cmd.reset()
        self.detector.reset()


class AlwaysMode(ListenMode):
    """Mic always on. Only utterances that start with the wake word are accepted;
    everything else is discarded silently."""

    def __init__(self, recognizer: RecognizerLike, wake_word: str = "hand",
                 open_recognizer: Optional[RecognizerLike] = None,
                 known: Optional[set[str]] = None):
        self.rec = recognizer
        self.wake_word = wake_word.strip().lower()
        self.open = OpenVocab(open_recognizer, known=known, wake_word=wake_word)
        self._last_partial = ""
        self._stable = 0

    STABLE_BLOCKS = 4

    def _emit(self, raw: str) -> list[VoiceEvent]:
        tokens = raw.lower().split()
        if not tokens or tokens[0] != self.wake_word:   # an [unk] before "hand" = chatter: reject
            self.open.clear()
            return []
        text, alt = self.open.resolve(" ".join(tokens[1:]))
        return [VoiceEvent("command_text", text, alt)] if (text or alt) else []

    def process(self, data: bytes, now: float) -> list[VoiceEvent]:
        self.open.add(data)
        final = self.rec.feed(data)
        if final is not None:
            self._last_partial, self._stable = "", 0
            return self._emit(final)
        # no endpointer in Vosk 0.3.x: "hand ..." unchanged for 1 s means the phrase is over
        partial = strip_unk(self.rec.partial())
        starts_with_wake = self.rec.partial().lower().split()[:1] == [self.wake_word]
        if starts_with_wake and partial == self._last_partial and len(partial.split()) > 1:
            self._stable += 1
        else:
            self._stable, self._last_partial = 0, (partial if starts_with_wake else "")
        if self._stable >= self.STABLE_BLOCKS:
            self._stable, self._last_partial = 0, ""
            return self._emit(self.rec.finalize())
        return []


class PushToTalkMode(ListenMode):
    """Mic opens only while the hotkey is held; no wake word needed."""

    def __init__(self, recognizer: RecognizerLike, open_recognizer: Optional[RecognizerLike] = None,
                 known: Optional[set[str]] = None, wake_word: str = "hand"):
        self.rec = recognizer
        self.open = OpenVocab(open_recognizer, known=known, wake_word=wake_word)
        self.held = False
        self._texts: list[str] = []
        self._alts: list[str] = []

    @property
    def wants_mic(self) -> bool:  # type: ignore[override]
        return self.held

    @property
    def mic_state(self) -> str:
        return "ptt" if self.held else "ptt_idle"

    def press(self) -> None:
        self._texts, self._alts = [], []
        self.open.clear()
        self.rec.reset()
        self.held = True

    def release(self) -> None:
        self.held = False

    def _collect(self, raw: str) -> None:
        text, alt = self.open.resolve(raw)
        if text or alt:
            self._texts.append(text)
            self._alts.append(alt or text)

    def process(self, data: bytes, now: float) -> list[VoiceEvent]:
        self.open.add(data)
        final = self.rec.feed(data)
        if final is not None:
            self._collect(final)
        return []

    def finish(self) -> list[VoiceEvent]:
        self._collect(self.rec.finalize())
        text = " ".join(t for t in self._texts if t)
        alt = " ".join(a for a in self._alts if a)
        self._texts, self._alts = [], []
        if alt == text:
            alt = ""
        return [VoiceEvent("command_text", text, alt)] if (text or alt) else []
