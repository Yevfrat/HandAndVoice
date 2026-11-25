from __future__ import annotations

import os
import sys
from pathlib import Path

from PySide6.QtCore import QProcess, Qt
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout, QFrame, QHBoxLayout, QLabel, QLineEdit,
    QMessageBox, QPushButton, QScrollArea, QSpinBox, QWidget,
)

from ui.page_base import Page
from voice import recognizer

DOWNLOAD_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "download_models.py"

LISTEN_OPTIONS = [
    ("Wake word (recommended): say 'Hand', then the command", "WAKE_WORD"),
    ("Always: every command starts with 'Hand'", "ALWAYS"),
    ("Push to talk: hold the key below while speaking", "PUSH_TO_TALK"),
]
START_MODES = [("Hybrid (voice + camera)", "HYBRID"), ("Voice Only", "VOICE_ONLY"),
               ("Gesture Only", "GESTURE_ONLY"), ("Paused", "PAUSED")]


def _dim(text: str) -> QLabel:
    lab = QLabel(text)
    lab.setObjectName("dim")
    lab.setWordWrap(True)
    return lab


class SettingsPage(Page):
    def __init__(self, controller):
        super().__init__("Settings", "Changes are saved and applied immediately, unless a note says to restart.")
        self.controller = controller
        self.store = controller.store
        s = self.store.settings

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        body = QWidget()
        body.setObjectName("page")
        self.form = QFormLayout(body)
        self.form.setContentsMargins(0, 0, 12, 0)
        self.form.setVerticalSpacing(10)
        # long labels wrap above their field instead of forcing a horizontal scrollbar
        self.form.setRowWrapPolicy(QFormLayout.WrapLongRows)
        self.form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        scroll.setWidget(body)
        self.root.addWidget(scroll, 1)

        # ---------------- general ----------------
        self._section("General")
        self.start_mode = QComboBox()
        for text, value in START_MODES:
            self.start_mode.addItem(text, value)
        self.start_mode.setCurrentIndex(self.start_mode.findData(s["start_mode"]))
        self.start_mode.activated.connect(
            lambda i: self.controller.apply_setting("start_mode", self.start_mode.itemData(i)))
        self.form.addRow("Start mode (next launch)", self.start_mode)

        self.indicators = QCheckBox("Show a message in the overlay whenever the microphone or camera turns on/off")
        self.indicators.setChecked(bool(s["show_indicators"]))
        self.indicators.toggled.connect(lambda on: self.controller.apply_setting("show_indicators", bool(on)))
        self.form.addRow("Privacy indicators", self.indicators)
        self.form.addRow("", _dim("The header always shows the microphone and camera state."))

        # ---------------- voice ----------------
        self._section("Voice")
        self.listen = QComboBox()
        for text, value in LISTEN_OPTIONS:
            self.listen.addItem(text, value)
        self.sync_listen_mode(s["listen_mode"])
        self.listen.activated.connect(lambda i: self.controller.set_listen_mode(self.listen.itemData(i)))
        self.form.addRow("Listen mode", self.listen)

        wake_row = QHBoxLayout()
        self.wake = QLineEdit(s["wake_word"])
        self.wake.setMaximumWidth(180)
        set_wake = QPushButton("Set")
        set_wake.clicked.connect(self._set_wake)
        wake_row.addWidget(self.wake)
        wake_row.addWidget(set_wake)
        wake_row.addStretch(1)
        self.form.addRow("Wake word", wake_row)
        self.wake_status = _dim("One plain English word the speech model knows, never a command word.")
        self.form.addRow("", self.wake_status)

        self.window_seconds = QSpinBox()
        self.window_seconds.setRange(1, 30)
        self.window_seconds.setValue(int(s["command_window_seconds"]))
        self.window_seconds.editingFinished.connect(
            lambda: self.controller.apply_setting("command_window_seconds", self.window_seconds.value()))
        self.form.addRow("Command window (seconds)", self.window_seconds)

        ptt_row = QHBoxLayout()
        self.ptt_label = QLabel(self._pretty_key(s["push_to_talk_key"]))
        self.ptt_button = QPushButton("Press a key...")
        self.ptt_button.clicked.connect(self._capture_ptt)
        ptt_row.addWidget(self.ptt_label)
        ptt_row.addWidget(self.ptt_button)
        ptt_row.addStretch(1)
        self.form.addRow("Push-to-talk key", ptt_row)
        self.form.addRow("", _dim("Right Alt is not recommended: AltGr conflicts with some keyboard layouts."))

        self.mic = QComboBox()
        self.mic.addItem("System default", None)               # the real list is read when the page is shown
        saved = s["microphone_device"]
        if saved is not None:
            self.mic.addItem(f"Saved microphone (#{saved})", saved)
            self.mic.setCurrentIndex(1)
        self._mics_loaded = False
        self.mic.activated.connect(lambda i: self.controller.apply_setting("microphone_device", self.mic.itemData(i)))
        refresh_mic = QPushButton("Refresh list")
        refresh_mic.clicked.connect(self.refresh_microphones)
        mic_row = QHBoxLayout()
        mic_row.addWidget(self.mic, 1)
        mic_row.addWidget(refresh_mic)
        self.form.addRow("Microphone", mic_row)

        # ---------------- camera and gestures ----------------
        self._section("Camera and gestures")
        self.camera_index = QComboBox()
        for i in range(5):
            self.camera_index.addItem(f"Camera {i}", i)
        self.camera_index.setCurrentIndex(max(0, self.camera_index.findData(int(s["camera_index"]))))
        self.camera_index.activated.connect(
            lambda i: self.controller.apply_setting("camera_index", self.camera_index.itemData(i)))
        self.form.addRow("Camera", self.camera_index)

        self.fps = QSpinBox()
        self.fps.setRange(5, 30)
        self.fps.setValue(int(s["camera_fps"]))
        self.fps.editingFinished.connect(lambda: self.controller.apply_setting("camera_fps", self.fps.value()))
        self.form.addRow("Camera FPS (5-30)", self.fps)

        self.default_hold = self._hold_spin(s["default_hold_seconds"], "default_hold_seconds")
        self.sleep_hold = self._hold_spin(s["sleep_hold_seconds"], "sleep_hold_seconds")
        self.scroll_hold = self._hold_spin(s["scroll_hold_seconds"], "scroll_hold_seconds", 0.05)
        self.form.addRow("Default hold (seconds)", self.default_hold)
        self.form.addRow("Fist / sleep hold (seconds)", self.sleep_hold)
        self.form.addRow("Scroll hold (seconds)", self.scroll_hold)
        apply_holds = QPushButton("Apply these hold times to all gestures")
        apply_holds.clicked.connect(self._apply_holds)
        self.form.addRow("", apply_holds)
        self.form.addRow("", _dim("Per-gesture hold times, the hand to use and movement tolerance are on the "
                                  "Hand Gestures page."))

        # ---------------- speech model ----------------
        self._section("Speech model")
        self.speech_model = QComboBox()
        self.speech_model.addItem("Small (40 MB, default, faster)", "SMALL")
        self.speech_model.addItem("Better (128 MB, needs download; try it and compare)", "BETTER")
        self.speech_model.setCurrentIndex(max(0, self.speech_model.findData(s["speech_model"])))
        self.speech_model.activated.connect(self._model_changed)
        self.form.addRow("Model (restart after changing)", self.speech_model)

        self.model_label = _dim("")
        self.form.addRow("Status", self.model_label)
        dl_row = QHBoxLayout()
        self.dl_missing = QPushButton("Download missing models")
        self.dl_missing.clicked.connect(lambda: self._download("", "the speech model (about 40 MB) and the "
                                                                 "hand model (about 8 MB)"))
        self.dl_better = QPushButton("Download better speech model")
        self.dl_better.clicked.connect(lambda: self._download("--better", "the better speech model (about 128 MB)"))
        dl_row.addWidget(self.dl_missing)
        dl_row.addWidget(self.dl_better)
        dl_row.addStretch(1)
        self.form.addRow("", dl_row)
        self.dl_status = _dim("")
        self.form.addRow("", self.dl_status)
        self.refresh_model_status()
        self._proc: QProcess | None = None

        # ---------------- data ----------------
        self._section("Data")
        self.form.addRow("Data folder", _dim(str(self.store.data_dir)))
        btn = QPushButton("Open data folder")
        btn.clicked.connect(lambda: os.startfile(str(self.store.data_dir)))
        self.form.addRow("", btn)

        # long item texts must not widen the page: show them elided inside a narrow box
        for combo in self.findChildren(QComboBox):
            combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
            combo.setMinimumContentsLength(14)
        for kind in (QSpinBox, QDoubleSpinBox):
            for spin in self.findChildren(kind):
                spin.setMaximumWidth(160)                     # numbers do not need the whole row
        self.indicators.setText("Show a message in the overlay when the microphone or camera turns on/off")
        self.indicators.setStyleSheet("QCheckBox { spacing: 8px; }")

    # ---------------- helpers ----------------
    def _section(self, title: str) -> None:
        lab = QLabel(title)
        lab.setStyleSheet("font-size: 12pt; font-weight: 600; padding-top: 10px;")
        self.form.addRow(lab)

    def _hold_spin(self, value: float, key: str, low: float = 0.1) -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setRange(low, 10.0)
        spin.setSingleStep(0.1)
        spin.setValue(float(value))
        spin.editingFinished.connect(lambda: self.controller.apply_setting(key, spin.value()))
        return spin

    @staticmethod
    def _pretty_key(name: str) -> str:
        return name.replace("_", " ").title() if name else "(none)"

    # ---------------- listen mode / wake word / ptt ----------------
    def sync_listen_mode(self, value: str) -> None:
        idx = self.listen.findData(value)
        if idx >= 0:
            self.listen.setCurrentIndex(idx)

    def _set_wake(self) -> None:
        error = self.controller.set_wake_word(self.wake.text())
        if error:
            self.wake_status.setText(error)
            self.wake.setText(self.store.get("wake_word"))
        else:
            self.wake_status.setText(f"Wake word is now '{self.store.get('wake_word')}'.")

    def _capture_ptt(self) -> None:
        self.ptt_label.setText("Press the key now...")
        self.ptt_button.setEnabled(False)
        self.controller.capture_ptt_key()

    def set_ptt_key(self, name: str) -> None:
        self.ptt_label.setText(self._pretty_key(name))
        self.ptt_button.setEnabled(True)

    def showEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        """Listing audio devices takes ~0.2 s: do it when the page is first opened, not at startup."""
        super().showEvent(event)
        if not self._mics_loaded:
            self.refresh_microphones()

    def refresh_microphones(self) -> None:
        self._mics_loaded = True
        current = self.store.get("microphone_device")
        self.mic.clear()
        self.mic.addItem("System default", None)
        for index, name in self.controller.list_microphones():
            self.mic.addItem(f"{name} (#{index})", index)
        idx = self.mic.findData(current)
        self.mic.setCurrentIndex(idx if idx >= 0 else 0)

    # ---------------- gestures ----------------
    def _apply_holds(self) -> None:
        self.controller.apply_default_holds()
        QMessageBox.information(self, "Hold times", "The hold times were applied to all 14 gestures.")

    # ---------------- speech model ----------------
    def _model_changed(self, index: int) -> None:
        choice = self.speech_model.itemData(index)
        self.controller.apply_setting("speech_model", choice)
        if choice == "BETTER" and not recognizer.better_installed():
            self.model_label.setText("The better model is not installed yet: use the download button below.")
        else:
            self.model_label.setText(f"Saved: {choice.title()} model. Quit (tray) and start the app again.")

    def refresh_model_status(self) -> None:
        parts = []
        parts.append("speech model: " + (recognizer.model_path().name if recognizer.model_installed() else "MISSING"))
        parts.append("better model: " + ("installed" if recognizer.better_installed() else "not installed"))
        from gestures.hand_tracker import MODEL_FILE

        parts.append("hand model: " + ("installed" if MODEL_FILE.is_file() else "MISSING"))
        self.model_label.setText("   |   ".join(parts))

    def _download(self, flag: str, what: str) -> None:
        if self._proc is not None:
            return
        answer = QMessageBox.question(
            self, "Download models",
            f"This downloads {what} from the internet (alphacephei.com, storage.googleapis.com) "
            "once. Hand&Voice itself stays offline. Continue?")
        if answer != QMessageBox.Yes:
            return
        proc = QProcess(self)
        proc.setProgram(sys.executable)
        proc.setArguments([str(DOWNLOAD_SCRIPT)] + ([flag] if flag else []))
        proc.setProcessChannelMode(QProcess.MergedChannels)
        proc.readyReadStandardOutput.connect(self._download_output)
        proc.finished.connect(self._download_done)
        self._proc = proc
        for b in (self.dl_missing, self.dl_better):
            b.setEnabled(False)
        self.dl_status.setText("Downloading...")
        proc.start()

    def shutdown(self) -> None:
        """App is quitting: do not leave a download process behind."""
        proc, self._proc = self._proc, None
        if proc is not None:
            proc.kill()
            proc.waitForFinished(1000)

    def _download_output(self) -> None:
        if self._proc is None:
            return
        text = bytes(self._proc.readAllStandardOutput()).decode("utf-8", errors="replace")
        lines = [p.strip() for p in text.replace("\r", "\n").split("\n") if p.strip()]
        if lines:
            self.dl_status.setText(lines[-1])

    def _download_done(self, code: int, _status) -> None:
        self._proc = None
        for b in (self.dl_missing, self.dl_better):
            b.setEnabled(True)
        self.dl_status.setText("Download finished. Restart the app to use it." if code == 0
                               else "Download failed (see the last message above). Check your internet connection.")
        self.refresh_model_status()
