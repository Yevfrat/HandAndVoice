import pytest

from services.config_store import ConfigStore
from voice import recognizer


@pytest.fixture
def models(tmp_path, monkeypatch):
    for name in (recognizer.SMALL_MODEL, recognizer.BETTER_MODEL):
        (tmp_path / name / "am").mkdir(parents=True)
    monkeypatch.setattr(recognizer, "models_dir", lambda: tmp_path)
    yield tmp_path
    recognizer.set_preferred("SMALL")


def test_small_is_the_default_even_when_the_better_model_is_installed(models):
    assert recognizer.model_path().name == recognizer.SMALL_MODEL


def test_better_is_used_only_when_chosen(models):
    recognizer.set_preferred("better")
    assert recognizer.model_path().name == recognizer.BETTER_MODEL
    recognizer.set_preferred("whatever")
    assert recognizer.model_path().name == recognizer.SMALL_MODEL


def test_falls_back_to_the_other_model_when_the_chosen_one_is_missing(tmp_path, monkeypatch):
    (tmp_path / recognizer.SMALL_MODEL / "am").mkdir(parents=True)
    monkeypatch.setattr(recognizer, "models_dir", lambda: tmp_path)
    recognizer.set_preferred("BETTER")
    assert recognizer.model_path().name == recognizer.SMALL_MODEL and recognizer.model_installed()
    assert not recognizer.better_installed()
    recognizer.set_preferred("SMALL")


def test_setting_is_validated(tmp_path):
    s = ConfigStore(data_dir=tmp_path / "d"); s.ensure_initialized()
    assert s.get("speech_model") == "SMALL"
    s.set("speech_model", "better")
    assert s.get("speech_model") == "BETTER"
    s.set("speech_model", "huge")
    assert s.get("speech_model") == "SMALL"
