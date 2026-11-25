"""Validates a Command, asks for confirmation when needed, then calls the executor."""
from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass
from typing import Callable, Optional

from core.command import Command, CommandType, Result
from core.confirmation import ConfirmationManager
from core.feedback import Feedback
from core.mode_manager import ModeManager

log = logging.getLogger(__name__)

Executor = Callable[[Command], Result]
PICKER_TIMEOUT = 10.0  # seconds a numbered picker stays open

MODE_COMMANDS = {
    CommandType.MODE_PAUSE, CommandType.MODE_RESUME, CommandType.MODE_VOICE_ONLY,
    CommandType.MODE_GESTURE_ONLY, CommandType.MODE_HYBRID,
}


@dataclass
class _Countdown:
    label_fmt: str
    hint: str
    expires_at: float
    action: Optional[Callable[[], Result]]
    on_cancel: Optional[Callable[[], Result]]
    last_shown: int = -1


@dataclass
class _Picker:
    title: str
    options: list[str]
    on_pick: Callable[[str], None]
    expires_at: float


class Dispatcher:
    def __init__(self, parser, modes: ModeManager, confirmation: ConfirmationManager,
                 feedback: Feedback, executors: dict[CommandType, Executor],
                 descriptions: Optional[dict[str, str]] = None,
                 clock: Callable[[], float] = time.monotonic,
                 preparers: Optional[dict[CommandType, Callable[[Command], tuple[bool, str]]]] = None):
        self.parser = parser
        self.modes = modes
        self.confirmation = confirmation
        self.feedback = feedback
        self.executors = executors
        self.descriptions = descriptions or {}
        # A preparer runs when a confirmation is requested: it may refuse, pin the target
        # (e.g. which window to close) and add detail to the prompt.
        self.preparers = preparers or {}
        self._clock = clock
        self._picker: Optional[_Picker] = None
        self._countdown: Optional[_Countdown] = None
        self._last_countdown: Optional[int] = None

    # ---------- entry points ----------
    def handle_text(self, text: str, source: str = "voice", confidence: float = 1.0,
                    alt: str = "") -> None:
        """`text` is the reading from the small grammar; `alt` the full-vocabulary reading.

        The grammar reading wins when it is a valid command. Only when it is not, the
        alternative reading gets a chance (apps that are not in the small grammar).
        """
        def parse(t: str):
            return self.parser.parse(
                t, mode=self.modes.mode.value, source=source, confidence=confidence,
                confirmation_pending=self.confirmation.is_pending or self.countdown_active,
                picker_active=self.picker_active,
            )

        res = parse(text)
        shown = text
        if alt and res.kind in ("error", "ignored"):
            res_alt = parse(alt)
            if res_alt.kind in ("command", "picker"):
                res, shown = res_alt, alt
            elif res.kind == "ignored":
                return                       # noise: nothing valid in either reading
            else:
                shown = alt                  # the full-vocabulary words read better in the error
                res = type(res)("error", message=f"Command not recognized: {alt}")
        if res.kind == "ignored":
            return
        self.feedback.command(shown)
        text = shown
        if res.kind == "error":
            self.feedback.error(res.message)
            self.feedback.record_action(source, text, res.message, False)
        elif res.kind == "picker":
            self.open_picker(res.message, res.candidates, self._launch_by_name(source, text))
        elif res.command is not None:
            self.dispatch(res.command)

    def dispatch(self, cmd: Command) -> None:
        if (cmd.source == "voice" and not self.modes.voice_enabled
                and cmd.type not in MODE_COMMANDS):
            self._fail(cmd, f"Voice commands are off in {self.modes.mode.value.replace('_', ' ').title()} mode")
            return

        if cmd.source == "gesture" and not self.modes.hands_enabled:
            self._fail(cmd, "Gestures are off in this mode")
            return
        if cmd.source == "face" and not self.modes.face_enabled:
            self._fail(cmd, "Face expressions are off in this mode")
            return

        t = cmd.type
        if t == CommandType.CONFIRM:
            pending = self.confirmation.confirm()
            if pending is None:
                self._fail(cmd, "Nothing to confirm")
            else:
                self.feedback.clear()
                self._execute(pending.command)
        elif t in (CommandType.CANCEL, CommandType.ESCAPE):
            had_countdown = self._countdown is not None
            cancelled = self.cancel_all(cmd.source)
            if cancelled and not had_countdown:
                self.feedback.info("Cancelled")
                self.feedback.record_action(cmd.source, cmd.label(), "Cancelled", True)
            elif not cancelled:
                self.feedback.info("Nothing to cancel")
        elif t == CommandType.PICK_OPTION:
            self.pick(int(cmd.target or 0))
        elif cmd.requires_confirmation:
            desc = self.descriptions.get(t.value, t.value.replace("_", " ").title())
            prepare = self.preparers.get(t)
            if prepare is not None:
                ok, detail = prepare(cmd)
                if not ok:
                    self._fail(cmd, detail)
                    return
                desc = f"{desc}\n{detail}"
            self.confirmation.request(cmd, desc)
            self._last_countdown = None
            self._show_confirmation(cmd, desc)
        else:
            self._execute(cmd)

    # ---------- countdowns (sleep, pending shutdown) ----------
    @property
    def countdown_active(self) -> bool:
        return self._countdown is not None

    def start_countdown(self, label_fmt: str, seconds: float,
                        action: Optional[Callable[[], Result]] = None,
                        on_cancel: Optional[Callable[[], Result]] = None,
                        hint: str = "Say 'Hand, cancel' to stop it") -> None:
        """Show a cancellable countdown. `label_fmt` may contain {s} (seconds left).
        When it runs out `action` is executed; cancelling runs `on_cancel`."""
        self._countdown = _Countdown(label_fmt, hint, self._clock() + seconds, action, on_cancel)
        self._show_countdown()

    def cancel_all(self, source: str = "voice") -> bool:
        """Cancel a pending confirmation, picker or countdown. True if something was cancelled."""
        cancelled = self.confirmation.cancel()
        if self._picker is not None:
            self._picker, cancelled = None, True
        cd, self._countdown = self._countdown, None
        if cd is not None:
            cancelled = True
            result = cd.on_cancel() if cd.on_cancel else Result.success("Cancelled")
            self.report(source, "cancel", result)
        self.feedback.clear()
        return cancelled

    def _show_countdown(self) -> None:
        cd = self._countdown
        if cd is None:
            return
        secs = max(0, math.ceil(cd.expires_at - self._clock()))
        cd.last_shown = secs
        self.feedback.prompt(cd.label_fmt.format(s=secs), cd.hint)

    # ---------- picker ----------
    @property
    def picker_active(self) -> bool:
        if self._picker and self._clock() >= self._picker.expires_at:
            self._picker = None
            self.feedback.clear()
        return self._picker is not None

    def open_picker(self, title: str, options: list[str], on_pick: Callable[[str], None]) -> None:
        options = options[:9]
        self._picker = _Picker(title, options, on_pick, self._clock() + PICKER_TIMEOUT)
        self.feedback.picker(title, options)

    def pick(self, number: int) -> None:
        if not self.picker_active:
            self.feedback.error("No choices are pending")
            return
        picker = self._picker
        if number < 1 or number > len(picker.options):
            self.feedback.error(f"Choose a number from 1 to {len(picker.options)}")
            return
        self._picker = None
        self.feedback.clear()
        picker.on_pick(picker.options[number - 1])

    def _launch_by_name(self, source: str, raw: str) -> Callable[[str], None]:
        def on_pick(name: str) -> None:
            self.dispatch(Command(CommandType.LAUNCH_APP, name, source, 1.0,
                                  self.modes.mode.value, False, raw))
        return on_pick

    # ---------- timers (called by the UI every 30 ms) ----------
    def tick(self) -> None:
        expired = self.confirmation.pop_expired()
        if expired is not None:
            self.feedback.clear()
            self.feedback.info("Confirmation timed out", "Nothing was done")
            self.feedback.record_action("-", expired.command.label(), "Timed out", False)
            self._last_countdown = None
        elif self.confirmation.is_pending:
            secs = math.ceil(self.confirmation.remaining())
            if secs != self._last_countdown:
                self._last_countdown = secs
                p = self.confirmation.pending
                self._show_confirmation(p.command, p.description)
        self._tick_countdown()
        _ = self.picker_active  # expires a stale picker

    def _tick_countdown(self) -> None:
        cd = self._countdown
        if cd is None:
            return
        remaining = cd.expires_at - self._clock()
        if remaining <= 0:
            self._countdown = None
            self.feedback.clear()
            if cd.action is not None:
                self.report("-", cd.label_fmt.split(" in ")[0], cd.action())
        elif math.ceil(remaining) != cd.last_shown:
            self._show_countdown()

    # ---------- internals ----------
    def _show_confirmation(self, cmd: Command, description: str) -> None:
        secs = math.ceil(self.confirmation.remaining())
        self.feedback.prompt(
            f"Confirm: {cmd.label()}",
            f"{description}\nSay 'Hand, confirm' or make the OK gesture. "
            f"'Hand, cancel' aborts. ({secs}s)",
        )

    def _fail(self, cmd: Command, message: str) -> None:
        self.feedback.error(message)
        self.feedback.record_action(cmd.source, cmd.label(), message, False)

    def _execute(self, cmd: Command) -> None:
        fn = self.executors.get(cmd.type)
        if fn is None:
            self._fail(cmd, f"'{cmd.label()}' is not available yet in this build")
            return
        # Every executed command is logged (text stays on this computer) so an unexpected
        # action can be traced to what was heard.
        log.info("Running %s target=%r source=%s heard=%r", cmd.type.value, cmd.target, cmd.source,
                 getattr(cmd, "raw", ""))
        try:
            result = fn(cmd)
        except Exception:
            log.exception("Executor for %s failed", cmd.type.value)
            self._fail(cmd, "Unexpected error while running the command")
            return
        if result.show:
            self.report(cmd.source, cmd.label(), result)

    def report(self, source: str, label: str, result: Result) -> None:
        """Show a result to the user and add it to the action history."""
        if result.ok:
            self.feedback.success(result.message)
        else:
            self.feedback.error(result.message)
        self.feedback.record_action(source, label, result.message, result.ok)
