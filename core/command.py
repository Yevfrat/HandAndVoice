"""Command dataclass, CommandType enum and the Result of executing a command."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional


class CommandType(str, Enum):
    # Launchers / prefixed
    LAUNCH_APP = "LAUNCH_APP"
    FILE_OPEN = "FILE_OPEN"
    WEB_OPEN = "WEB_OPEN"
    SEARCH = "SEARCH"
    PICK_OPTION = "PICK_OPTION"
    # Window
    SWITCH_WINDOW = "SWITCH_WINDOW"
    TASK_VIEW = "TASK_VIEW"
    MINIMIZE = "MINIMIZE"
    MAXIMIZE = "MAXIMIZE"
    CLOSE_WINDOW = "CLOSE_WINDOW"
    FULLSCREEN = "FULLSCREEN"
    SHOW_DESKTOP = "SHOW_DESKTOP"
    NEXT_WINDOW = "NEXT_WINDOW"
    PREVIOUS_WINDOW = "PREVIOUS_WINDOW"
    # Browser
    NEW_TAB = "NEW_TAB"
    CLOSE_TAB = "CLOSE_TAB"
    NEXT_TAB = "NEXT_TAB"
    PREVIOUS_TAB = "PREVIOUS_TAB"
    REFRESH = "REFRESH"
    BACK = "BACK"
    FORWARD = "FORWARD"
    SCROLL_UP = "SCROLL_UP"
    SCROLL_DOWN = "SCROLL_DOWN"
    PAGE_UP = "PAGE_UP"
    PAGE_DOWN = "PAGE_DOWN"
    GO_TO_TOP = "GO_TO_TOP"
    GO_TO_BOTTOM = "GO_TO_BOTTOM"
    # System
    VOLUME_UP = "VOLUME_UP"
    VOLUME_DOWN = "VOLUME_DOWN"
    MUTE = "MUTE"
    PAUSE_MEDIA = "PAUSE_MEDIA"
    PLAY_MEDIA = "PLAY_MEDIA"
    SCREENSHOT = "SCREENSHOT"
    LOCK_SCREEN = "LOCK_SCREEN"
    SLEEP = "SLEEP"
    SHUTDOWN = "SHUTDOWN"
    RESTART = "RESTART"
    CLOSE_ALL_APPS = "CLOSE_ALL_APPS"
    # Modes
    MODE_PAUSE = "MODE_PAUSE"
    MODE_RESUME = "MODE_RESUME"
    MODE_VOICE_ONLY = "MODE_VOICE_ONLY"
    MODE_GESTURE_ONLY = "MODE_GESTURE_ONLY"
    MODE_HYBRID = "MODE_HYBRID"
    # Confirmation
    CONFIRM = "CONFIRM"
    CANCEL = "CANCEL"
    ESCAPE = "ESCAPE"
    # Listen modes
    LISTEN_WAKE_WORD = "LISTEN_WAKE_WORD"
    LISTEN_ALWAYS = "LISTEN_ALWAYS"
    LISTEN_PUSH_TO_TALK = "LISTEN_PUSH_TO_TALK"


@dataclass
class Command:
    type: CommandType
    target: Optional[str] = None
    source: str = "voice"            # "voice" | "gesture" | "ui"
    confidence: float = 1.0          # 0-1
    mode: str = "HYBRID"             # active mode when the command was created
    requires_confirmation: bool = False
    raw: str = ""                    # recognized text or gesture name

    def label(self) -> str:
        return self.raw or self.type.value.replace("_", " ").title()


@dataclass
class Result:
    ok: bool
    message: str
    show: bool = True     # False: the executor already reported (e.g. it opened a picker)

    @classmethod
    def success(cls, message: str) -> "Result":
        return cls(True, message)

    @classmethod
    def error(cls, message: str) -> "Result":
        return cls(False, message)
