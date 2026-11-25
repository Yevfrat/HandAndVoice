import ctypes

import pytest

from automation import keyboard_controller as kc
from automation.web_controller import (
    WebController, build_search_url, find_alias, is_browser_process, validate_url,
)

ALIASES = {"Alim": "https://alimlab.uz", "YouTube": "https://www.youtube.com",
           "Bad": "javascript:alert(1)", "Plain": "http://example.com"}


class FakeKeyboard:
    def __init__(self): self.log = []
    def press(self, keys): self.log.append(("press", keys))
    def type_text(self, text): self.log.append(("type", text))


def controller(foreground, kb=None, opened=None, sink=None):
    kb = kb or FakeKeyboard()
    opened = opened if opened is not None else []
    return WebController(keyboard=kb, foreground_process=lambda: foreground, opener=opened.append,
                         sleep=lambda s: None, background=False, error_sink=sink), kb, opened


# ---------------- URL validation ----------------
@pytest.mark.parametrize("url", ["https://alimlab.uz", "https://www.google.com/search?q=a+b",
                                 "HTTPS://Example.com/x"])
def test_valid_urls(url):
    assert validate_url(url) == url


@pytest.mark.parametrize("url", [
    "http://example.com", "file:///C:/Windows/System32/calc.exe", "javascript:alert(1)",
    "data:text/html,<script>1</script>", "ftp://x.org", "https://", "https:///nohost",
    "alimlab.uz", "", "https://exa mple.com", "https://x.org/\nwhoami", "calc.exe",
])
def test_invalid_urls_rejected(url):
    with pytest.raises(ValueError):
        validate_url(url)


# ---------------- aliases ----------------
def test_alias_lookup_case_insensitive():
    assert find_alias(ALIASES, "alim") == "https://alimlab.uz"
    assert find_alias(ALIASES, "YOUTUBE") == "https://www.youtube.com"
    assert find_alias(ALIASES, "unknown") is None


def test_alias_not_found_never_guesses():
    wc, kb, opened = controller("explorer.exe")
    res = wc.open_alias("alimlab", ALIASES)
    assert not res.ok and res.message == "Website alias not found"
    assert opened == [] and kb.log == []


def test_alias_with_bad_scheme_is_refused():
    wc, kb, opened = controller("explorer.exe")
    for alias in ("bad", "plain"):
        res = wc.open_alias(alias, ALIASES)
        assert not res.ok
    assert opened == [] and kb.log == []


# ---------------- search ----------------
def test_search_url_is_encoded():
    assert build_search_url("weather in London") == "https://www.google.com/search?q=weather+in+London"
    assert "%26" in build_search_url("a & b") and "%23" in build_search_url("c# tips")
    assert build_search_url("  spaced   out ") == "https://www.google.com/search?q=spaced+out"
    with pytest.raises(ValueError):
        build_search_url("   ")


def test_search_query_cannot_inject_a_scheme():
    url = build_search_url("javascript:alert(1) https://evil.com")
    assert url.startswith("https://www.google.com/search?q=")
    assert "evil" in url and " " not in url


# ---------------- opening ----------------
def test_default_browser_when_no_browser_in_front():
    wc, kb, opened = controller("explorer.exe")
    res = wc.open_alias("Alim", ALIASES)
    assert res.ok and opened == ["https://alimlab.uz"] and kb.log == []


@pytest.mark.parametrize("proc", ["chrome.exe", "MSEDGE.EXE", "firefox.exe", "brave.exe", "opera.exe"])
def test_new_tab_when_browser_in_front(proc):
    wc, kb, opened = controller(proc)
    res = wc.open_alias("alim", ALIASES)
    assert res.ok and "new tab" in res.message and opened == []
    assert kb.log == [("press", "ctrl+t"), ("press", "ctrl+l"), ("type", "https://alimlab.uz"),
                      ("press", "enter")]


def test_search_in_browser_types_the_search_url():
    wc, kb, _ = controller("chrome.exe")
    wc.search("hello world")
    assert ("type", "https://www.google.com/search?q=hello+world") in kb.log


def test_browser_detection():
    assert is_browser_process("Chrome.exe") and not is_browser_process("notepad.exe")
    assert not is_browser_process(None)


def test_keyboard_failure_is_reported_not_raised():
    class Broken(FakeKeyboard):
        def press(self, keys): raise kc.InputBlockedError("blocked by admin app")
    errors = []
    wc, _, _ = controller("chrome.exe", kb=Broken(), sink=errors.append)
    assert wc.open_alias("alim", ALIASES).ok
    assert errors == ["blocked by admin app"]


# ---------------- SendInput plumbing ----------------
def test_input_struct_matches_windows_layout():
    assert ctypes.sizeof(kc.INPUT) == (40 if ctypes.sizeof(ctypes.c_void_p) == 8 else 28)


def test_key_table():
    assert kc.vk_for("ctrl") == 0xA2 and kc.vk_for("t") == ord("T") and kc.vk_for("F11") == 0x7A
    with pytest.raises(ValueError):
        kc.vk_for("nonsense")
    assert kc._key_input(kc.vk_for("right"), up=False).ki.dwFlags & kc.KEYEVENTF_EXTENDEDKEY
    assert kc._key_input(kc.vk_for("t"), up=True).ki.dwFlags & kc.KEYEVENTF_KEYUP
