import json

import pytest

from services.config_store import CONFIG_FILES, ConfigStore


@pytest.fixture
def store(tmp_path):
    s = ConfigStore(data_dir=tmp_path / "data")
    s.ensure_initialized()
    return s


def test_first_run_copies_all_defaults(store):
    for name in CONFIG_FILES:
        assert (store.data_dir / name).is_file()


def test_defaults_match_spec(store):
    s = store.settings
    assert s["start_mode"] == "HYBRID"
    assert s["listen_mode"] == "WAKE_WORD"
    assert s["wake_word"] == "hand"
    assert s["command_window_seconds"] == 5
    assert s["push_to_talk_key"] == "ctrl_r"
    assert s["camera_fps"] == 12


def test_existing_user_files_not_overwritten(tmp_path):
    d = tmp_path / "data"
    d.mkdir()
    (d / "settings.json").write_text(json.dumps({"camera_fps": 20}))
    s = ConfigStore(data_dir=d)
    s.ensure_initialized()
    assert s.get("camera_fps") == 20
    assert s.get("wake_word") == "hand"  # missing keys filled from defaults


def test_set_persists_to_disk(store):
    store.set("listen_mode", "PUSH_TO_TALK")
    reloaded = ConfigStore(data_dir=store.data_dir)
    reloaded.ensure_initialized()
    assert reloaded.get("listen_mode") == "PUSH_TO_TALK"


def test_numeric_values_are_clamped(store):
    store.set("camera_fps", 999)
    assert store.get("camera_fps") == 30
    store.set("camera_fps", 1)
    assert store.get("camera_fps") == 5


def test_invalid_enum_reverts_to_default(store):
    store.set("listen_mode", "BANANA")
    assert store.get("listen_mode") == "WAKE_WORD"


def test_wrong_type_reverts_to_default(store):
    store.set("show_indicators", "yes")
    assert store.get("show_indicators") is True


def test_wake_word_lowercased(store):
    store.set("wake_word", "  HAND ")
    assert store.get("wake_word") == "hand"


def test_unknown_key_rejected(store):
    with pytest.raises(KeyError):
        store.set("nope", 1)


def test_corrupt_settings_file_falls_back(tmp_path):
    d = tmp_path / "data"
    d.mkdir()
    (d / "settings.json").write_text("{ not json")
    s = ConfigStore(data_dir=d)
    s.ensure_initialized()
    assert s.get("listen_mode") == "WAKE_WORD"


def test_load_and_save_other_json(store):
    aliases = store.load_json("web_aliases.json")
    assert aliases["Alim"] == "https://alimlab.uz"
    aliases["Test"] = "https://example.com"
    store.save_json("web_aliases.json", aliases)
    assert store.load_json("web_aliases.json")["Test"] == "https://example.com"


def test_shipped_configs_are_consistent(store):
    gestures = store.load_json("gestures.json")
    assert len(gestures) == 14
    assert gestures["FIST"]["hold_seconds"] == 2.0
    assert gestures["POINT_UP"]["hold_seconds"] == 0.3
    cmds = store.load_json("voice_commands.json")
    for phrase in ("minimize", "close all apps", "go to top", "sleep", "confirm"):
        assert phrase in cmds
    assert cmds["close all apps"]["requires_confirmation"] is True
    # The wake word must never be a command phrase.
    assert store.get("wake_word") not in cmds
    for url in store.load_json("web_aliases.json").values():
        assert url.startswith("https://")
