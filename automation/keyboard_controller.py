"""Keyboard and mouse-wheel input through SendInput (no pyautogui, no clipboard).

Normal keys and modifiers use hardware scan codes; text uses KEYEVENTF_UNICODE.
"""
from __future__ import annotations

import ctypes
import logging
import time
from ctypes import wintypes
from typing import Iterable, Union

log = logging.getLogger(__name__)

INPUT_MOUSE, INPUT_KEYBOARD = 0, 1
KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
KEYEVENTF_SCANCODE = 0x0008
MOUSEEVENTF_WHEEL = 0x0800
WHEEL_DELTA = 120
MAPVK_VK_TO_VSC = 0

ULONG_PTR = ctypes.c_size_t


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD), ("wParamH", wintypes.WORD)]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]


_user32 = ctypes.WinDLL("user32", use_last_error=True)
_user32.SendInput.argtypes = (wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int)
_user32.SendInput.restype = wintypes.UINT
_user32.MapVirtualKeyW.argtypes = (wintypes.UINT, wintypes.UINT)
_user32.MapVirtualKeyW.restype = wintypes.UINT


class InputBlockedError(RuntimeError):
    """Windows refused the input (usually the target app runs as administrator)."""


VK = {
    "ctrl": 0xA2, "shift": 0xA0, "alt": 0xA4, "win": 0x5B,
    "tab": 0x09, "enter": 0x0D, "return": 0x0D, "escape": 0x1B, "esc": 0x1B, "space": 0x20,
    "backspace": 0x08, "delete": 0x2E, "insert": 0x2D,
    "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28,
    "home": 0x24, "end": 0x23, "pageup": 0x21, "pagedown": 0x22,
    "printscreen": 0x2C,
    "volume_mute": 0xAD, "volume_down": 0xAE, "volume_up": 0xAF, "media_play_pause": 0xB3,
}
VK.update({f"f{i}": 0x6F + i for i in range(1, 13)})
EXTENDED = {0x25, 0x26, 0x27, 0x28, 0x24, 0x23, 0x21, 0x22, 0x2D, 0x2E, 0x5B, 0x2C,
            0xAD, 0xAE, 0xAF, 0xB3}
MODIFIERS = ("ctrl", "shift", "alt", "win")


def vk_for(name: str) -> int:
    key = name.strip().lower()
    if key in VK:
        return VK[key]
    if len(key) == 1 and (key.isalnum()):
        return ord(key.upper())
    raise ValueError(f"Unknown key: {name!r}")


MEDIA_VKS = {0xAD, 0xAE, 0xAF, 0xB3}   # mute, volume down/up, play/pause: sent by virtual key


def _key_input(vk: int, up: bool) -> INPUT:
    inp = INPUT(type=INPUT_KEYBOARD)
    if vk in MEDIA_VKS:
        # Media keys have no useful scan code; Windows expects the virtual key.
        flags = KEYEVENTF_EXTENDEDKEY | (KEYEVENTF_KEYUP if up else 0)
        inp.ki = KEYBDINPUT(vk, 0, flags, 0, 0)
        return inp
    scan = _user32.MapVirtualKeyW(vk, MAPVK_VK_TO_VSC)
    flags = KEYEVENTF_SCANCODE | (KEYEVENTF_KEYUP if up else 0)
    if vk in EXTENDED:
        flags |= KEYEVENTF_EXTENDEDKEY
    inp.ki = KEYBDINPUT(0, scan, flags, 0, 0)
    return inp


def _unicode_input(code_unit: int, up: bool) -> INPUT:
    inp = INPUT(type=INPUT_KEYBOARD)
    inp.ki = KEYBDINPUT(0, code_unit, KEYEVENTF_UNICODE | (KEYEVENTF_KEYUP if up else 0), 0, 0)
    return inp


def _send(inputs: list[INPUT]) -> None:
    if not inputs:
        return
    arr = (INPUT * len(inputs))(*inputs)
    sent = _user32.SendInput(len(inputs), arr, ctypes.sizeof(INPUT))
    if sent != len(inputs):
        err = ctypes.get_last_error()
        raise InputBlockedError(
            "Windows blocked keyboard input. If the target app runs as administrator, "
            f"Windows does not allow it (code {err})."
        )


def key_down(key: str) -> None:
    _send([_key_input(vk_for(key), up=False)])


def key_up(key: str) -> None:
    _send([_key_input(vk_for(key), up=True)])


def press(keys: Union[str, Iterable[str]]) -> None:
    """press("ctrl+shift+tab") or press(["ctrl", "t"]): modifiers down, key tap, release."""
    parts = keys.split("+") if isinstance(keys, str) else list(keys)
    parts = [p.strip().lower() for p in parts if p.strip()]
    if not parts:
        raise ValueError("No keys given")
    *mods, last = parts
    inputs = [_key_input(vk_for(m), False) for m in mods]
    inputs.append(_key_input(vk_for(last), False))
    inputs.append(_key_input(vk_for(last), True))
    inputs += [_key_input(vk_for(m), True) for m in reversed(mods)]
    _send(inputs)


def type_text(text: str, delay: float = 0.0) -> None:
    """Type Unicode text. Never uses the clipboard. Text is never logged."""
    for ch in text:
        units = ch.encode("utf-16-le")
        inputs = []
        for i in range(0, len(units), 2):
            inputs.append(_unicode_input(int.from_bytes(units[i:i + 2], "little"), False))
        for i in range(0, len(units), 2):
            inputs.append(_unicode_input(int.from_bytes(units[i:i + 2], "little"), True))
        _send(inputs)
        if delay:
            time.sleep(delay)


def send_wheel(notches: int) -> None:
    """Mouse wheel at the current cursor position (does not move the cursor)."""
    inp = INPUT(type=INPUT_MOUSE)
    inp.mi = MOUSEINPUT(0, 0, ctypes.c_ulong(notches * WHEEL_DELTA & 0xFFFFFFFF).value,
                        MOUSEEVENTF_WHEEL, 0, 0)
    _send([inp])
