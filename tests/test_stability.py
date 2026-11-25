import os
import queue
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from core.app_controller import AppController
from services.config_store import ConfigStore
from voice import recognizer
from voice.engine import VoiceEngine
from voice.grammar import PhraseSources
from voice.listen_modes import VoiceEvent
from voice.microphone import Microphone, MicrophoneError


@pytest.fixture
def app(tmp_path):
    store = ConfigStore(data_dir=tmp_path / "d"); store.ensure_initialized()
    c = AppController(store)
    errors = []
    c.feedback.add_listener(lambda e: errors.append(e.title) if e.kind == "error" else None)
    return c, store, errors


# ---------------------------------------------------------------- error handling
def test_the_same_device_problem_is_reported_once_not_on_every_retry(app):
    c, store, errors = app
    for _ in range(4):
        c._handle_event(VoiceEvent("error", "Camera 0 could not be opened."))
    assert errors == ["Camera 0 could not be opened."]
    c._handle_event(VoiceEvent("error", "Microphone not available."))      # a different problem is shown
    assert len(errors) == 2


def test_an_explicit_retry_may_report_the_problem_again(app):
    c, store, errors = app
    c._handle_event(VoiceEvent("error", "Speech model is missing."))
    c.retry_devices()
    c._handle_event(VoiceEvent("error", "Speech model is missing."))
    assert errors.count("Speech model is missing.") == 2


def test_retry_rebuilds_both_engines(app):
    c, store, errors = app
    v, g = c.engine._version, c.gestures._version
    c.retry_devices()
    assert c.engine._version == v + 1 and c.gestures._version == g + 1


def test_a_missing_microphone_gives_a_clear_message_and_never_raises_anything_else():
    with pytest.raises(MicrophoneError) as exc:
        Microphone(device=999).start()                    # no such audio device on any PC
    assert "#999" in str(exc.value) and "Settings > Microphone" in str(exc.value)


def test_a_missing_speech_model_is_a_friendly_error(tmp_path, monkeypatch):
    monkeypatch.setattr(recognizer, "models_dir", lambda: tmp_path)
    monkeypatch.setattr(recognizer, "_model", None)
    assert not recognizer.model_installed()
    with pytest.raises(recognizer.ModelMissingError, match="download_models.py"):
        recognizer.get_model()


def test_the_voice_engine_reports_an_unknown_wake_word(tmp_path, monkeypatch):
    class Vocab:
        def __contains__(self, w): return w != "zzzqq"
    monkeypatch.setattr(recognizer, "get_model", lambda: object())
    monkeypatch.setattr(recognizer, "load_vocabulary", lambda: Vocab())
    store = ConfigStore(data_dir=tmp_path / "d"); store.ensure_initialized()
    store.set("wake_word", "zzzqq")
    eng = VoiceEngine(store, lambda: PhraseSources(["minimize"]), queue.Queue())
    with pytest.raises(RuntimeError, match="not a word the speech model knows"):
        eng._build_mode()


def test_settings_refuse_a_wake_word_the_model_does_not_know(app, monkeypatch):
    c, store, errors = app
    class Vocab:
        def __contains__(self, w): return w == "computer"
    monkeypatch.setattr(recognizer, "vocabulary_if_loaded", lambda: Vocab())
    assert "not a word the speech model knows" in c.set_wake_word("zzzqq")
    assert store.get("wake_word") == "hand"
    assert c.set_wake_word("computer") == "" and store.get("wake_word") == "computer"


def test_setting_the_wake_word_never_loads_the_model_on_the_ui_thread(app, monkeypatch):
    c, store, errors = app
    monkeypatch.setattr(recognizer, "_model", None)
    called = []
    monkeypatch.setattr(recognizer, "get_model", lambda: called.append(1))
    assert c.set_wake_word("computer") == "" and called == []


# ---------------------------------------------------------------- UI never blocks
@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def test_audio_devices_are_listed_when_the_settings_page_opens_not_at_startup(app, qapp, monkeypatch):
    from ui.settings_page import SettingsPage
    c, store, errors = app
    calls = []
    monkeypatch.setattr(c, "list_microphones", lambda: (calls.append(1), [(3, "USB mic")])[1])
    page = SettingsPage(c)
    assert calls == []                                    # building the window did not query the sound card
    page.show()
    assert calls == [1] and page.mic.findData(3) >= 0
    page.hide(); page.show()
    assert calls == [1]                                   # only once
    page.close()


def test_the_saved_microphone_is_selectable_before_the_list_is_read(app, qapp):
    from ui.settings_page import SettingsPage
    c, store, errors = app
    store.set("microphone_device", 7)
    page = SettingsPage(c)
    assert page.mic.currentData() == 7


def test_hidden_window_skips_gesture_widget_updates(app, qapp):
    from gestures.types import GestureStatus
    from ui.main_window import MainWindow
    c, store, errors = app
    w = MainWindow(store, c)
    c.camera_on = True
    calls = []
    w.gesture_page.set_status = lambda st, camera: calls.append(1)
    w.on_gesture("status", GestureStatus("PEACE", 0.9, 12.0, 0.1, True, False))
    assert calls == []                                    # window not visible (tray): no work
    w.show()
    w.on_gesture("status", GestureStatus("PEACE", 0.9, 12.0, 0.1, True, False))
    assert calls == [1]
    w.close()


# ---------------------------------------------------------------- clean shutdown
def test_shutdown_stops_everything_quickly_even_when_nothing_was_started(app):
    c, store, errors = app
    t = time.perf_counter()
    c.shutdown()
    assert time.perf_counter() - t < 1.0


def test_shutdown_asks_both_worker_threads_to_stop_before_waiting(app, monkeypatch):
    c, store, errors = app
    order = []
    monkeypatch.setattr(c.gestures, "request_stop", lambda: order.append("g-request"))
    monkeypatch.setattr(c.engine, "request_stop", lambda: order.append("v-request"))
    monkeypatch.setattr(c.gestures, "shutdown", lambda: order.append("g-join"))
    monkeypatch.setattr(c.engine, "shutdown", lambda: order.append("v-join"))
    c.shutdown()
    assert order == ["g-request", "v-request", "g-join", "v-join"]     # parallel stop, then joins
