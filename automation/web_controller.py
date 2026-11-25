"""Open websites and searches. https only; aliases only (never guesses a URL)."""
from __future__ import annotations

import logging
import os
import threading
import time
from typing import Callable, Optional
from urllib.parse import quote_plus, urlsplit

from core.command import Result
from voice.text_normalizer import normalize

log = logging.getLogger(__name__)

BROWSER_PROCESSES = {"chrome.exe", "msedge.exe", "firefox.exe", "brave.exe", "opera.exe"}
SEARCH_URL = "https://www.google.com/search?q="
MAX_QUERY_CHARS = 200


def validate_url(url: str) -> str:
    """Return the URL if it is a plain https address, else raise ValueError."""
    if not isinstance(url, str) or any(c.isspace() or ord(c) < 32 for c in url.strip()):
        raise ValueError("Invalid web address")
    url = url.strip()
    parts = urlsplit(url)
    if parts.scheme.lower() != "https":
        raise ValueError("Only https:// addresses are allowed")
    if not parts.hostname:
        raise ValueError("Web address has no host name")
    return url


def find_alias(aliases: dict[str, str], spoken: str) -> Optional[str]:
    """URL for a spoken alias (case-insensitive, normalized), or None."""
    key = normalize(spoken)
    for alias, url in aliases.items():
        if normalize(alias) == key:
            return url
    return None


def build_search_url(query: str) -> str:
    query = " ".join(query.split())[:MAX_QUERY_CHARS]
    if not query:
        raise ValueError("Say what to search for")
    return SEARCH_URL + quote_plus(query)


def is_browser_process(name: Optional[str]) -> bool:
    return bool(name) and name.lower() in BROWSER_PROCESSES


class WebController:
    def __init__(self, keyboard=None, foreground_process: Optional[Callable[[], Optional[str]]] = None,
                 opener: Optional[Callable[[str], None]] = None,
                 sleep: Callable[[float], None] = time.sleep, background: bool = True,
                 error_sink: Optional[Callable[[str], None]] = None):
        self._keyboard = keyboard
        self._foreground_process = foreground_process
        self._opener = opener or os.startfile
        self._sleep = sleep
        self._background = background
        self._error_sink = error_sink
        self._lock = threading.Lock()

    # ----- lazily bound Windows pieces (keeps this module importable in tests) -----
    @property
    def keyboard(self):
        if self._keyboard is None:
            from automation import keyboard_controller

            self._keyboard = keyboard_controller
        return self._keyboard

    def foreground_process(self) -> Optional[str]:
        if self._foreground_process is not None:
            return self._foreground_process()
        from automation import window_manager

        return window_manager.get_process_name(window_manager.get_foreground_hwnd())

    def browser_in_front(self) -> bool:
        return is_browser_process(self.foreground_process())

    # ----- public actions -----
    def open_alias(self, spoken: str, aliases: dict[str, str]) -> Result:
        url = find_alias(aliases, spoken)
        if url is None:
            return Result.error("Website alias not found")
        try:
            url = validate_url(url)
        except ValueError as exc:
            return Result.error(f"Website '{spoken}' is not allowed: {exc}")
        return self.open_url(url, f"Opening {spoken}")

    def search(self, query: str) -> Result:
        try:
            url = build_search_url(query)
        except ValueError as exc:
            return Result.error(str(exc))
        return self.open_url(url, "Searching the web")

    def open_url(self, url: str, message: str = "Opening website") -> Result:
        try:
            url = validate_url(url)
        except ValueError as exc:
            return Result.error(str(exc))
        if self.browser_in_front():
            if self._background:
                threading.Thread(target=self._new_tab, args=(url,), name="web-new-tab", daemon=True).start()
            else:
                self._new_tab(url)
            return Result.success(message + " in a new tab")
        try:
            self._opener(url)
        except OSError as exc:
            return Result.error(f"Could not open the browser: {exc}")
        return Result.success(message)

    def _new_tab(self, url: str) -> None:
        """Ctrl+T, Ctrl+L, type the address, Enter. Runs off the UI thread."""
        kb = self.keyboard
        try:
            with self._lock:
                kb.press("ctrl+t")
                self._sleep(0.35)
                kb.press("ctrl+l")
                self._sleep(0.1)
                kb.type_text(url)
                self._sleep(0.05)
                kb.press("enter")
        except Exception as exc:
            log.error("Opening a new tab failed: %s", exc)
            if self._error_sink:
                self._error_sink(str(exc))
