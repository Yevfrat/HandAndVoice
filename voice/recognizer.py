"""Vosk wrapper. The model is loaded lazily on first use and never at import time."""
from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

SAMPLE_RATE = 16000
SMALL_MODEL = "vosk-model-small-en-us-0.15"           # 40 MB, default
BETTER_MODEL = "vosk-model-en-us-0.22-lgraph"          # 128 MB, more accurate (optional)
MODEL_NAME = SMALL_MODEL                                # kept for compatibility

# "SMALL" (default) or "BETTER" (settings.json: speech_model). Takes effect after a restart:
# the model is loaded once. If the chosen one is not installed the other one is used.
_preferred = "SMALL"


class ModelMissingError(RuntimeError):
    pass


def set_preferred(choice: str) -> None:
    global _preferred
    _preferred = "BETTER" if str(choice).upper() == "BETTER" else "SMALL"


def models_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "models"


def _installed(name: str) -> bool:
    p = models_dir() / name
    return p.is_dir() and (p / "am").exists()


def model_path() -> Path:
    order = (BETTER_MODEL, SMALL_MODEL) if _preferred == "BETTER" else (SMALL_MODEL, BETTER_MODEL)
    for name in order:
        if _installed(name):
            return models_dir() / name
    return models_dir() / SMALL_MODEL


def model_installed() -> bool:
    return _installed(SMALL_MODEL) or _installed(BETTER_MODEL)


def better_installed() -> bool:
    return _installed(BETTER_MODEL)


_model = None
_model_lock = threading.Lock()


def get_model():
    """Load the Vosk model once (thread-safe). Raises ModelMissingError if absent."""
    global _model
    with _model_lock:
        if _model is None:
            if not model_installed():
                raise ModelMissingError(
                    "Speech model is missing. Run: python scripts/download_models.py"
                )
            from vosk import Model, SetLogLevel  # imported lazily: heavy

            SetLogLevel(-1)
            log.info("Loading Vosk model %s", model_path().name)
            _model = Model(str(model_path()))
            log.info("Vosk model loaded")
        return _model


class ModelVocabulary:
    """`word in vocab` asks the Vosk model itself whether it knows the word.

    (The small model ships no readable word list, so this is the only reliable way.)
    """

    def __init__(self, model):
        self._model = model
        self._cache: dict[str, bool] = {}

    def __contains__(self, word: object) -> bool:
        w = str(word).lower()
        known = self._cache.get(w)
        if known is None:
            known = self._model.vosk_model_find_word(w) >= 0
            self._cache[w] = known
        return known


_vocab: Optional[ModelVocabulary] = None


def vocabulary_if_loaded() -> Optional[ModelVocabulary]:
    """The vocabulary, but only if the model is already in memory (never loads it: UI-safe)."""
    return load_vocabulary() if _model is not None else None


def load_vocabulary() -> Optional[ModelVocabulary]:
    """Vocabulary of the loaded model, or None when it cannot be queried."""
    global _vocab
    if _vocab is None:
        try:
            model = get_model()
            if not hasattr(model, "vosk_model_find_word"):
                return None
            _vocab = ModelVocabulary(model)
        except Exception:
            log.exception("Could not read the model vocabulary")
            return None
    return _vocab


class SpeechRecognizer:
    """Thin wrapper around KaldiRecognizer with a grammar (JSON list of phrases)."""

    def __init__(self, grammar_json: Optional[str] = None, model=None, sample_rate: int = SAMPLE_RATE):
        from vosk import KaldiRecognizer

        model = model or get_model()
        if grammar_json:
            self._rec = KaldiRecognizer(model, sample_rate, grammar_json)
        else:
            self._rec = KaldiRecognizer(model, sample_rate)

    def feed(self, data: bytes) -> Optional[str]:
        """Feed audio. Returns the final text of a finished utterance, else None."""
        if self._rec.AcceptWaveform(data):
            return json.loads(self._rec.Result()).get("text", "")
        return None

    def partial(self) -> str:
        return json.loads(self._rec.PartialResult()).get("partial", "")

    def finalize(self) -> str:
        text = json.loads(self._rec.FinalResult()).get("text", "")
        return text

    def reset(self) -> None:
        self._rec.Reset()
