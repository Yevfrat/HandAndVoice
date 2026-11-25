"""Global hotkeys via pynput: the push-to-talk key and the Ctrl+Alt+R resume hotkey.

Only these specific keys are matched. Key presses are never stored or logged.
"""
from __future__ import annotations

import logging
from typing import Callable, Optional

log = logging.getLogger(__name__)

MODIFIER_GROUPS = {
    "ctrl": {"ctrl", "ctrl_l", "ctrl_r"},
    "alt": {"alt", "alt_l", "alt_r", "alt_gr"},
    "shift": {"shift", "shift_l", "shift_r"},
    "win": {"cmd", "cmd_l", "cmd_r"},
}
MODIFIER_ALIASES = {"control": "ctrl", "cmd": "win", "windows": "win"}


def parse_hotkey(text: str) -> tuple[frozenset[str], str]:
    """"ctrl+alt+r" -> (frozenset({"ctrl", "alt"}), "r")."""
    parts = [p.strip().lower() for p in text.split("+") if p.strip()]
    if not parts:
        raise ValueError("Empty hotkey")
    *mods, final = parts
    groups = []
    for m in mods:
        m = MODIFIER_ALIASES.get(m, m)
        if m not in MODIFIER_GROUPS:
            raise ValueError(f"Unknown modifier {m!r}")
        groups.append(m)
    return frozenset(groups), final


def key_id(key) -> Optional[str]:
    """Canonical lowercase name for a pynput key ('ctrl_r', 'r', 'f5'...)."""
    name = getattr(key, "name", None)
    if name:
        return name
    vk = getattr(key, "vk", None)
    if vk is not None and (0x30 <= vk <= 0x39 or 0x41 <= vk <= 0x5A):
        return chr(vk).lower()  # Ctrl+R reports a control char; the virtual key is reliable
    char = getattr(key, "char", None)
    return char.lower() if char else None


class HotkeyListener:
    def __init__(self, ptt_key: str, resume_hotkey: str,
                 on_ptt_press: Callable[[], None], on_ptt_release: Callable[[], None],
                 on_resume: Callable[[], None], ptt_enabled: Callable[[], bool]):
        self._on_ptt_press = on_ptt_press
        self._on_ptt_release = on_ptt_release
        self._on_resume = on_resume
        self._ptt_enabled = ptt_enabled
        self._pressed: set[str] = set()
        self._ptt_held = False
        self._listener = None
        self._capture: Optional[Callable[[str], None]] = None
        self.configure(ptt_key, resume_hotkey)

    def configure(self, ptt_key: str, resume_hotkey: str) -> None:
        self._ptt_key = ptt_key.strip().lower()
        try:
            self._resume_mods, self._resume_key = parse_hotkey(resume_hotkey)
        except ValueError as exc:
            log.warning("Invalid resume hotkey %r (%s); using ctrl+alt+r", resume_hotkey, exc)
            self._resume_mods, self._resume_key = parse_hotkey("ctrl+alt+r")

    def start(self) -> None:
        from pynput import keyboard

        if self._listener is not None:
            return
        self._listener = keyboard.Listener(on_press=self._press, on_release=self._release)
        self._listener.daemon = True
        self._listener.start()

    def stop(self) -> None:
        if self._listener is not None:
            self._listener.stop()
            self._listener = None

    def _mods_down(self, group: str) -> bool:
        return bool(self._pressed & MODIFIER_GROUPS[group])

    def capture_next(self, callback: Callable[[str], None]) -> None:
        """The next key press is reported to `callback` (Settings: "press a key") and is
        not treated as a hotkey. The key name is the only thing kept."""
        self._capture = callback

    def cancel_capture(self) -> None:
        self._capture = None

    def _press(self, key) -> None:
        kid = key_id(key)
        if kid is None:
            return
        capture, self._capture = self._capture, None
        if capture is not None:
            capture(kid)
            return
        self._pressed.add(kid)
        if kid == self._ptt_key and not self._ptt_held and self._ptt_enabled():
            self._ptt_held = True
            self._on_ptt_press()
        if kid == self._resume_key and all(self._mods_down(g) for g in self._resume_mods):
            self._on_resume()

    def _release(self, key) -> None:
        kid = key_id(key)
        if kid is None:
            return
        self._pressed.discard(kid)
        if kid == self._ptt_key and self._ptt_held:
            self._ptt_held = False
            self._on_ptt_release()
