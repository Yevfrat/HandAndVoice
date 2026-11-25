import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from core.command import CommandType, Result
from core.confirmation import ConfirmationManager
from core.dispatcher import Dispatcher
from core.feedback import Feedback
from core.mode_manager import ModeManager
from gestures.gesture_controller import (
    BROWSER, NORMAL, STEP_HOLD, SWITCH, VOLUME, GestureController, GestureUI,
)
from gestures.types import GestureEvent

ROOT = Path(__file__).parent.parent / "config"
GESTURES = json.loads((ROOT / "gestures.json").read_text())
COMMANDS = json.loads((ROOT / "voice_commands.json").read_text())
CONFIRM = {CommandType(s["type"]) for s in COMMANDS.values() if s.get("requires_confirmation")}


class Clock:
    def __init__(self): self.t = 1000.0
    def __call__(self): return self.t


class FakeKB:
    def __init__(self): self.log = []
    def key_down(self, k): self.log.append(("down", k))
    def key_up(self, k): self.log.append(("up", k))
    def press(self, k): self.log.append(("press", k))
    def send_wheel(self, n): self.log.append(("wheel", n))


class FakeWM:
    def __init__(self, process="notepad.exe", elevated=False):
        self.info = SimpleNamespace(process=process, elevated=elevated, own=False, protected=False)
    def foreground_info(self): return self.info


class FakeMedia:
    def __init__(self): self.keys = []
    def press(self, k): self.keys.append(k)


class Rig:
    def __init__(self, process="notepad.exe", elevated=False):
        self.clock = Clock()
        self.kb, self.wm, self.media = FakeKB(), FakeWM(process, elevated), FakeMedia()
        self.feedback = Feedback()
        self.events = []
        self.feedback.add_listener(self.events.append)
        self.ran = []
        self.modes = ModeManager("HYBRID")
        self.conf = ConfirmationManager(5.0, self.clock)
        self.overrides = {}
        self.ui_calls = []
        executors = {}
        for t in (CommandType.CLOSE_ALL_APPS, CommandType.CLOSE_WINDOW, CommandType.PLAY_MEDIA,
                  CommandType.NEXT_TAB, CommandType.PREVIOUS_TAB, CommandType.NEXT_WINDOW,
                  CommandType.PREVIOUS_WINDOW):
            executors[t] = lambda c, t=t: (self.ran.append((t.name, c.source)), Result.success(t.name))[1]
        executors[CommandType.SLEEP] = self._sleep
        self.disp = Dispatcher(None, self.modes, self.conf, self.feedback, executors, {}, self.clock)
        self.g = GestureController(
            self.disp, None, self.media, self.feedback, lambda: GESTURES, lambda: {"cooldown_seconds": 1.0},
            self.overrides, CONFIRM, keyboard=self.kb, wm=self.wm,
            ui=GestureUI(toggle_overlay=lambda: self.ui_calls.append("toggle"),
                         close_overlay=lambda: self.ui_calls.append("close")), clock=self.clock)
        self.slept = []

    def _sleep(self, cmd):
        self.disp.start_countdown("Sleep in {s}s", 3, action=lambda: (self.slept.append(1), Result.success("sleep"))[1])
        return Result(True, "", show=False)

    def send(self, name, phase="start", y=0.5, size=0.2):
        self.g.handle(GestureEvent(name, phase, 0.5, y, size))

    def advance(self, secs):
        self.clock.t += secs
        self.g.tick()
        self.disp.tick()

    def errors(self):
        return [e.title for e in self.events if e.kind == "error"]


# ---------------------------------------------------------------- scroll
def test_scroll_up_and_down_use_the_mouse_wheel_at_80ms():
    r = Rig()
    r.send("POINT_UP", "start")
    assert r.kb.log == [("wheel", 1)]
    r.advance(0.03); r.send("POINT_UP", "tick")
    assert r.kb.log == [("wheel", 1)]                      # too early
    r.advance(0.06); r.send("POINT_UP", "tick")
    assert r.kb.log == [("wheel", 1), ("wheel", 1)]
    r.advance(0.1); r.send("POINT_DOWN", "start")
    assert r.kb.log[-1] == ("wheel", -1)


def test_scroll_stops_on_release_and_blocked_for_admin_apps():
    r = Rig()
    r.send("POINT_UP", "start"); r.send("POINT_UP", "end")
    n = len(r.kb.log)
    r.advance(1); r.send("POINT_UP", "end")
    assert len(r.kb.log) == n
    r = Rig(elevated=True)
    r.send("POINT_UP", "start"); r.advance(0.2); r.send("POINT_UP", "tick")
    assert r.kb.log == [] and len(r.errors()) == 1         # reported once, never retried


# ---------------------------------------------------------------- switch window
def test_switch_window_holds_alt_and_opens_the_switcher():
    r = Rig()
    r.send("INDEX_THUMB")
    assert r.kb.log == [("down", "alt"), ("press", "tab")]
    assert r.g.mode == SWITCH and r.g.s.alt_held
    assert r.overrides == {"POINT_RIGHT": STEP_HOLD, "POINT_LEFT": STEP_HOLD}


def test_switch_window_steps_with_a_04s_cooldown():
    r = Rig()
    r.send("INDEX_THUMB"); r.kb.log.clear()
    r.advance(0.5); r.send("POINT_RIGHT")
    r.advance(0.2); r.send("POINT_RIGHT", "tick")           # 0.2 s later: too early
    r.advance(0.3); r.send("POINT_RIGHT", "tick")           # 0.5 s after the first step
    r.advance(0.5); r.send("POINT_LEFT")
    assert r.kb.log == [("press", "right"), ("press", "right"), ("press", "left")]


def test_ok_selects_by_releasing_alt():
    r = Rig()
    r.send("INDEX_THUMB"); r.kb.log.clear()
    r.send("OK")
    assert r.kb.log == [("up", "alt")] and r.g.mode == NORMAL and not r.g.s.alt_held
    assert r.overrides == {}
    assert ("press", "enter") not in r.kb.log               # OK in this mode is not Enter


def test_escape_cancels_the_switcher_without_changing_focus():
    r = Rig()
    r.send("INDEX_THUMB"); r.kb.log.clear()
    r.send("ILY")
    assert r.kb.log == [("press", "escape"), ("up", "alt")]
    assert r.g.mode == NORMAL and not r.g.s.alt_held
    assert "close" in r.ui_calls


def test_switch_window_times_out_after_8_seconds_like_escape():
    r = Rig()
    r.send("INDEX_THUMB"); r.kb.log.clear()
    r.advance(7.9)
    assert r.g.mode == SWITCH
    r.advance(0.2)
    assert r.kb.log == [("press", "escape"), ("up", "alt")] and r.g.mode == NORMAL


def test_any_gesture_resets_the_switch_timeout():
    r = Rig()
    r.send("INDEX_THUMB")
    r.advance(6); r.send("POINT_RIGHT")
    r.advance(6)
    assert r.g.mode == SWITCH                                # 6 s since the last gesture
    r.advance(2.1)
    assert r.g.mode == NORMAL


def test_switch_window_is_refused_for_administrator_apps():
    r = Rig(elevated=True)
    r.send("INDEX_THUMB")
    assert r.kb.log == [] and r.g.mode == NORMAL and r.errors()


def test_voice_next_window_moves_the_selection_while_the_switcher_is_open():
    r = Rig()
    assert r.g.step_selection(+1) is False                   # not in switch mode: voice does Alt+Tab itself
    r.send("INDEX_THUMB"); r.kb.log.clear()
    assert r.g.step_selection(+1) and r.g.step_selection(-1)
    assert r.kb.log == [("press", "right"), ("press", "left")]


# ---------------------------------------------------------------- browser mode
def test_browser_mode_steps_tabs():
    r = Rig(process="chrome.exe")
    r.send("PEACE")
    assert r.g.mode == BROWSER and r.kb.log == []
    r.advance(0.5); r.send("POINT_RIGHT")
    r.advance(0.5); r.send("POINT_LEFT")
    assert r.kb.log == [("press", "ctrl+tab"), ("press", "ctrl+shift+tab")]
    r.advance(0.2); r.send("POINT_LEFT", "tick")
    assert len(r.kb.log) == 2                                # 0.4 s step cooldown


def test_browser_mode_needs_a_browser_and_times_out_after_10s():
    r = Rig(process="notepad.exe")
    r.send("PEACE")
    assert r.g.mode == NORMAL and r.errors() == ["Browser mode needs a browser in focus"]
    r = Rig(process="msedge.exe")
    r.send("PEACE")
    r.advance(9.9)
    assert r.g.mode == BROWSER
    r.advance(0.2)
    assert r.g.mode == NORMAL and r.overrides == {}


def test_escape_leaves_browser_mode():
    r = Rig(process="firefox.exe")
    r.send("PEACE"); r.send("ILY")
    assert r.g.mode == NORMAL and r.overrides == {}


# ---------------------------------------------------------------- volume mode
def test_volume_follows_the_hand_height():
    r = Rig()
    r.send("SHAKA", "start", y=0.6)
    assert r.g.mode == VOLUME
    r.advance(0.2); r.send("SHAKA", "tick", y=0.6)           # sets the anchor
    r.advance(0.2); r.send("SHAKA", "tick", y=0.55)          # 0.05 < 0.1 x 0.2 ? no: 0.05 > 0.02 -> up
    r.advance(0.2); r.send("SHAKA", "tick", y=0.60)          # moved down by 0.05 -> down
    assert r.media.keys == ["volume_up", "volume_down"]


def test_volume_ignores_small_movement_and_respects_the_rate():
    r = Rig()
    r.send("SHAKA", "start", y=0.6)
    r.advance(0.2); r.send("SHAKA", "tick", y=0.6)
    r.advance(0.2); r.send("SHAKA", "tick", y=0.59)          # 0.01 < 0.1 x 0.2
    assert r.media.keys == []
    r.advance(0.2); r.send("SHAKA", "tick", y=0.50)
    r.advance(0.05); r.send("SHAKA", "tick", y=0.30)         # 0.05 s later: rate-limited
    assert r.media.keys == ["volume_up"]


def test_volume_mode_ends_when_shaka_is_released_or_with_escape():
    r = Rig()
    r.send("SHAKA", "start"); r.send("SHAKA", "end")
    assert r.g.mode == NORMAL
    r.send("SHAKA", "start"); r.send("ILY")
    assert r.g.mode == NORMAL


def test_other_gestures_are_ignored_in_volume_mode():
    r = Rig()
    r.send("SHAKA", "start")
    r.send("THUMBS_UP"); r.send("FIST")
    assert r.ran == [] and r.g.mode == VOLUME


# ---------------------------------------------------------------- normal mode actions
def test_point_right_left_next_prev_window_or_tab():
    r = Rig(process="notepad.exe")
    r.send("POINT_RIGHT"); r.advance(1.5); r.send("POINT_LEFT")
    assert [n for n, _ in r.ran] == ["NEXT_WINDOW", "PREVIOUS_WINDOW"]
    r = Rig(process="chrome.exe")
    r.send("POINT_RIGHT"); r.advance(1.5); r.send("POINT_LEFT")
    assert [n for n, _ in r.ran] == ["NEXT_TAB", "PREVIOUS_TAB"]
    assert all(src == "gesture" for _, src in r.ran)


def test_single_action_per_hold_and_cooldown():
    r = Rig()
    r.send("POINT_RIGHT"); r.send("POINT_RIGHT", "tick"); r.send("POINT_RIGHT", "tick")
    assert len(r.ran) == 1                                   # ticks do nothing in normal mode
    r.advance(0.3); r.send("POINT_RIGHT")                    # started again within 1 s
    assert len(r.ran) == 1
    r.advance(1.0); r.send("POINT_RIGHT")
    assert len(r.ran) == 2


def test_rock_toggles_play_pause_and_open_palm_the_overlay():
    r = Rig()
    r.send("ROCK"); r.send("OPEN_PALM")
    assert r.ran == [("PLAY_MEDIA", "gesture")] and r.ui_calls == ["toggle"]


def test_ok_is_enter_in_normal_mode():
    r = Rig()
    r.send("OK")
    assert r.kb.log == [("press", "enter")]
    r = Rig(elevated=True)
    r.send("OK")
    assert r.kb.log == [] and r.errors()


# ---------------------------------------------------------------- confirmation and sleep
def test_thumbs_up_asks_before_closing_everything_and_ok_confirms():
    r = Rig()
    r.send("THUMBS_UP")
    assert r.ran == [] and r.conf.is_pending               # nothing closes without confirmation
    prompt = [e for e in r.events if e.kind == "prompt"][-1]
    assert "OK gesture" in prompt.body
    r.send("OK")
    assert r.ran == [("CLOSE_ALL_APPS", "gesture")] and not r.conf.is_pending
    assert r.kb.log == []                                    # OK confirmed, it did not press Enter


def test_thumbs_down_close_window_needs_confirmation_too():
    r = Rig()
    r.send("THUMBS_DOWN")
    assert r.ran == [] and r.conf.is_pending
    r.send("OK")
    assert r.ran == [("CLOSE_WINDOW", "gesture")]


def test_ily_cancels_a_pending_confirmation():
    r = Rig()
    r.send("THUMBS_UP"); r.send("ILY")
    assert r.ran == [] and not r.conf.is_pending
    r.send("OK")                                             # nothing pending now: plain Enter
    assert r.ran == [] and r.kb.log == [("press", "enter")]
    assert "close" in r.ui_calls


def test_confirmation_times_out_without_action():
    r = Rig()
    r.send("THUMBS_UP"); r.advance(6)
    r.send("OK")
    assert r.ran == []


def test_fist_starts_a_countdown_and_ily_cancels_it():
    r = Rig()
    r.send("FIST")
    assert r.slept == [] and r.disp.countdown_active        # a 3 s countdown, not an instant sleep
    r.advance(1); r.send("ILY")
    r.advance(10)
    assert r.slept == [] and not r.disp.countdown_active    # cancelled: the PC never sleeps


def test_fist_sleeps_after_the_countdown_if_not_cancelled():
    r = Rig()
    r.send("FIST"); r.advance(3.1)
    assert r.slept == [1]


def test_escape_with_nothing_to_cancel_is_harmless():
    r = Rig()
    r.send("ILY")
    assert r.kb.log == [] and r.ran == [] and r.ui_calls == ["close"]


# ---------------------------------------------------------------- safety
def test_release_all_never_leaves_alt_held():
    r = Rig()
    r.send("INDEX_THUMB"); r.kb.log.clear()
    r.g.release_all()
    assert r.kb.log == [("up", "alt")] and r.g.mode == NORMAL and r.overrides == {}
    r.g.release_all()                                        # safe to call again
    assert r.kb.log == [("up", "alt")]


def test_a_failing_key_press_releases_alt():
    class Blocked(FakeKB):
        def press(self, k):
            raise RuntimeError("blocked")
    r = Rig()
    r.g._keyboard = Blocked()
    r.send("INDEX_THUMB")
    assert r.g.mode == NORMAL and not r.g.s.alt_held
    assert ("up", "alt") in r.g._keyboard.log


def test_gesture_commands_are_rejected_when_the_camera_mode_is_off():
    r = Rig()
    r.modes.set_mode("VOICE_ONLY")
    r.send("THUMBS_UP")
    assert r.ran == [] and not r.conf.is_pending
    assert r.errors() == ["Gestures are off in this mode"]


def test_unknown_or_unmapped_gesture_does_nothing():
    r = Rig()
    r.send("NONE")
    assert r.kb.log == [] and r.ran == []
