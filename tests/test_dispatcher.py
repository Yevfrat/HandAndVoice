import json
import queue
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from core.command import Command, CommandType, Result
from core.confirmation import ConfirmationManager
from core.dispatcher import Dispatcher
from core.feedback import Feedback
from core.mode_manager import Mode, ModeManager
from services.config_store import ConfigStore
from voice.command_parser import CommandParser, FuzzyResult
from voice.engine import VoiceEngine
from voice.grammar import PhraseSources
from voice.hotkeys import HotkeyListener, key_id, parse_hotkey

COMMANDS = json.loads((Path(__file__).parent.parent / "config" / "voice_commands.json").read_text())


class Clock:
    def __init__(self): self.t = 0.0
    def __call__(self): return self.t


class Resolver:
    def __init__(self, fuzzy=None): self.fuzzy = fuzzy or FuzzyResult("none")
    def find_exact(self, text): return SimpleNamespace(name="Claude") if text == "claude" else None
    def find_fuzzy(self, text): return self.fuzzy


def build(fuzzy=None):
    clock = Clock()
    fb = Feedback()
    events = []
    fb.add_listener(events.append)
    modes = ModeManager("HYBRID")
    conf = ConfirmationManager(5.0, clock)
    launched, ran = [], []
    executors = {
        CommandType.LAUNCH_APP: lambda c: (launched.append(c.target), Result.success(f"Opening {c.target}"))[1],
        CommandType.CLOSE_ALL_APPS: lambda c: (ran.append("closeall"), Result.success("closed"))[1],
    }
    d = Dispatcher(CommandParser(COMMANDS, Resolver(fuzzy), "hand"), modes, conf, fb, executors,
                   {"CLOSE_ALL_APPS": "Close all windows"}, clock)
    return d, fb, events, launched, ran, clock, modes


def kinds(events):
    return [e.kind for e in events]


def test_launch_runs_executor():
    d, fb, events, launched, *_ = build()
    d.handle_text("hand claude")
    assert launched == ["Claude"]
    assert "success" in kinds(events)
    assert fb.actions[0].ok and fb.actions[0].result == "Opening Claude"


def test_unrecognized_command_shows_error_and_runs_nothing():
    d, fb, events, launched, *_ = build()
    d.handle_text("banana")
    assert launched == [] and "error" in kinds(events)
    assert events[-1].title == "Command not recognized: banana"


def test_alternative_reading_is_used_only_when_the_grammar_reading_fails():
    d, fb, events, launched, *_ = build()
    d.handle_text("", alt="claude")                     # grammar heard nothing, full vocabulary heard "claude"
    assert launched == ["Claude"]
    d.handle_text("claude", alt="clod")                 # grammar reading is valid: it wins
    assert launched == ["Claude", "Claude"]


def test_unrecognized_error_shows_the_full_vocabulary_words():
    d, fb, events, launched, *_ = build()
    d.handle_text("water", alt="wooden spoon")
    assert events[-1].title == "Command not recognized: wooden spoon" and launched == []


def test_noise_in_both_readings_is_ignored_silently():
    d, fb, events, *_ = build()
    d.handle_text("", alt="")
    d.handle_text("[unk]", alt="")
    assert not [e for e in events if e.kind in ("error", "success")]


def test_unimplemented_command_reports_not_available():
    d, fb, events, *_ = build()
    d.handle_text("minimize")
    assert events[-1].kind == "error" and "not available" in events[-1].title


def test_destructive_command_waits_for_confirmation():
    d, fb, events, launched, ran, clock, _ = build()
    d.handle_text("close all apps")
    assert ran == [] and "prompt" in kinds(events)
    d.handle_text("confirm")
    assert ran == ["closeall"]


def test_cancel_and_timeout_run_nothing():
    d, fb, events, launched, ran, clock, _ = build()
    d.handle_text("close all apps")
    d.handle_text("cancel")
    assert ran == []
    d.handle_text("confirm")                     # nothing pending any more
    assert ran == [] and events[-1].kind == "error"

    d.handle_text("close all apps")
    clock.t += 6
    d.tick()
    assert ran == [] and events[-1].title == "Confirmation timed out"
    d.handle_text("confirm")
    assert ran == []


def test_confirm_without_pending_is_rejected():
    d, fb, events, launched, ran, *_ = build()
    d.handle_text("confirm")
    assert ran == [] and events[-1].kind == "error"


def test_picker_flow_by_number():
    apps = [SimpleNamespace(name="Code"), SimpleNamespace(name="Code Editor")]
    d, fb, events, launched, *_ = build(FuzzyResult("ambiguous", apps))
    d.handle_text("hand code")
    assert launched == [] and events[-1].kind == "picker" and events[-1].options == ["Code", "Code Editor"]
    d.handle_text("hand two")
    assert launched == ["Code Editor"]


def test_picker_by_click_and_expiry():
    apps = [SimpleNamespace(name="A one"), SimpleNamespace(name="B")]
    d, fb, events, launched, ran, clock, _ = build(FuzzyResult("ambiguous", apps))
    d.handle_text("something")
    d.pick(1)
    assert launched == ["A one"]
    d.handle_text("something")
    clock.t += 11
    d.handle_text("two")                         # picker expired -> number is not a command
    assert launched == ["A one"]


def test_voice_commands_rejected_when_voice_is_off():
    d, fb, events, launched, ran, clock, modes = build()
    modes.set_mode(Mode.GESTURE_ONLY)
    d.handle_text("claude")
    assert launched == [] and events[-1].kind == "error"


def test_mode_manager_pause_resume():
    m = ModeManager("GESTURE_ONLY")
    seen = []
    m.add_listener(seen.append)
    m.pause(); m.resume()
    assert seen == [Mode.PAUSED, Mode.GESTURE_ONLY]
    assert not m.voice_enabled and m.camera_enabled
    assert ModeManager("PAUSED").mode == Mode.PAUSED


# ---------------- hotkeys ----------------
def test_parse_hotkey():
    assert parse_hotkey("ctrl+alt+r") == (frozenset({"ctrl", "alt"}), "r")
    with pytest.raises(ValueError):
        parse_hotkey("hyper+r")


def test_key_id_prefers_virtual_key_over_control_char():
    assert key_id(SimpleNamespace(vk=0x52, char="\x12")) == "r"
    assert key_id(SimpleNamespace(name="ctrl_r")) == "ctrl_r"


def test_hotkey_listener_logic():
    log = []
    h = HotkeyListener("ctrl_r", "ctrl+alt+r", lambda: log.append("down"), lambda: log.append("up"),
                       lambda: log.append("resume"), lambda: True)
    K = lambda **kw: SimpleNamespace(**kw)
    h._press(K(name="ctrl_r")); h._press(K(name="ctrl_r")); h._release(K(name="ctrl_r"))
    assert log == ["down", "up"]                 # auto-repeat does not re-trigger
    log.clear()
    h._press(K(name="ctrl_l")); h._press(K(name="alt_l")); h._press(K(vk=0x52, char="\x12"))
    assert log == ["resume"]
    log.clear()
    h2 = HotkeyListener("ctrl_r", "ctrl+alt+r", lambda: log.append("down"), lambda: log.append("up"),
                        lambda: None, lambda: False)  # PTT disabled
    h2._press(K(name="ctrl_r"))
    assert log == []


# ---------------- engine with fake mic + fake recognizers ----------------
class FakeMic:
    def __init__(self):
        self.q = queue.Queue(); self.running = False; self.starts = 0; self.stops = 0
    def start(self): self.running = True; self.starts += 1
    def stop(self): self.running = False; self.stops += 1
    def read(self, timeout=0.25):
        try: return self.q.get(timeout=timeout)
        except queue.Empty: return None
    def read_nowait(self):
        try: return self.q.get_nowait()
        except queue.Empty: return None


class EngineRec:
    def __init__(self, grammar):
        self.grammar = json.loads(grammar)
    def feed(self, data):
        if self.grammar == ["hand", "[unk]"]:
            return None
        return "minimize" if data == b"CMD" else None
    def partial(self): return "hand" if self._last == b"HAND" else ""
    _last = b""
    def finalize(self): return ""
    def reset(self): pass


class WakeRec(EngineRec):
    def feed(self, data): self._last = data; return None


def wait_for(cond, timeout=3.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond(): return True
        time.sleep(0.02)
    return False


def test_engine_end_to_end_with_fakes(tmp_path):
    store = ConfigStore(data_dir=tmp_path / "d"); store.ensure_initialized()
    events = queue.Queue(); mic = FakeMic()
    factory = lambda g: WakeRec(g) if json.loads(g) == ["hand", "[unk]"] else EngineRec(g)
    sources = lambda: PhraseSources(list(COMMANDS), ["Claude"], ["Alim"])
    eng = VoiceEngine(store, sources, events, recognizer_factory=factory, mic_factory=lambda dev: mic)
    eng.start()
    try:
        def drain():
            out = []
            while True:
                try: out.append(events.get_nowait())
                except queue.Empty: return out
        assert wait_for(lambda: not mic.running)            # disabled: mic closed
        eng.set_enabled(True)
        assert wait_for(lambda: mic.running)
        mic.q.put(b"CMD")                                    # no wake word: nothing
        mic.q.put(b"SIL"); mic.q.put(b"SIL"); mic.q.put(b"SIL")
        time.sleep(0.3)
        assert not any(e.kind == "command_text" for e in drain())
        mic.q.put(b"HAND")                                   # wake word
        mic.q.put(b"CMD")                                    # command inside the window
        got = []
        assert wait_for(lambda: got.extend(drain()) or any(e.kind == "command_text" for e in got))
        assert [e.text for e in got if e.kind == "command_text"] == ["minimize"]
        assert any(e.kind == "mic_state" and e.text == "window" for e in got)
        eng.set_enabled(False)                               # Pause: stream must be fully closed
        assert wait_for(lambda: not mic.running)
        assert mic.stops >= 1
    finally:
        eng.shutdown()


def test_enabling_a_running_microphone_does_not_reopen_it(tmp_path):
    store = ConfigStore(data_dir=tmp_path / "d"); store.ensure_initialized()
    events = queue.Queue(); mic = FakeMic()
    factory = lambda g: WakeRec(g) if json.loads(g) == ["hand", "[unk]"] else EngineRec(g)
    eng = VoiceEngine(store, lambda: PhraseSources(list(COMMANDS), ["Claude"], ["Alim"]), events,
                      recognizer_factory=factory, mic_factory=lambda dev: mic)
    eng.start(); eng.set_enabled(True)
    try:
        assert wait_for(lambda: mic.running)
        starts = mic.starts
        for _ in range(5):                                   # e.g. Gesture Only -> Hybrid -> Voice Only
            eng.set_enabled(True)
        time.sleep(0.5)
        assert mic.starts == starts and mic.running
        eng.set_enabled(False)
        assert wait_for(lambda: not mic.running)
        eng.set_enabled(True)
        assert wait_for(lambda: mic.running and mic.starts == starts + 1)
    finally:
        eng.shutdown()


def test_engine_reports_missing_model_without_crashing(tmp_path):
    store = ConfigStore(data_dir=tmp_path / "d"); store.ensure_initialized()
    events = queue.Queue()
    def bad_factory(g): raise RuntimeError("Speech model is missing")
    eng = VoiceEngine(store, lambda: PhraseSources(list(COMMANDS)), events,
                      recognizer_factory=bad_factory, mic_factory=lambda d: FakeMic())
    eng.start(); eng.set_enabled(True)
    try:
        assert wait_for(lambda: any(getattr(e, "kind", "") == "error" for e in list(events.queue)))
    finally:
        eng.shutdown()
