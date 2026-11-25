"""Messages for the UI/overlay plus in-memory history (never written to disk)."""
from __future__ import annotations

import logging
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Callable

log = logging.getLogger(__name__)


@dataclass
class FeedbackEvent:
    kind: str        # heard | command | success | error | info | prompt | picker | clear
    title: str = ""
    body: str = ""
    persistent: bool = False
    options: list[str] = field(default_factory=list)


@dataclass
class ActionRecord:
    time: str
    source: str
    command: str
    result: str
    ok: bool


class Feedback:
    def __init__(self):
        self._listeners: list[Callable[[FeedbackEvent], None]] = []
        self.actions: deque[ActionRecord] = deque(maxlen=10)
        self.texts: deque[str] = deque(maxlen=20)
        self.readings: deque[str] = deque(maxlen=30)   # distinct recent readings (for "teach a variant")

    def add_listener(self, fn: Callable[[FeedbackEvent], None]) -> None:
        self._listeners.append(fn)

    def emit(self, event: FeedbackEvent) -> None:
        for fn in list(self._listeners):
            try:
                fn(event)
            except Exception:  # a broken UI listener must never break command handling
                log.exception("Feedback listener failed")

    # convenience emitters
    def heard(self, text: str, alt: str = "") -> None:
        self.texts.appendleft(text + (f"   (also heard: {alt})" if alt else ""))
        for reading in (text, alt):
            if reading and reading not in self.readings:
                self.readings.appendleft(reading)
        self.emit(FeedbackEvent("heard", text))

    def command(self, text: str) -> None:
        self.emit(FeedbackEvent("command", text))

    def success(self, title: str, body: str = "") -> None:
        self.emit(FeedbackEvent("success", title, body))

    def error(self, title: str, body: str = "") -> None:
        log.warning("Error shown to user: %s %s", title, body)
        self.emit(FeedbackEvent("error", title, body))

    def info(self, title: str, body: str = "") -> None:
        self.emit(FeedbackEvent("info", title, body))

    def prompt(self, title: str, body: str = "") -> None:
        self.emit(FeedbackEvent("prompt", title, body, persistent=True))

    def picker(self, title: str, options: list[str]) -> None:
        self.emit(FeedbackEvent("picker", title, "", persistent=True, options=list(options)))

    def clear(self) -> None:
        self.emit(FeedbackEvent("clear"))

    def record_action(self, source: str, command: str, result: str, ok: bool) -> None:
        self.actions.appendleft(ActionRecord(time.strftime("%H:%M:%S"), source, command, result, ok))
