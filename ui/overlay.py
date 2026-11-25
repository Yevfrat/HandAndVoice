"""Small frameless, always-on-top, click-through feedback window."""
from __future__ import annotations

import time

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from ui import theme

AUTO_HIDE_MS = 3000


class Overlay(QWidget):
    def __init__(self):
        super().__init__(None)
        self.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.Tool
            | Qt.WindowTransparentForInput
            | Qt.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setFixedWidth(360)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self._box = QWidget()
        self._box.setObjectName("ovbox")
        inner = QVBoxLayout(self._box)
        inner.setContentsMargins(16, 12, 16, 12)
        self._title = QLabel("")
        self._title.setWordWrap(True)
        self._body = QLabel("")
        self._body.setWordWrap(True)
        self._body.setStyleSheet(f"color:{theme.TEXT};")
        inner.addWidget(self._title)
        inner.addWidget(self._body)
        lay.addWidget(self._box)
        self._style(theme.ACCENT)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.hide)
        self.info_open = False            # the open-palm info panel is showing
        self._persistent = False          # a prompt (confirmation, picker, countdown) is showing
        self._message_until = 0.0         # a normal message is showing until this time

    def _style(self, color: str) -> None:
        self._box.setStyleSheet(
            f"#ovbox {{ background: rgba(15,20,25,235); border: 1px solid {color}; border-radius: 12px; }}"
        )
        self._title.setStyleSheet(f"font-weight:600; font-size:11pt; color:{color};")

    def show_message(self, title: str, body: str = "", persistent: bool = False,
                     error: bool = False) -> None:
        """Show a message; it auto-hides after 3 s unless persistent (pending prompt)."""
        self.info_open = False
        self._style(theme.RED if error else theme.ACCENT)
        self._title.setText(title)
        self._body.setText(body)
        self._body.setVisible(bool(body))
        self.adjustSize()
        self._move_to_corner()
        self.show()
        self.raise_()
        self._timer.stop()
        self._persistent = persistent
        self._message_until = time.monotonic() + AUTO_HIDE_MS / 1000
        if not persistent:
            self._timer.start(AUTO_HIDE_MS)

    def show_info(self, title: str, body: str) -> None:
        """The open-palm info panel: stays until toggled off, cancelled or replaced."""
        self.show_message(title, body, persistent=True)
        self.info_open = True

    def show_gesture(self, title: str, progress: float) -> None:
        """Live gesture feedback. Never covers a prompt or a fresh message."""
        if self._persistent or time.monotonic() < self._message_until:
            return
        blocks = int(round(progress * 10))
        self._style(theme.ACCENT)
        self._title.setText(title)
        self._body.setText("#" * blocks + "-" * (10 - blocks) + f"  {int(progress * 100)}%")
        self._body.setVisible(True)
        self.adjustSize()
        self._move_to_corner()
        self.show()
        self._timer.start(900)

    def show_picker(self, title: str, options: list[str]) -> None:
        lines = "\n".join(f"{i}.  {name}" for i, name in enumerate(options, 1))
        self.show_message(title, lines + "\n\nSay 'Hand, one' ... or click on the Dashboard",
                          persistent=True)

    def clear(self) -> None:
        self._timer.stop()
        self._persistent = False
        self.info_open = False
        self._message_until = 0.0
        self.hide()

    def _move_to_corner(self) -> None:
        screen = QGuiApplication.primaryScreen()
        if screen is None:
            return
        area = screen.availableGeometry()
        self.move(area.right() - self.width() - 20, area.bottom() - self.height() - 20)
