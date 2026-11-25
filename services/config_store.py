"""Loads and saves the JSON configuration files.

User data lives in %APPDATA%\\HandAndVoice. On first run the defaults shipped in
the project's ``config/`` folder are copied there. The app only reads and writes
the user folder afterwards.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import tempfile
import threading
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

APP_FOLDER_NAME = "HandAndVoice"
DEFAULTS_DIR = Path(__file__).resolve().parent.parent / "config"
CONFIG_FILES = (
    "settings.json",
    "voice_commands.json",
    "gestures.json",
    "app_aliases.json",
    "web_aliases.json",
    "voice_variants.json",
)

# Allowed values for enumerated settings.
ENUMS: dict[str, tuple[str, ...]] = {
    "start_mode": ("VOICE_ONLY", "GESTURE_ONLY", "HYBRID", "PAUSED"),
    "listen_mode": ("WAKE_WORD", "ALWAYS", "PUSH_TO_TALK"),
    "hand": ("Right", "Left", "Any"),
    "log_level": ("DEBUG", "INFO", "WARNING", "ERROR"),
    "speech_model": ("SMALL", "BETTER"),
    "face_wink_style": ("TABS", "PAGES", "HISTORY"),
}

# (min, max) limits for numeric settings.
RANGES: dict[str, tuple[float, float]] = {
    "command_window_seconds": (1, 30),
    "face_hold_seconds": (0.2, 3.0),
    "camera_index": (0, 10),
    "camera_fps": (5, 30),
    "camera_width": (160, 1920),
    "camera_height": (120, 1080),
    "min_hand_confidence": (0.1, 1.0),
    "stable_frames": (1, 30),
    "default_hold_seconds": (0.1, 10),
    "sleep_hold_seconds": (0.1, 10),
    "scroll_hold_seconds": (0.05, 5),
    "cooldown_seconds": (0.0, 10),
    "movement_tolerance": (0.01, 1.0),
    "scroll_notch_interval_ms": (20, 1000),
    "volume_tick_interval_ms": (50, 2000),
    "switch_window_timeout_seconds": (1, 60),
    "browser_mode_timeout_seconds": (1, 60),
    "confirmation_timeout_seconds": (1, 60),
    "sleep_countdown_seconds": (1, 30),
    "fuzzy_threshold": (50, 100),
    "picker_gap": (0, 50),
    "file_open_threshold": (50, 100),
    "file_open_gap": (0, 50),
    "index_refresh_minutes": (1, 1440),
}


def get_data_dir() -> Path:
    """Return the user data folder (override with HANDANDVOICE_DATA_DIR for tests)."""
    override = os.environ.get("HANDANDVOICE_DATA_DIR")
    if override:
        return Path(override)
    appdata = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    return Path(appdata) / APP_FOLDER_NAME


def _atomic_write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, ensure_ascii=False)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


class ConfigStore:
    def __init__(self, data_dir: Path | None = None, defaults_dir: Path | None = None):
        self.data_dir = Path(data_dir) if data_dir else get_data_dir()
        self.defaults_dir = Path(defaults_dir) if defaults_dir else DEFAULTS_DIR
        self._lock = threading.RLock()
        self._settings: dict[str, Any] = {}

    # ----- setup -----
    def ensure_initialized(self) -> None:
        """Create the data folder and copy any missing default files into it."""
        self.data_dir.mkdir(parents=True, exist_ok=True)
        for name in CONFIG_FILES:
            target = self.data_dir / name
            if not target.exists():
                shutil.copyfile(self.defaults_dir / name, target)
                log.info("Created default config file %s", name)
        self._settings = self._load_settings()

    # ----- generic JSON -----
    def load_json(self, name: str) -> Any:
        """Load a config file; fall back to the shipped default if unreadable."""
        path = self.data_dir / name
        try:
            with open(path, "r", encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, json.JSONDecodeError) as exc:
            log.warning("Could not read %s (%s); using defaults", name, exc)
            with open(self.defaults_dir / name, "r", encoding="utf-8") as fh:
                return json.load(fh)

    def save_json(self, name: str, data: Any) -> None:
        with self._lock:
            _atomic_write_json(self.data_dir / name, data)

    # ----- settings -----
    def default_settings(self) -> dict[str, Any]:
        with open(self.defaults_dir / "settings.json", "r", encoding="utf-8") as fh:
            return json.load(fh)

    def _load_settings(self) -> dict[str, Any]:
        defaults = self.default_settings()
        loaded = self.load_json("settings.json")
        if not isinstance(loaded, dict):
            loaded = {}
        merged = dict(defaults)
        for key, value in loaded.items():
            if key in defaults:  # ignore unknown keys
                merged[key] = value
        return self.validate(merged, defaults)

    @staticmethod
    def validate(settings: dict[str, Any], defaults: dict[str, Any]) -> dict[str, Any]:
        """Coerce values to the right type/range; invalid values revert to defaults."""
        out = dict(settings)
        for key, default in defaults.items():
            value = out.get(key, default)
            try:
                if key in ENUMS:
                    value = str(value).upper() if key != "hand" else str(value).capitalize()
                    if value not in ENUMS[key]:
                        raise ValueError
                elif isinstance(default, bool):
                    if not isinstance(value, bool):
                        raise ValueError
                elif isinstance(default, (int, float)) and not isinstance(default, bool):
                    if isinstance(value, bool) or not isinstance(value, (int, float)):
                        raise ValueError
                    if key in RANGES:
                        lo, hi = RANGES[key]
                        value = max(lo, min(hi, value))
                    if isinstance(default, int):
                        value = int(round(value))
                elif isinstance(default, list):
                    if not isinstance(value, list):
                        raise ValueError
                elif isinstance(default, str):
                    if not isinstance(value, str) or not value.strip():
                        raise ValueError
                    value = value.strip()
                elif default is None:
                    if value is not None and not isinstance(value, (int, str)):
                        raise ValueError
            except ValueError:
                log.warning("Invalid value for setting %s; using default", key)
                value = default
            out[key] = value
        out["wake_word"] = out["wake_word"].lower()
        return out

    @property
    def settings(self) -> dict[str, Any]:
        with self._lock:
            if not self._settings:
                self._settings = self._load_settings()
            return dict(self._settings)

    def get(self, key: str, default: Any = None) -> Any:
        return self.settings.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self.update({key: value})

    def update(self, changes: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            defaults = self.default_settings()
            unknown = [k for k in changes if k not in defaults]
            if unknown:
                raise KeyError(f"Unknown setting(s): {', '.join(unknown)}")
            merged = dict(self.settings)
            merged.update(changes)
            self._settings = self.validate(merged, defaults)
            _atomic_write_json(self.data_dir / "settings.json", self._settings)
            return dict(self._settings)

    def reset_settings(self) -> None:
        with self._lock:
            self._settings = self.default_settings()
            _atomic_write_json(self.data_dir / "settings.json", self._settings)
