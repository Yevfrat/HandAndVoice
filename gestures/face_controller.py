"""Turns wink events into actions (Face Only mode). Qt-free; runs on the UI thread.

Right eye closed = next page, left eye closed = previous page. What "page" means is a setting:
  TABS     browser tab (Ctrl+Tab); outside a browser the next / previous window
  PAGES    Page Down / Page Up (documents, PDFs, long web pages)
  HISTORY  browser Forward / Back
"""
from __future__ import annotations

import logging
from typing import Callable, Optional

from automation.web_controller import is_browser_process
from core.command import Command, CommandType
from gestures.face import WINK_LEFT, WINK_RIGHT
from gestures.types import GestureEvent

log = logging.getLogger(__name__)

ELEVATED_MESSAGE = "Target app runs as administrator; keyboard input is blocked by Windows"
STYLES = ("TABS", "PAGES", "HISTORY")


class FaceController:
    def __init__(self, dispatcher, feedback, settings_provider: Callable[[], dict], keyboard=None, wm=None):
        self.dispatcher = dispatcher
        self.feedback = feedback
        self.settings_provider = settings_provider
        self._keyboard = keyboard
        self._wm = wm

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

    def _foreground(self):
        try:
            return self.wm.foreground_info()
        except Exception:
            return None

    def handle(self, ev: GestureEvent) -> None:
        if ev.phase != "start":
            return
        try:
            if ev.name == WINK_RIGHT:
                self._step(+1)
            elif ev.name == WINK_LEFT:
                self._step(-1)
        except Exception as exc:
            log.exception("Face action failed")
            self.feedback.error(str(exc) if type(exc).__name__ == "InputBlockedError" else "A face action failed")

    def _step(self, direction: int) -> None:
        style = self.settings_provider().get("face_wink_style", "TABS")
        info = self._foreground()
        if info is not None and info.elevated:
            self.feedback.error(ELEVATED_MESSAGE)
            return
        if style == "PAGES":
            self.keyboard.press("pagedown" if direction > 0 else "pageup")
            self.feedback.info("Next page" if direction > 0 else "Previous page")
        elif style == "HISTORY":
            self.keyboard.press("alt+right" if direction > 0 else "alt+left")
            self.feedback.info("Forward" if direction > 0 else "Back")
        else:
            browser = info is not None and is_browser_process(info.process)
            if direction > 0:
                ctype = CommandType.NEXT_TAB if browser else CommandType.NEXT_WINDOW
            else:
                ctype = CommandType.PREVIOUS_TAB if browser else CommandType.PREVIOUS_WINDOW
            self.dispatcher.dispatch(Command(ctype, None, "face", 1.0, self.dispatcher.modes.mode.value,
                                             False, "wink right" if direction > 0 else "wink left"))
