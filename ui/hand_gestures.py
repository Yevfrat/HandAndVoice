from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDoubleSpinBox, QFrame, QGridLayout, QHBoxLayout, QHeaderView,
    QLabel, QListWidget, QProgressBar, QSpinBox, QTableWidget, QTableWidgetItem, QVBoxLayout,
)

from gestures.types import EMOJI, Gesture, GestureStatus
from ui import theme
from ui.page_base import Page

ACTION_TEXT = {
    "CLOSE_ALL_APPS": "Close All Apps (asks to confirm)", "CLOSE_WINDOW": "Close Window (asks to confirm)",
    "SLEEP": "Sleep (3 s countdown)", "TOGGLE_OVERLAY": "Show / hide the overlay",
    "ENTER_BROWSER_MODE": "Browser mode", "ENTER_SWITCH_WINDOW": "Switch Window mode",
    "SCROLL_UP": "Scroll up (while held)", "SCROLL_DOWN": "Scroll down (while held)",
    "NEXT_WINDOW_OR_TAB": "Next window / tab", "PREVIOUS_WINDOW_OR_TAB": "Previous window / tab",
    "ENTER_OR_CONFIRM": "Enter / confirm", "PLAY_PAUSE": "Play / pause",
    "ENTER_VOLUME_MODE": "Volume mode", "ESCAPE": "Escape / cancel",
}


class HandGesturesPage(Page):
    def __init__(self, controller):
        super().__init__("Hand Gestures",
                         "Hold a gesture still in front of the camera until the bar is full: its action "
                         "runs. An unknown pose never does anything. Make the 🤟 gesture to cancel.")
        self.controller = controller
        self.store = controller.store

        # ---------------- live preview ----------------
        card = QFrame()
        card.setObjectName("card")
        cl = QVBoxLayout(card)
        top = QHBoxLayout()
        self.big = QLabel("-")
        self.big.setStyleSheet("font-size: 30pt; font-weight: 600;")
        self.info = QLabel("")
        self.info.setObjectName("dim")
        top.addWidget(self.big, 1)
        top.addWidget(self.info, 2)
        cl.addLayout(top)
        self.bar = QProgressBar()
        self.bar.setRange(0, 100)
        self.bar.setTextVisible(True)
        self.bar.setFormat("hold %p%")
        cl.addWidget(self.bar)
        self.state = QLabel("")
        self.state.setObjectName("dim")
        cl.addWidget(self.state)
        self.root.addWidget(card)

        # ---------------- settings ----------------
        s = self.store.settings
        row = QHBoxLayout()
        self.enabled = QCheckBox("Gestures enabled (camera is on in Hybrid and Gesture Only mode)")
        self.enabled.setChecked(bool(s["gestures_enabled"]))
        self.enabled.toggled.connect(self.controller.set_gestures_enabled)
        row.addWidget(self.enabled)
        row.addStretch(1)
        self.root.addLayout(row)

        self.hand = QComboBox()
        self.hand.addItems(["Any", "Right", "Left"])
        self.hand.setCurrentText(s["hand"])
        self.hand.activated.connect(lambda _: self.controller.set_gesture_setting("hand", self.hand.currentText()))
        self.tolerance = QDoubleSpinBox()
        self.tolerance.setRange(0.05, 0.5)
        self.tolerance.setSingleStep(0.01)
        self.tolerance.setValue(float(s["movement_tolerance"]))
        self.tolerance.setToolTip("How much the hand may move while holding a gesture (x hand size). Lower = stricter.")
        self.tolerance.editingFinished.connect(
            lambda: self.controller.set_gesture_setting("movement_tolerance", self.tolerance.value()))
        self.fps = QSpinBox()
        self.fps.setRange(5, 30)
        self.fps.setValue(int(s["camera_fps"]))
        self.fps.editingFinished.connect(lambda: self.controller.set_gesture_setting("camera_fps", self.fps.value()))
        self.camera_index = QSpinBox()
        self.camera_index.setRange(0, 9)
        self.camera_index.setValue(int(s["camera_index"]))
        self.camera_index.editingFinished.connect(
            lambda: self.controller.set_gesture_setting("camera_index", self.camera_index.value()))
        # label above its field, so no label is squeezed and the row fits narrow windows
        grid = QGridLayout()
        grid.setHorizontalSpacing(18)
        grid.setVerticalSpacing(4)
        for col, (text, widget) in enumerate((("Hand", self.hand), ("Movement tolerance", self.tolerance),
                                              ("Camera FPS", self.fps), ("Camera number", self.camera_index))):
            lab = QLabel(text)
            lab.setObjectName("dim")
            grid.addWidget(lab, 0, col)
            grid.addWidget(widget, 1, col)
        grid.setColumnStretch(4, 1)
        self.root.addLayout(grid)

        # ---------------- table + recent events ----------------
        mid = QHBoxLayout()
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Gesture", "Action", "Hold (s)", "On"])
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.verticalHeader().hide()
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setMinimumHeight(300)
        mid.addWidget(self.table, 5)
        right = QVBoxLayout()
        lab = QLabel("Recent gesture events")
        lab.setObjectName("dim")
        right.addWidget(lab)
        self.events = QListWidget()
        right.addWidget(self.events, 1)
        mid.addLayout(right, 3)
        self.root.addLayout(mid, 1)
        self._fill_table()
        self.table.setMinimumWidth(560)            # the action text must stay readable
        self.set_status(GestureStatus(), camera=False)

    # ---------------- table ----------------
    def _fill_table(self) -> None:
        cfg = self.controller.load_gestures_config()
        names = [g.value for g in Gesture if g != Gesture.NONE]
        self.table.setRowCount(len(names))
        for r, name in enumerate(names):
            c = cfg.get(name, {})
            self.table.setItem(r, 0, QTableWidgetItem(f"{EMOJI[Gesture(name)]}  {name}"))
            self.table.setItem(r, 1, QTableWidgetItem(ACTION_TEXT.get(c.get("action", ""), c.get("action", ""))))
            spin = QDoubleSpinBox()
            spin.setRange(0.1, 10.0)
            spin.setSingleStep(0.1)
            spin.setValue(float(c.get("hold_seconds", 1.0)))
            spin.editingFinished.connect(lambda n=name, w=spin: self.controller.set_gesture_hold(n, w.value()))
            self.table.setCellWidget(r, 2, spin)
            box = QCheckBox()
            box.setChecked(bool(c.get("enabled", True)))
            box.toggled.connect(lambda on, n=name: self.controller.set_gesture_enabled(n, on))
            holder = QFrame()
            hl = QHBoxLayout(holder)
            hl.setContentsMargins(8, 0, 0, 0)
            hl.addWidget(box)
            self.table.setCellWidget(r, 3, holder)
        self.table.resizeColumnToContents(0)

    # ---------------- live updates ----------------
    def set_status(self, st: GestureStatus, camera: bool) -> None:
        if not camera:
            self.big.setText("Camera off")
            self.big.setStyleSheet(f"font-size: 22pt; font-weight: 600; color: {theme.GREY};")
            self.info.setText("Gestures need Hybrid or Gesture Only mode and 'Gestures enabled'.")
            self.bar.setValue(0)
            self.state.setText("")
            return
        if not st.hand:
            self.big.setText("No hand")
            self.big.setStyleSheet(f"font-size: 26pt; font-weight: 600; color: {theme.GREY};")
            self.info.setText(f"{st.fps:.1f} FPS" + ("  (idle: scanning slowly to save CPU)" if st.idle else ""))
            self.bar.setValue(0)
            self.state.setText("Show your hand: the camera speeds up at once." if st.idle else "")
            return
        elif st.gesture == "NONE":
            self.big.setText("Hand: unknown pose")
            self.big.setStyleSheet(f"font-size: 22pt; font-weight: 600; color: {theme.AMBER};")
        else:
            g = Gesture(st.gesture)
            self.big.setText(f"{EMOJI[g]}  {st.gesture}")
            self.big.setStyleSheet(f"font-size: 28pt; font-weight: 600; color: {theme.ACCENT};")
        self.info.setText(f"confidence {st.confidence:.0%}    {st.fps:.1f} FPS")
        self.bar.setValue(int(st.progress * 100))
        if st.started:
            self.state.setText("Hold reached: the action ran.")
        elif st.gesture != "NONE" and st.hand:
            self.state.setText("Keep holding still...")
        else:
            self.state.setText("An unknown pose never does anything.")

    def add_event(self, time_text: str, name: str, phase: str) -> None:
        emoji = EMOJI.get(Gesture(name), "") if name in Gesture.__members__ else ""
        self.events.insertItem(0, f"{time_text}   {emoji} {name}   {phase}")
        while self.events.count() > 12:
            self.events.takeItem(self.events.count() - 1)
