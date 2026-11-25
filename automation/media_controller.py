"""Volume, mute and play/pause through the Windows media keys."""
from __future__ import annotations

import logging

from core.command import Command, CommandType, Result

log = logging.getLogger(__name__)

# Each volume key press changes the volume by 2 %.
KEYS = {
    CommandType.VOLUME_UP: ("volume_up", "Volume up"),
    CommandType.VOLUME_DOWN: ("volume_down", "Volume down"),
    CommandType.MUTE: ("volume_mute", "Mute toggled"),
    # Both voice commands send the same toggle key (Windows has only play/pause).
    CommandType.PLAY_MEDIA: ("media_play_pause", "Play/Pause toggled"),
    CommandType.PAUSE_MEDIA: ("media_play_pause", "Play/Pause toggled"),
}
HANDLED = set(KEYS)


class MediaController:
    def __init__(self, keyboard=None):
        self._keyboard = keyboard

    @property
    def keyboard(self):
        if self._keyboard is None:
            from automation import keyboard_controller

            self._keyboard = keyboard_controller
        return self._keyboard

    def press(self, key: str) -> None:
        self.keyboard.press(key)

    def execute(self, cmd: Command) -> Result:
        entry = KEYS.get(cmd.type)
        if entry is None:
            return Result.error(f"'{cmd.label()}' is not a media command")
        key, message = entry
        try:
            self.press(key)
        except Exception as exc:
            log.warning("Media key %s failed", key)
            return Result.error(str(exc))
        return Result.success(message)
