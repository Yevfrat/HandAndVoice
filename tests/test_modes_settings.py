import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from core.app_controller import AppController
from core.command import CommandType
from core.mode_manager import Mode
from services.config_store import ConfigStore
from voice.hotkeys import key_id


@pytest.fixture
def app(tmp_path):
    store = ConfigStore(data_dir=tmp_path / "d"); store.ensure_initialized()
    c = AppController(store)
    events = []
    c.feedback.add_listener(events.append)
    c.apply = lambda: (c.engine.set_enabled(c.mode_manager.voice_enabled), c._apply_gesture_state())
    c.apply()
    return SimpleNamespace(c=c, store=store, events=events)


def flags(c):
    return c.engine._enabled.is_set(), c.gestures._enabled.is_set()


K = lambda **kw: SimpleNamespace(**kw)


# ---------------------------------------------------------------- the four modes
@pytest.mark.parametrize("mode,mic,camera", [
    (Mode.HYBRID, True, True), (Mode.VOICE_ONLY, True, False),
    (Mode.GESTURE_ONLY, False, True), (Mode.PAUSED, False, False),
])
def test_each_mode_turns_the_microphone_and_camera_on_or_off(app, mode, mic, camera):
    c = app.c
    c.mode_manager.set_mode(Mode.PAUSED if mode != Mode.PAUSED else Mode.HYBRID)   # make sure it changes
    c.mode_manager.set_mode(mode)
    assert flags(c) == (mic, camera)
    assert c.mode_manager.voice_enabled == mic and c.mode_manager.camera_enabled == camera


def test_gestures_enabled_off_keeps_the_camera_off_in_hybrid(app):
    c = app.c
    assert flags(c) == (True, True)
    c.set_gestures_enabled(False)
    assert flags(c) == (True, False)
    c.set_gestures_enabled(True)
    assert flags(c) == (True, True)


def test_start_mode_paused_starts_with_everything_off(tmp_path):
    store = ConfigStore(data_dir=tmp_path / "d"); store.ensure_initialized()
    store.set("start_mode", "PAUSED")
    c = AppController(store)
    c.engine.set_enabled(c.mode_manager.voice_enabled); c._apply_gesture_state()
    assert c.mode_manager.mode == Mode.PAUSED and flags(c) == (False, False)
    c.mode_manager.resume()                                     # no earlier mode: back to Hybrid
    assert c.mode_manager.mode == Mode.HYBRID and flags(c) == (True, True)


def test_resume_goes_back_to_the_mode_before_the_pause(app):
    c = app.c
    c.mode_manager.set_mode(Mode.GESTURE_ONLY)
    c.mode_manager.pause()
    assert flags(c) == (False, False)
    c.mode_manager.resume()
    assert c.mode_manager.mode == Mode.GESTURE_ONLY and flags(c) == (False, True)


def test_mode_change_tells_the_user_what_is_on(app):
    app.c.mode_manager.set_mode(Mode.GESTURE_ONLY)
    ev = [e for e in app.events if e.kind == "info"][-1]
    assert ev.title == "Mode: Gesture Only" and "Microphone OFF" in ev.body and "camera on" in ev.body
    app.c.mode_manager.pause()
    ev = [e for e in app.events if e.kind == "info"][-1]
    assert "Microphone OFF" in ev.body and "camera OFF" in ev.body


def test_voice_commands_switch_modes(app):
    c = app.c
    c.dispatcher.handle_text("hand voice only")
    assert c.mode_manager.mode == Mode.VOICE_ONLY and flags(c) == (True, False)
    c.dispatcher.handle_text("hand hybrid")
    assert flags(c) == (True, True)
    c.dispatcher.handle_text("hand pause")
    assert c.mode_manager.mode == Mode.PAUSED and flags(c) == (False, False)


def test_gesture_only_by_voice_closes_the_microphone(app):
    app.c.dispatcher.handle_text("hand gesture only")
    assert app.c.mode_manager.mode == Mode.GESTURE_ONLY and flags(app.c) == (False, True)


# ---------------------------------------------------------------- Ctrl+Alt+R and hotkeys
def test_ctrl_alt_r_resumes_from_pause(app):
    c = app.c
    c.mode_manager.pause()
    h = c.hotkeys
    h._press(K(name="ctrl_l")); h._press(K(name="alt_l")); h._press(K(vk=0x52, char="\x12"))
    c.poll()
    assert c.mode_manager.mode == Mode.HYBRID and flags(c) == (True, True)


def test_ctrl_alt_r_does_nothing_when_not_paused(app):
    c = app.c
    c.mode_manager.set_mode(Mode.VOICE_ONLY)
    h = c.hotkeys
    h._press(K(name="ctrl_l")); h._press(K(name="alt_l")); h._press(K(vk=0x52, char="\x12"))
    c.poll()
    assert c.mode_manager.mode == Mode.VOICE_ONLY


def test_press_a_key_captures_the_push_to_talk_key(app):
    c, store = app.c, app.store
    got = []
    c.add_state_listener(lambda k, v: got.append((k, v)))
    c.capture_ptt_key()
    c.hotkeys._press(K(name="f9"))                       # the captured key is not a hotkey press
    c.poll()
    assert store.get("push_to_talk_key") == "f9" and ("ptt_key", "f9") in got
    assert c.hotkeys._ptt_key == "f9"
    c.hotkeys._press(K(name="f9"))                       # now it is the push-to-talk key
    assert c.hotkeys._ptt_held is False                  # (listen mode is not PUSH_TO_TALK here)
    c.set_listen_mode("PUSH_TO_TALK")
    c.hotkeys._release(K(name="f9"))
    c.hotkeys._press(K(name="f9"))
    assert c.hotkeys._ptt_held is True


def test_capture_can_be_cancelled(app):
    c = app.c
    c.capture_ptt_key(); c.cancel_capture()
    c.hotkeys._press(K(name="f9")); c.poll()
    assert app.store.get("push_to_talk_key") == "ctrl_r"


# ---------------------------------------------------------------- settings
def test_voice_settings_reload_the_voice_engine(app):
    c = app.c
    v = c.engine._version
    c.apply_setting("command_window_seconds", 8)
    c.apply_setting("microphone_device", 1)
    assert app.store.get("command_window_seconds") == 8 and app.store.get("microphone_device") == 1
    assert c.engine._version == v + 2


def test_camera_settings_reload_the_gesture_engine_and_are_clamped(app):
    c = app.c
    v = c.gestures._version
    c.apply_setting("camera_fps", 99)
    c.apply_setting("camera_index", 1)
    assert app.store.get("camera_fps") == 30 and app.store.get("camera_index") == 1
    assert c.gestures._version == v + 2
    c.apply_setting("camera_fps", 1)
    assert app.store.get("camera_fps") == 5


def test_wake_word_can_be_changed_but_never_to_a_command_word(app):
    c = app.c
    for bad in ("minimize", "file", "two words", "", "one"):
        assert c.set_wake_word(bad) != "", bad
    assert app.store.get("wake_word") == "hand"
    v = c.engine._version
    assert c.set_wake_word(" Computer ") == ""
    assert app.store.get("wake_word") == "computer" and c.parser.wake_word == "computer"
    assert c.engine._version == v + 1
    c.dispatcher.handle_text("computer hybrid")           # the new wake word is stripped by the parser
    assert c.mode_manager.mode == Mode.HYBRID


def test_default_hold_times_are_written_to_every_gesture(app):
    c = app.c
    app.store.update({"default_hold_seconds": 1.5, "sleep_hold_seconds": 3.0, "scroll_hold_seconds": 0.2})
    c.apply_default_holds()
    g = c.load_gestures_config()
    assert g["FIST"]["hold_seconds"] == 3.0 and g["POINT_UP"]["hold_seconds"] == 0.2
    assert g["POINT_DOWN"]["hold_seconds"] == 0.2 and g["OPEN_PALM"]["hold_seconds"] == 1.5
    assert all("enabled" in cfg and "action" in cfg for cfg in g.values())     # nothing else was lost


def test_microphone_list_never_raises(app, monkeypatch):
    import voice.microphone as mic
    monkeypatch.setattr(mic, "list_input_devices", lambda: (_ for _ in ()).throw(RuntimeError("no audio")))
    assert app.c.list_microphones() == []


def test_the_settings_in_the_spec_exist_with_valid_defaults(app):
    s = app.store.settings
    for key in ("start_mode", "listen_mode", "wake_word", "command_window_seconds", "push_to_talk_key",
                "microphone_device", "camera_index", "camera_fps", "default_hold_seconds",
                "show_indicators"):
        assert key in s
    assert s["start_mode"] == "HYBRID" and s["listen_mode"] == "WAKE_WORD" and s["show_indicators"] is True


# ---------------------------------------------------------------- UI
@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def test_tray_menu_follows_the_mode(qapp):
    from ui.tray import Tray
    t = Tray()
    t.set_mode("GESTURE_ONLY")
    checked = [k for k, a in t._mode_actions.items() if a.isChecked()]
    assert checked == ["GESTURE_ONLY"] and t.act_pause.text() == "Pause"
    assert "Gesture Only" in t.icon.toolTip()
    t.set_mode("PAUSED")
    assert t.act_pause.text() == "Resume" and not any(a.isChecked() for a in t._mode_actions.values())
    got = []
    t.mode_requested.connect(got.append)
    t._mode_actions["HYBRID"].trigger()
    assert got == ["HYBRID"]


def test_dashboard_mode_buttons(qapp):
    from ui.dashboard import DashboardPage
    d = DashboardPage()
    got, paused = [], []
    d.mode_requested.connect(got.append); d.pause_toggle_requested.connect(lambda: paused.append(1))
    d._mode_buttons["VOICE_ONLY"].click(); d.pause_button.click()
    assert got == ["VOICE_ONLY"] and paused == [1]
    d.set_mode("PAUSED")
    assert d.pause_button.text() == "Resume" and not any(b.isChecked() for b in d._mode_buttons.values())
    d.set_mode("HYBRID")
    assert d._mode_buttons["HYBRID"].isChecked() and d.pause_button.text() == "Pause"


def test_settings_page_saves_and_applies(app, qapp, monkeypatch):
    from ui.settings_page import SettingsPage
    c = app.c
    page = SettingsPage(c)
    assert page.start_mode.currentData() == "HYBRID" and page.indicators.isChecked()
    assert page.mic.itemText(0) == "System default" and page.mic.itemData(0) is None

    page.start_mode.setCurrentIndex(page.start_mode.findData("VOICE_ONLY")); page.start_mode.activated.emit(page.start_mode.currentIndex())
    assert app.store.get("start_mode") == "VOICE_ONLY"

    page.indicators.setChecked(False)
    assert app.store.get("show_indicators") is False

    page.window_seconds.setValue(9); page.window_seconds.editingFinished.emit()
    assert app.store.get("command_window_seconds") == 9
    page.fps.setValue(20); page.fps.editingFinished.emit()
    assert app.store.get("camera_fps") == 20
    page.default_hold.setValue(1.4); page.default_hold.editingFinished.emit()
    assert app.store.get("default_hold_seconds") == 1.4

    page.wake.setText("minimize"); page._set_wake()
    assert "command" in page.wake_status.text().lower() and page.wake.text() == "hand"
    page.wake.setText("jarvis"); page._set_wake()
    assert app.store.get("wake_word") == "jarvis"

    page.listen.setCurrentIndex(page.listen.findData("PUSH_TO_TALK")); page.listen.activated.emit(page.listen.currentIndex())
    assert app.store.get("listen_mode") == "PUSH_TO_TALK"

    c.add_state_listener(lambda k, v: page.set_ptt_key(v) if k == "ptt_key" else None)   # as main window does
    page._capture_ptt()
    assert page.ptt_label.text().startswith("Press") and not page.ptt_button.isEnabled()
    c.hotkeys._press(K(name="f8")); c.poll()
    assert app.store.get("push_to_talk_key") == "f8"
    assert page.ptt_label.text() == "F8" and page.ptt_button.isEnabled()

    from voice import recognizer
    monkeypatch.setattr(recognizer, "better_installed", lambda: False)
    page.speech_model.setCurrentIndex(1); page.speech_model.activated.emit(1)
    assert app.store.get("speech_model") == "BETTER"
    assert "not installed" in page.model_label.text()            # tells the user to download it
    monkeypatch.setattr(recognizer, "better_installed", lambda: True)
    page.speech_model.setCurrentIndex(0); page.speech_model.activated.emit(0)
    assert app.store.get("speech_model") == "SMALL" and "start the app again" in page.model_label.text()


def test_settings_page_shows_model_status_and_download_buttons(app, qapp):
    from ui.settings_page import SettingsPage
    page = SettingsPage(app.c)
    text = page.model_label.text()
    assert "speech model" in text and "hand model" in text and "better model" in text
    assert page.dl_missing.isEnabled() and page.dl_better.isEnabled()
