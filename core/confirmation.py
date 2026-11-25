"""Pending-confirmation state with timeout."""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable, Optional

from core.command import Command


@dataclass
class PendingConfirmation:
    command: Command
    description: str
    expires_at: float


class ConfirmationManager:
    def __init__(self, timeout: float = 5.0, clock: Callable[[], float] = time.monotonic):
        self.timeout = timeout
        self._clock = clock
        self._pending: Optional[PendingConfirmation] = None

    def request(self, command: Command, description: str) -> PendingConfirmation:
        self._pending = PendingConfirmation(command, description, self._clock() + self.timeout)
        return self._pending

    @property
    def pending(self) -> Optional[PendingConfirmation]:
        if self._pending and self._clock() >= self._pending.expires_at:
            return None
        return self._pending

    @property
    def is_pending(self) -> bool:
        return self.pending is not None

    def remaining(self) -> float:
        p = self.pending
        return max(0.0, p.expires_at - self._clock()) if p else 0.0

    def confirm(self) -> Optional[PendingConfirmation]:
        p = self.pending
        self._pending = None
        return p

    def cancel(self) -> bool:
        had = self.pending is not None
        self._pending = None
        return had

    def pop_expired(self) -> Optional[PendingConfirmation]:
        """Return (and clear) the pending confirmation if it has timed out."""
        if self._pending and self._clock() >= self._pending.expires_at:
            p, self._pending = self._pending, None
            return p
        return None
