from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QFrame, QGridLayout, QHBoxLayout, QHeaderView, QLabel, QListWidget,
    QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from ui.page_base import Page, make_card


class DashboardPage(Page):
    overlay_test_requested = Signal()
    pick_requested = Signal(int)
    mode_requested = Signal(str)          # VOICE_ONLY | GESTURE_ONLY | HYBRID
    pause_toggle_requested = Signal()
    retry_requested = Signal()

    def __init__(self):
        super().__init__("Dashboard", "Live status of Hand&Voice.")
        grid_host = QWidget()
        grid = QGridLayout(grid_host)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(12)
        self._values: dict[str, QLabel] = {}
        for i, name in enumerate(("Mode", "Listen mode", "Microphone", "Camera", "File index", "App registry")):
            card, value = make_card(name)
            value.setWordWrap(True)
            self._values[name] = value
            grid.addWidget(card, i // 3, i % 3)
        self.root.addWidget(grid_host)

        # mode switch buttons (also: tray menu, voice, Ctrl+Alt+R resumes from Pause)
        mode_row = QHBoxLayout()
        self._mode_buttons: dict[str, QPushButton] = {}
        for label, key in (("Voice Only", "VOICE_ONLY"), ("Gesture Only", "GESTURE_ONLY"), ("Hybrid", "HYBRID"),
                           ("Face Only", "FACE_ONLY")):
            b = QPushButton(label)
            b.setCheckable(True)
            b.clicked.connect(lambda _=False, k=key: self.mode_requested.emit(k))
            mode_row.addWidget(b)
            self._mode_buttons[key] = b
        self.pause_button = QPushButton("Pause")
        self.pause_button.setCheckable(True)
        self.pause_button.clicked.connect(lambda _=False: self.pause_toggle_requested.emit())
        mode_row.addWidget(self.pause_button)
        mode_row.addStretch(1)
        self.root.addLayout(mode_row)

        # pending choice (numbered picker) - clickable alternative to saying the number
        self._picker_box = QFrame()
        self._picker_box.setObjectName("card")
        pl = QVBoxLayout(self._picker_box)
        self._picker_title = QLabel("")
        pl.addWidget(self._picker_title)
        self._picker_buttons = QHBoxLayout()
        pl.addLayout(self._picker_buttons)
        self._picker_box.hide()
        self.root.addWidget(self._picker_box)

        cols = QHBoxLayout()
        left = QVBoxLayout()
        left.addWidget(self._dim("Last 10 actions"))
        self.actions = QTableWidget(0, 4)
        self.actions.setHorizontalHeaderLabels(["Time", "Source", "Command", "Result"])
        self.actions.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        self.actions.verticalHeader().hide()
        self.actions.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.actions.setSelectionMode(QAbstractItemView.NoSelection)
        left.addWidget(self.actions, 1)
        cols.addLayout(left, 3)
        right = QVBoxLayout()
        right.addWidget(self._dim("Frequently used apps"))
        self.frequent = QListWidget()
        self.frequent.setMaximumHeight(110)
        right.addWidget(self.frequent)
        right.addWidget(self._dim("Last 20 recognized texts"))
        self.texts = QListWidget()
        right.addWidget(self.texts, 1)
        cols.addLayout(right, 2)
        self.root.addLayout(cols, 1)

        bottom = QHBoxLayout()
        btn = QPushButton("Test overlay")
        btn.clicked.connect(self.overlay_test_requested)
        self.retry_button = QPushButton("Retry microphone and camera")
        self.retry_button.clicked.connect(self.retry_requested)
        bottom.addWidget(btn)
        bottom.addWidget(self.retry_button)
        bottom.addStretch(1)
        self.root.addLayout(bottom)

    @staticmethod
    def _dim(text: str) -> QLabel:
        lab = QLabel(text)
        lab.setObjectName("dim")
        return lab

    def set_mode(self, mode: str) -> None:
        for key, button in self._mode_buttons.items():
            button.setChecked(key == mode)
        self.pause_button.setChecked(mode == "PAUSED")
        self.pause_button.setText("Resume" if mode == "PAUSED" else "Pause")

    def set_card(self, name: str, value: str) -> None:
        if name in self._values:
            self._values[name].setText(value)

    # ----- picker -----
    def show_picker(self, title: str, options: list[str]) -> None:
        self._picker_title.setText(title + " - choose one:")
        while self._picker_buttons.count():
            item = self._picker_buttons.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        for i, name in enumerate(options, 1):
            b = QPushButton(f"{i}. {name}")
            b.clicked.connect(lambda _=False, n=i: self.pick_requested.emit(n))
            self._picker_buttons.addWidget(b)
        self._picker_buttons.addStretch(1)
        self._picker_box.show()

    def hide_picker(self) -> None:
        self._picker_box.hide()

    # ----- history -----
    def refresh_history(self, actions, texts, frequent) -> None:
        self.actions.setRowCount(len(actions))
        for r, a in enumerate(actions):
            for c, val in enumerate((a.time, a.source, a.command, a.result)):
                self.actions.setItem(r, c, QTableWidgetItem(val))
        self.texts.clear()
        self.texts.addItems(list(texts))
        self.frequent.clear()
        self.frequent.addItems([f"{name}  ({count})" for name, count in frequent])
