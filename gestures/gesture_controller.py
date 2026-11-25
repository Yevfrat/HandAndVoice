"""Turns gesture events into actions (spec 4.4, 4.5, 9). Qt-free; runs on the UI thread.

Modes: NORMAL, SWITCH_WINDOW (Alt held while choosing a window), BROWSER (tab stepping),
VOLUME (hand height changes the volume). Everything goes through fixed tables; destructive
actions use the dispatcher's confirmation / countdown like voice does.

SAFETY: this class may hold the Alt key down. release_all() releases it and is called whenever
the camera stops, the mode changes, an error happens, on timeout and on shutdown.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

from automation.web_controller import is_browser_process
from core.command import Command, CommandType
from gestures.types import Gesture, GestureEvent

log = logging.getLogger(__name__)

NORMAL, SWITCH, BROWSER, VOLUME = "NORMAL", "SWITCH_WINDOW", "BROWSER", "VOLUME"
ELEVATED_MESSAGE = "Target app runs as administrator; keyboard input is blocked by Windows"

SCROLL_INTERVAL = 0.080        # one wheel notch every 80 ms
STEP_INTERVAL = 0.4            # between selection / tab steps
VOLUME_INTERVAL = 0.15
VOLUME_MOVE = 0.10             # wrist travel (x hand size) per volume tick
SWITCH_TIMEOUT = 8.0
BROWSER_TIMEOUT = 10.0
STEP_HOLD = 0.4                # hold needed for a swipe step inside the modes (>= 5 frames)

HELP_LINES = [
    "👍 Close all apps   👎 Close window   ✊ Sleep",
    "✋ This panel   ✌️ Browser mode   👆 Switch window",
    "☝️/👇 Scroll   👉/👈 Next/prev   👌 Enter / confirm",
    "🤘 Play/pause   🤙 Volume   🤟 Escape / cancel",
]


@dataclass
class GestureUI:
    """Callbacks into the UI layer (set by main.py)."""
    toggle_overlay: Callable[[], None] = lambda: None
    close_overlay: Callable[[], None] = lambda: None


@dataclass
class _State:
    mode: str = NORMAL
    alt_held: bool = False
    entered: float = 0.0
    last_activity: float = 0.0
    last_step: float = -10.0
    last_scroll: float = -10.0
    last_volume: float = -10.0
    volume_anchor: Optional[float] = None
    scroll_blocked: bool = False
    cooldown: dict = field(default_factory=dict)


class GestureController:
    def __init__(self, dispatcher, window_commands, media, feedback, gestures_provider: Callable[[], dict],
                 settings_provider: Callable[[], dict], hold_overrides: dict,
                 confirm_types: set[CommandType], keyboard=None, wm=None, ui: Optional[GestureUI] = None,
                 clock: Callable[[], float] = time.monotonic):
        self.dispatcher = dispatcher
        self.window_commands = window_commands
        self.media = media
        self.feedback = feedback
        self.gestures_provider = gestures_provider
        self.settings_provider = settings_provider
        self.hold_overrides = hold_overrides
        self.confirm_types = confirm_types
        self._keyboard = keyboard
        self._wm = wm
        self.ui = ui or GestureUI()
        self._clock = clock
        self.s = _State()

    # lazily bound so the module imports without touching Windows
    @property
    def keyboard(self):
        if self._keyboard is None:
            from automation import keyboard_controller

            self._keyboard = keyboard_controller
        return self._keyboard

    @property
    def wm(self):
        if self._wm is None:
            from automation import window_manager

            self._wm = window_manager
        return self._wm

    # ------------------------------------------------------------------ state
    @property
    def mode(self) -> str:
        return self.s.mode

    @property
    def switch_active(self) -> bool:
        return self.s.mode == SWITCH

    def _foreground(self):
        try:
            return self.wm.foreground_info()
        except Exception:
            return None

    def _browser_in_front(self) -> bool:
        info = self._foreground()
        return bool(info) and is_browser_process(info.process)

    def _blocked(self) -> Optional[str]:
        info = self._foreground()
        if info is not None and info.elevated:
            return ELEVATED_MESSAGE
        return None

    # ------------------------------------------------------------------ safety
    def release_all(self) -> None:
        """Release Alt if held and return to NORMAL. Safe to call at any time."""
        try:
            if self.s.alt_held:
                self.keyboard.key_up("alt")
        except Exception:
            log.exception("Could not release Alt")
        finally:
            self.s.alt_held = False
            self._set_mode(NORMAL)

    def _set_mode(self, mode: str) -> None:
        self.s.mode = mode
        self.hold_overrides.clear()
        if mode in (SWITCH, BROWSER):
            self.hold_overrides.update({Gesture.POINT_RIGHT.value: STEP_HOLD, Gesture.POINT_LEFT.value: STEP_HOLD})
        now = self._clock()
        self.s.entered = self.s.last_activity = now
        self.s.volume_anchor = None

    def _fail(self, message: str) -> None:
        self.feedback.error(message)
        self.release_all()

    # ------------------------------------------------------------------ timers
    def tick(self) -> None:
        """Called every 30 ms by the UI loop: mode timeouts."""
        now = self._clock()
        if self.s.mode == SWITCH and now - self.s.last_activity >= SWITCH_TIMEOUT:
            self._cancel_switch("Switch Window timed out")
        elif self.s.mode == BROWSER and now - self.s.last_activity >= BROWSER_TIMEOUT:
            self.release_all()
            self.feedback.info("Browser mode ended", "Timed out")

    # ------------------------------------------------------------------ events
    def handle(self, ev: GestureEvent) -> None:
        try:
            self._handle(ev)
        except Exception as exc:
            name = type(exc).__name__
            log.exception("Gesture action failed")
            self._fail(str(exc) if name == "InputBlockedError" else "A gesture action failed")

    def _handle(self, ev: GestureEvent) -> None:
        name, phase, now = ev.name, ev.phase, self._clock()
        if phase == "end":
            if name == Gesture.POINT_UP.value or name == Gesture.POINT_DOWN.value:
                self.s.scroll_blocked = False
            if name == Gesture.SHAKA.value and self.s.mode == VOLUME:
                self.release_all()
                self.feedback.info("Volume mode ended")
            return
        if self.s.mode != NORMAL:
            self.s.last_activity = now

        if name == Gesture.ILY.value and phase == "start":
            return self._escape()

        if self.s.mode == VOLUME:
            if name == Gesture.SHAKA.value:
                self._volume(ev, now)
            return

        if self.s.mode in (SWITCH, BROWSER) and name in (Gesture.POINT_RIGHT.value, Gesture.POINT_LEFT.value):
            self._step(+1 if name == Gesture.POINT_RIGHT.value else -1, now)
            return
        if self.s.mode == SWITCH and name == Gesture.OK.value and phase == "start":
            return self._switch_confirm()

        if name in (Gesture.POINT_UP.value, Gesture.POINT_DOWN.value):
            return self._scroll(+1 if name == Gesture.POINT_UP.value else -1, phase, now)
        if phase != "start":
            return                                   # ticks only matter for scroll, steps and volume
        self._normal_start(name, now)

    # ------------------------------------------------------------------ normal mode
    def _normal_start(self, name: str, now: float) -> None:
        action = self.gestures_provider().get(name, {}).get("action", "")
        handlers = {
            "ENTER_SWITCH_WINDOW": self._enter_switch,
            "TOGGLE_OVERLAY": lambda: self.ui.toggle_overlay(),
            "ENTER_BROWSER_MODE": self._enter_browser,
            "NEXT_WINDOW_OR_TAB": lambda: self._next_prev(+1, name, now),
            "PREVIOUS_WINDOW_OR_TAB": lambda: self._next_prev(-1, name, now),
            "PLAY_PAUSE": lambda: self._dispatch(CommandType.PLAY_MEDIA, "rock"),
            "ENTER_VOLUME_MODE": lambda: self._enter_volume(now),
            "CLOSE_ALL_APPS": lambda: self._dispatch(CommandType.CLOSE_ALL_APPS, "thumbs up"),
            "CLOSE_WINDOW": lambda: self._dispatch(CommandType.CLOSE_WINDOW, "thumbs down"),
            "SLEEP": lambda: self._dispatch(CommandType.SLEEP, "fist"),
            "ESCAPE": self._escape,
            "ENTER_OR_CONFIRM": self._ok,
        }
        handler = handlers.get(action)
        if handler is None:
            log.debug("No handler for gesture %s (%s)", name, action)
            return
        handler()

    def _dispatch(self, ctype: CommandType, raw: str) -> None:
        cmd = Command(ctype, None, "gesture", 1.0, self.dispatcher.modes.mode.value,
                      ctype in self.confirm_types, raw)
        self.dispatcher.dispatch(cmd)

    def _next_prev(self, direction: int, name: str, now: float) -> None:
        if now < self.s.cooldown.get(name, 0.0):
            return
        self.s.cooldown[name] = now + float(self.settings_provider().get("cooldown_seconds", 1.0))
        browser = self._browser_in_front()
        if direction > 0:
            self._dispatch(CommandType.NEXT_TAB if browser else CommandType.NEXT_WINDOW, "point right")
        else:
            self._dispatch(CommandType.PREVIOUS_TAB if browser else CommandType.PREVIOUS_WINDOW, "point left")

    def _ok(self) -> None:
        if self.dispatcher.confirmation.is_pending:
            self._dispatch(CommandType.CONFIRM, "ok")
            return
        if self.s.mode == SWITCH:
            self._switch_confirm()
            return
        blocked = self._blocked()
        if blocked:
            self.feedback.error(blocked)
            return
        self.keyboard.press("enter")
        self.feedback.info("Enter")

    def _escape(self) -> None:
        """Cancel a pending confirmation / countdown / picker, leave every mode, close the overlay."""
        was_switch = self.s.mode == SWITCH
        left_mode = self.s.mode != NORMAL
        if was_switch:
            self._cancel_switch(None)                 # Escape + release Alt: no change of focus
        elif left_mode:
            self.release_all()
        self.ui.close_overlay()                       # first: it must not hide the messages below
        had_countdown = self.dispatcher.countdown_active
        cancelled = self.dispatcher.cancel_all("gesture")
        if cancelled and not had_countdown:
            self.feedback.info("Cancelled")           # (a countdown reports its own result)
        elif left_mode and not cancelled:
            self.feedback.info("Cancelled")
        elif not cancelled:
            self.feedback.info("Escape")

    # ------------------------------------------------------------------ scroll
    def _scroll(self, sign: int, phase: str, now: float) -> None:
        if phase == "start":
            blocked = self._blocked()
            self.s.scroll_blocked = bool(blocked)
            if blocked:
                self.feedback.error(blocked)
                return
        if self.s.scroll_blocked:
            return
        if now - self.s.last_scroll >= SCROLL_INTERVAL:
            self.s.last_scroll = now
            self.keyboard.send_wheel(sign)           # at the cursor position; the cursor does not move

    # ------------------------------------------------------------------ switch window
    def _enter_switch(self) -> None:
        blocked = self._blocked()
        if blocked:
            self.feedback.error(blocked)
            return
        self.keyboard.key_down("alt")
        self.s.alt_held = True
        self._set_mode(SWITCH)
        try:
            self.keyboard.press("tab")               # Alt is held: this opens the Windows switcher
        except Exception:
            self.release_all()
            raise
        self.feedback.info("Switch Window", "Point left/right to choose, 👌 to select, 🤟 to cancel")

    def _switch_confirm(self) -> None:
        self.keyboard.key_up("alt")                  # releasing Alt selects the highlighted window
        self.s.alt_held = False
        self._set_mode(NORMAL)
        self.feedback.success("Window selected")

    def _cancel_switch(self, message: Optional[str]) -> None:
        try:
            self.keyboard.press("escape")
        except Exception:
            log.exception("Escape failed")
        self.release_all()                           # releases Alt
        if message:
            self.feedback.info(message)

    def step_selection(self, direction: int) -> bool:
        """Voice 'Next/Previous Window' during Switch Window mode. False if the mode is not active."""
        if self.s.mode != SWITCH:
            return False
        self.s.last_activity = self._clock()
        self.keyboard.press("right" if direction > 0 else "left")
        return True

    # ------------------------------------------------------------------ browser mode
    def _enter_browser(self) -> None:
        if not self._browser_in_front():
            self.feedback.error("Browser mode needs a browser in focus")
            return
        blocked = self._blocked()
        if blocked:
            self.feedback.error(blocked)
            return
        self._set_mode(BROWSER)
        self.feedback.info("Browser mode", "Point left/right to change tab, 🤟 to leave")

    def _step(self, direction: int, now: float) -> None:
        if now - self.s.last_step < STEP_INTERVAL:
            return
        self.s.last_step = now
        if self.s.mode == SWITCH:
            self.keyboard.press("right" if direction > 0 else "left")
        else:
            self.keyboard.press("ctrl+tab" if direction > 0 else "ctrl+shift+tab")

    # ------------------------------------------------------------------ volume mode
    def _enter_volume(self, now: float) -> None:
        blocked = self._blocked()
        if blocked:
            self.feedback.error(blocked)
            return
        self._set_mode(VOLUME)
        self.feedback.info("Volume mode", "Move your hand up / down. Lower the hand or 🤟 to stop")

    def _volume(self, ev: GestureEvent, now: float) -> None:
        if ev.phase not in ("tick", "start"):
            return
        if self.s.volume_anchor is None:
            self.s.volume_anchor = ev.y
            return
        if ev.size <= 0 or now - self.s.last_volume < VOLUME_INTERVAL:
            return
        moved = self.s.volume_anchor - ev.y          # image y grows downwards: up = positive
        if abs(moved) > VOLUME_MOVE * ev.size:
            self.media.press("volume_up" if moved > 0 else "volume_down")
            self.s.volume_anchor = ev.y
            self.s.last_volume = now
