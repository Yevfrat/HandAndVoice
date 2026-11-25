"""Voice engine: one background thread that owns the microphone and recognizers.

It never touches Qt. Results go to a queue.Queue of VoiceEvent that the UI polls.
"""
from __future__ import annotations

import logging
import queue
import threading
import time
from typing import Callable, Optional

from voice.grammar import (
    PhraseSources, build_command_phrases, build_wake_grammar, validate_wake_word,
)
from voice.listen_modes import (
    AlwaysMode, ListenMode, PushToTalkMode, VoiceEvent, WakeWordMode,
)
from voice.microphone import Microphone, MicrophoneError
from voice.text_normalizer import UNK

log = logging.getLogger(__name__)


class VoiceEngine:
    def __init__(self, store, sources_provider: Callable[[], PhraseSources],
                 events: "queue.Queue[VoiceEvent]",
                 recognizer_factory: Optional[Callable[[str], object]] = None,
                 mic_factory: Optional[Callable[[object], object]] = None,
                 open_factory: Optional[Callable[[], object]] = None):
        self.store = store
        self.sources_provider = sources_provider
        self.events = events
        self._factory = recognizer_factory or self._default_factory
        self._real_models = recognizer_factory is None
        # open-vocabulary recognizer for "file ..." / "search ..." free text
        self._open_factory = open_factory or (self._default_open_factory if self._real_models else None)
        self._mic_factory = mic_factory or (lambda device: Microphone(device=device))
        self._stop = threading.Event()
        self._enabled = threading.Event()
        self._version = 0
        self._thread: Optional[threading.Thread] = None
        self._mode: Optional[ListenMode] = None
        self._last_state: Optional[str] = None

    # ---------- public API (called from the UI thread) ----------
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="voice-engine", daemon=True)
        self._thread.start()

    def request_stop(self) -> None:
        """Ask the thread to finish without waiting (so several threads can stop in parallel)."""
        self._stop.set()

    def shutdown(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3)

    def set_enabled(self, enabled: bool) -> None:
        if enabled:
            if not self._enabled.is_set():        # only a real off -> on change reopens the microphone
                self._version += 1
                self._enabled.set()
        else:
            self._enabled.clear()

    def reload(self) -> None:
        """Rebuild recognizers (listen mode, wake word or grammar changed)."""
        self._version += 1

    def ptt_press(self) -> None:
        mode = self._mode
        if isinstance(mode, PushToTalkMode):
            mode.press()

    def ptt_release(self) -> None:
        mode = self._mode
        if isinstance(mode, PushToTalkMode):
            mode.release()

    # ---------- internals ----------
    @staticmethod
    def _default_factory(grammar_json: str):
        from voice.recognizer import SpeechRecognizer, get_model

        return SpeechRecognizer(grammar_json, get_model())

    @staticmethod
    def _default_open_factory():
        from voice.recognizer import SpeechRecognizer, get_model

        return SpeechRecognizer(None, get_model())

    def _put(self, kind: str, text: str = "") -> None:
        self.events.put(VoiceEvent(kind, text))

    def _emit_state(self, state: str) -> None:
        if state != self._last_state:
            self._last_state = state
            self._put("mic_state", state)

    def _build_mode(self) -> ListenMode:
        s = self.store.settings
        wake = s["wake_word"]
        listen = s["listen_mode"]
        sources = self.sources_provider()
        validate_wake_word(wake, sources.command_phrases)

        vocabulary = None
        if self._real_models:
            from voice.recognizer import get_model, load_vocabulary

            self._put("info", "Loading speech model...")
            get_model()  # raises ModelMissingError with a friendly message
            vocabulary = load_vocabulary()
            if vocabulary is not None and wake not in vocabulary:
                raise RuntimeError(
                    f"The wake word '{wake}' is not a word the speech model knows, so it could never "
                    "be recognized. Choose another one in Settings.")

        grammar = build_command_phrases(
            sources, wake if listen == "ALWAYS" else None, vocabulary,
            absorb_wake_word=wake if listen != "ALWAYS" else None)
        self._put("grammar_skipped", "; ".join(grammar.skipped))
        command_json = grammar.to_json()

        open_rec = self._open_factory() if self._open_factory else None
        # phrases the grammar understands exactly (without the wake-word prefix of ALWAYS mode)
        prefix = f"{wake} "
        known = {(p[len(prefix):] if listen == "ALWAYS" and p.startswith(prefix) else p)
                 for p in grammar.phrases if p != UNK and p != wake}
        if listen == "ALWAYS":
            return AlwaysMode(self._factory(command_json), wake, open_rec, known)
        if listen == "PUSH_TO_TALK":
            return PushToTalkMode(self._factory(command_json), open_rec, known, wake)
        return WakeWordMode(self._factory(build_wake_grammar(wake)), self._factory(command_json),
                            wake, float(s["command_window_seconds"]), open_rec, known)

    def _run(self) -> None:
        mic = None
        mode: Optional[ListenMode] = None
        seen = -1
        failed_version = -2
        try:
            while not self._stop.is_set():
                if not self._enabled.is_set():
                    if mic is not None and mic.running:
                        mic.stop()
                    mode = None
                    self._mode = None
                    self._emit_state("off")
                    self._stop.wait(0.1)
                    continue

                if mode is None or seen != self._version:
                    if failed_version == self._version:
                        self._emit_state("off")
                        self._stop.wait(0.2)
                        continue
                    seen = self._version
                    if mic is not None and mic.running:
                        mic.stop()
                    try:
                        mic = self._mic_factory(self.store.get("microphone_device"))
                        mode = self._build_mode()
                        self._mode = mode
                    except Exception as exc:
                        log.error("Voice engine could not start: %s", exc)
                        self._put("error", str(exc))
                        mode = None
                        self._mode = None
                        failed_version = seen
                        continue

                try:
                    wants = mode.wants_mic
                    if wants and not mic.running:
                        mic.start()
                    if mic.running:
                        block = mic.read(0.25)
                        now = time.monotonic()
                        if block is not None:
                            for ev in mode.process(block, now):
                                self.events.put(ev)
                        if not wants:  # key released: flush what is left, then close the mic
                            while True:
                                b = mic.read_nowait()
                                if b is None:
                                    break
                                mode.process(b, now)
                            mic.stop()
                            for ev in mode.finish():
                                self.events.put(ev)
                    else:
                        self._stop.wait(0.05)
                    self._emit_state(mode.mic_state)
                except MicrophoneError as exc:
                    log.error("%s", exc)
                    self._put("error", str(exc))
                    failed_version = seen
                    mode = None
                    self._mode = None
                    self._emit_state("off")
                except Exception as exc:
                    log.exception("Voice engine error")
                    self._put("error", f"Voice engine error: {exc}")
                    failed_version = seen
                    mode = None
                    self._mode = None
                    self._emit_state("off")
        finally:
            if mic is not None:
                try:
                    mic.stop()
                except Exception:
                    log.exception("Error stopping microphone")
            self._mode = None
            self._emit_state("off")
