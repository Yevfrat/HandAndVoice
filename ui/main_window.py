"""Main window: sidebar navigation, status header and the six pages."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QListWidget, QMainWindow, QStackedWidget,
    QVBoxLayout, QWidget,
)

from services.config_store import ConfigStore
from ui import theme
from ui.applications import ApplicationsPage
from ui.dashboard import DashboardPage
from ui.face_page import FacePage
from ui.files_websites import FilesWebsitesPage
from ui.hand_gestures import HandGesturesPage
from ui.settings_page import SettingsPage
from ui.tray import make_app_icon
from ui.voice_commands import VoiceCommandsPage

MODE_LABELS = {
    "VOICE_ONLY": "Voice Only",
    "GESTURE_ONLY": "Gesture Only",
    "HYBRID": "Hybrid",
    "PAUSED": "Paused",
    "FACE_ONLY": "Face Only",
}
LISTEN_LABELS = {"WAKE_WORD": "Wake word", "ALWAYS": "Always", "PUSH_TO_TALK": "Push to talk"}


class Header(QFrame):
    """Always-visible status strip."""

    def __init__(self):
        super().__init__()
        self.setObjectName("header")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 10, 20, 10)
        lay.setSpacing(4)

        row1 = QHBoxLayout()
        self.mic_dot = QLabel("●")
        self.mic_text = QLabel("")
        self.cam_text = QLabel("")
        self.mode_text = QLabel("")
        self.listen_text = QLabel("")
        row1.addWidget(self.mic_dot)
        row1.addWidget(self.mic_text)
        for w in (self.cam_text, self.mode_text, self.listen_text):
            row1.addSpacing(18)
            row1.addWidget(w)
        row1.addStretch(1)

        row2 = QHBoxLayout()
        self.last_command = QLabel("")
        self.last_action = QLabel("")
        self.last_error = QLabel("")
        self.last_error.setObjectName("error")
        for w in (self.last_command, self.last_action):
            w.setObjectName("dim")
            row2.addWidget(w)
            row2.addSpacing(18)
        row2.addWidget(self.last_error)
        row2.addStretch(1)

        lay.addLayout(row1)
        lay.addLayout(row2)
        self.set_mic("off")
        self.set_camera(False)
        self.set_mode("HYBRID")
        self.set_listen_mode("WAKE_WORD")
        self.set_last_command("-")
        self.set_last_action("-")
        self.set_last_error("")

    def set_mic(self, state: str) -> None:
        label, color = theme.MIC_STATES[state]
        self.mic_dot.setStyleSheet(f"color:{color}; font-size:14pt;")
        self.mic_text.setText(label)

    def set_camera(self, on: bool) -> None:
        self.cam_text.setText(f"Camera: {'ON' if on else 'OFF'}")

    def set_mode(self, mode: str) -> None:
        self.mode_text.setText(f"Mode: {MODE_LABELS.get(mode, mode)}")

    def set_listen_mode(self, mode: str) -> None:
        self.listen_text.setText(f"Listen: {LISTEN_LABELS.get(mode, mode)}")

    def set_last_command(self, text: str) -> None:
        self.last_command.setText(f"Command: {text}")

    def set_last_action(self, text: str) -> None:
        self.last_action.setText(f"Action: {text}")

    def set_last_error(self, text: str) -> None:
        self.last_error.setText(f"Error: {text}" if text else "")


class MainWindow(QMainWindow):
    closed_to_tray = Signal()

    PAGES = ("Dashboard", "Voice Commands", "Hand Gestures", "Face Expressions", "Applications",
             "Files & Websites", "Settings")

    def __init__(self, store: ConfigStore, controller):
        super().__init__()
        self.store = store
        self.controller = controller
        self._really_quit = False
        self.setWindowTitle("Hand&Voice")
        self.setWindowIcon(make_app_icon())
        self.resize(1000, 640)
        self.setMinimumSize(820, 520)

        root = QWidget()
        root.setObjectName("root")
        self.setCentralWidget(root)
        outer = QHBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        sidebar = QWidget()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(210)
        sb = QVBoxLayout(sidebar)
        sb.setContentsMargins(0, 0, 0, 0)
        logo = QLabel("Hand&Voice")
        logo.setObjectName("logo")
        sb.addWidget(logo)
        self.nav = QListWidget()
        self.nav.setObjectName("nav")
        self.nav.addItems(self.PAGES)
        self.nav.setFocusPolicy(Qt.NoFocus)
        self.nav.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        sb.addWidget(self.nav, 1)
        outer.addWidget(sidebar)

        right = QVBoxLayout()
        right.setSpacing(0)
        self.header = Header()
        right.addWidget(self.header)
        self.stack = QStackedWidget()
        self.dashboard = DashboardPage()
        self.applications = ApplicationsPage(controller)
        self.settings_page = SettingsPage(controller)
        self.files_page = FilesWebsitesPage(controller)
        self.voice_page = VoiceCommandsPage(controller)
        self.gesture_page = HandGesturesPage(controller)
        self.face_page = FacePage(controller)
        for page in (
            self.dashboard, self.voice_page, self.gesture_page, self.face_page,
            self.applications, self.files_page, self.settings_page,
        ):
            self.stack.addWidget(page)
        right.addWidget(self.stack, 1)
        outer.addLayout(right, 1)

        self.nav.currentRowChanged.connect(self.stack.setCurrentIndex)
        self.nav.setCurrentRow(0)
        self.refresh_from_settings()

    def refresh_from_settings(self) -> None:
        s = self.store.settings
        self.apply_state("mode", self.controller.mode_manager.mode.value)
        self.apply_state("listen_mode", s["listen_mode"])
        self.apply_state("mic", self.controller.mic_state)
        self.apply_state("camera", "on" if self.controller.camera_on else "off")
        self.apply_state("index", str(self.controller.indexer.count()))
        self.apply_state("registry", str(len(self.controller.registry)))
        self.refresh_history()

    def apply_state(self, kind: str, value: str) -> None:
        """Update header and dashboard from a controller state change."""
        if kind == "mode":
            self.header.set_mode(value)
            self.dashboard.set_card("Mode", MODE_LABELS.get(value, value))
            self.dashboard.set_mode(value)
            self.face_page.set_mode(value)
        elif kind == "ptt_key":
            self.settings_page.set_ptt_key(value)
        elif kind == "listen_mode":
            self.header.set_listen_mode(value)
            self.dashboard.set_card("Listen mode", LISTEN_LABELS.get(value, value))
            self.settings_page.sync_listen_mode(value)
        elif kind == "mic":
            self.header.set_mic(value)
            self.dashboard.set_card("Microphone", theme.MIC_STATES[value][0])
        elif kind == "camera":
            on = value == "on"
            self.header.set_camera(on)
            self.dashboard.set_card("Camera", "ON" if on else "OFF")
            if not on:
                self.gesture_page.set_status(self.controller.gesture_status, camera=False)
                self.face_page.set_status(self.controller.gesture_status, camera=False)
        elif kind == "registry":
            self.dashboard.set_card("App registry", f"{value} apps")
            self.applications.reload()
        elif kind == "index":
            self.dashboard.set_card("File index", f"{value} files")
            self.files_page.set_index_count(value)
        elif kind == "grammar_skipped":
            self.applications.set_skipped(value)
        elif kind == "voice_error":
            self.header.set_last_error(value)

    def on_gesture(self, kind: str, payload) -> None:
        if kind == "status" and self.controller.camera_on:
            if self.isVisible() and not self.isMinimized():     # nobody sees it in the tray: do no work
                if payload.pipeline == "face":
                    self.face_page.set_status(payload, camera=True)
                else:
                    self.gesture_page.set_status(payload, camera=True)
        elif kind == "event":
            when, name, phase = self.controller.recent_gesture_events[0]
            if not name.startswith("WINK_"):                    # face events are shown on the Face page
                self.gesture_page.add_event(when, name, phase)

    def refresh_history(self) -> None:
        c = self.controller
        self.dashboard.refresh_history(c.feedback.actions, c.feedback.texts, c.usage.top(5))
        self.voice_page.refresh_recent()

    def request_quit(self) -> None:
        self._really_quit = True
        self.close()

    def closeEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        # Only the user's click on the window's X means "hide to the tray". A close that does
        # not come from the user (QApplication.quit(), Windows logoff / shutdown) must be
        # accepted, otherwise Qt cancels the quit and Windows reports the app as blocking shutdown.
        if self._really_quit or not event.spontaneous():
            event.accept()
        else:
            event.ignore()
            self.hide()
            self.closed_to_tray.emit()
