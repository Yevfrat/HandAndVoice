"""System tray icon and menu."""
from __future__ import annotations

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QAction, QActionGroup, QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from ui import theme


def make_app_icon() -> QIcon:
    pix = QPixmap(64, 64)
    pix.fill(Qt.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.Antialiasing)
    p.setBrush(QColor(theme.BG_SIDEBAR))
    p.setPen(QColor(theme.ACCENT))
    p.drawEllipse(4, 4, 56, 56)
    p.setPen(QColor(theme.ACCENT))
    font = p.font()
    font.setBold(True)
    font.setPointSize(22)
    p.setFont(font)
    p.drawText(pix.rect(), Qt.AlignCenter, "H&V")
    p.end()
    return QIcon(pix)


class Tray(QObject):
    show_requested = Signal()
    pause_toggle_requested = Signal()
    mode_requested = Signal(str)   # VOICE_ONLY | GESTURE_ONLY | HYBRID
    quit_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.icon = QSystemTrayIcon(make_app_icon(), parent)
        self.icon.setToolTip("Hand&Voice")
        menu = QMenu()
        act_show = QAction("Show", menu)
        act_show.triggered.connect(self.show_requested)
        self.act_pause = QAction("Pause", menu)
        self.act_pause.triggered.connect(self.pause_toggle_requested)
        menu.addAction(act_show)
        menu.addAction(self.act_pause)
        mode_menu = menu.addMenu("Mode")
        self._mode_actions: dict[str, QAction] = {}
        group = QActionGroup(mode_menu)
        group.setExclusive(True)
        for label, key in (("Voice Only", "VOICE_ONLY"), ("Gesture Only", "GESTURE_ONLY"), ("Hybrid", "HYBRID"),
                           ("Face Only (manual: mic and hands OFF)", "FACE_ONLY")):
            a = QAction(label, mode_menu)
            a.setCheckable(True)
            a.triggered.connect(lambda _=False, k=key: self.mode_requested.emit(k))
            group.addAction(a)
            mode_menu.addAction(a)
            self._mode_actions[key] = a
        self._mode_group = group
        menu.addSeparator()
        act_quit = QAction("Quit", menu)
        act_quit.triggered.connect(self.quit_requested)
        menu.addAction(act_quit)
        self._menu = menu
        self.icon.setContextMenu(menu)
        self.icon.activated.connect(self._on_activated)

    def _on_activated(self, reason) -> None:
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            self.show_requested.emit()

    def set_paused(self, paused: bool) -> None:
        self.act_pause.setText("Resume" if paused else "Pause")

    def set_mode(self, mode: str) -> None:
        """Check the active mode in the menu and show it in the tooltip."""
        for key, action in self._mode_actions.items():
            action.setChecked(key == mode)
        self.set_paused(mode == "PAUSED")
        self.icon.setToolTip("Hand&Voice - " + mode.replace("_", " ").title())

    def show(self) -> None:
        self.icon.show()

    def notify(self, title: str, text: str) -> None:
        self.icon.showMessage(title, text, QSystemTrayIcon.Information, 3000)

    def hide(self) -> None:
        self.icon.hide()
