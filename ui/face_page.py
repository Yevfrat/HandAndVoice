from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout, QFrame, QHBoxLayout,
    QHeaderView, QLabel, QProgressBar, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout,
)

from gestures.face import WINK_LEFT, WINK_RIGHT
from gestures.face_tracker import MODEL_FILE
from gestures.types import GestureStatus
from ui import theme
from ui.page_base import Page

STYLES = [
    ("Browser tabs (next / previous window outside a browser)", "TABS"),
    ("Pages: Page Down / Page Up (documents, PDFs, long web pages)", "PAGES"),
    ("Browser history: Forward / Back", "HISTORY"),
]
ACTION_TEXT = {
    "TABS": ("Next tab / window", "Previous tab / window"),
    "PAGES": ("Page Down", "Page Up"),
    "HISTORY": ("Forward", "Back"),
}


class FacePage(Page):
    start_requested = Signal()      # handled by main.py (asks for confirmation first)
    leave_requested = Signal()

    def __init__(self, controller):
        super().__init__(
            "Face Expressions",
            "A separate mode that you switch on yourself. While it is on, the microphone is CLOSED and "
            "hand gestures are OFF: only your face controls the computer.")
        self.controller = controller
        self.store = controller.store
        self._in_face_mode = False

        row = QHBoxLayout()
        self.toggle = QPushButton("")
        self.toggle.clicked.connect(self._toggle)
        row.addWidget(self.toggle)
        self.model_label = QLabel("")
        self.model_label.setObjectName("dim")
        self.model_label.setWordWrap(True)
        row.addWidget(self.model_label, 1)
        self.root.addLayout(row)

        card = QFrame()
        card.setObjectName("card")
        cl = QVBoxLayout(card)
        self.headline = QLabel("")
        self.headline.setStyleSheet("font-size: 20pt; font-weight: 600;")
        cl.addWidget(self.headline)
        self.right_bar = self._eye_bar()
        self.left_bar = self._eye_bar()
        for text, bar in (("Your RIGHT eye", self.right_bar), ("Your LEFT eye", self.left_bar)):
            line = QHBoxLayout()
            lab = QLabel(text)
            lab.setMinimumWidth(130)
            line.addWidget(lab)
            line.addWidget(bar, 1)
            cl.addLayout(line)
        self.hint = QLabel("")
        self.hint.setObjectName("dim")
        self.hint.setWordWrap(True)
        cl.addWidget(self.hint)
        self.root.addWidget(card)

        form = QFormLayout()
        self.style = QComboBox()
        for text, value in STYLES:
            self.style.addItem(text, value)
        self.style.setCurrentIndex(max(0, self.style.findData(self.store.get("face_wink_style"))))
        self.style.activated.connect(self._style_changed)
        form.addRow("What \"next page\" means", self.style)
        self.hold = QDoubleSpinBox()
        self.hold.setRange(0.2, 3.0)
        self.hold.setSingleStep(0.1)
        self.hold.setValue(float(self.store.get("face_hold_seconds")))
        self.hold.editingFinished.connect(
            lambda: self.controller.set_face_setting("face_hold_seconds", self.hold.value()))
        form.addRow("Hold the closed eye (seconds)", self.hold)
        self.swap = QCheckBox("Left and right are swapped (tick this if the wrong eye reacts)")
        self.swap.setChecked(bool(self.store.get("face_swap_eyes")))
        self.swap.toggled.connect(lambda on: self.controller.set_face_setting("face_swap_eyes", bool(on)))
        form.addRow("Calibration", self.swap)
        self.root.addLayout(form)

        self.table = QTableWidget(3, 2)
        self.table.setHorizontalHeaderLabels(["What you do", "What happens"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.verticalHeader().hide()
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setMaximumHeight(150)
        self.root.addWidget(self.table)
        self._fill_table()
        self.root.addStretch(1)

        self.refresh_model()
        self.set_mode(controller.mode_manager.mode.value)

    # ---------------- widgets ----------------
    @staticmethod
    def _eye_bar() -> QProgressBar:
        bar = QProgressBar()
        bar.setRange(0, 100)
        bar.setFormat("%p% open")
        return bar

    def _fill_table(self) -> None:
        nxt, prev = ACTION_TEXT.get(self.store.get("face_wink_style"), ACTION_TEXT["TABS"])
        rows = [("Close your RIGHT eye, keep the other open", nxt),
                ("Close your LEFT eye, keep the other open", prev),
                ("Blink with both eyes", "Nothing (normal blinking is ignored)")]
        for r, (a, b) in enumerate(rows):
            self.table.setItem(r, 0, QTableWidgetItem(a))
            self.table.setItem(r, 1, QTableWidgetItem(b))

    def _style_changed(self, index: int) -> None:
        self.controller.set_face_setting("face_wink_style", self.style.itemData(index))
        self._fill_table()

    def showEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        super().showEvent(event)
        self.refresh_model()

    def refresh_model(self) -> None:
        installed = MODEL_FILE.is_file()
        self.model_label.setText(
            "Face model: installed" if installed else
            "Face model: MISSING. Settings > Download missing models (about 4 MB), then come back.")
        self.toggle.setEnabled(installed or self._in_face_mode)

    # ---------------- mode ----------------
    def _toggle(self) -> None:
        (self.leave_requested if self._in_face_mode else self.start_requested).emit()

    def set_mode(self, mode: str) -> None:
        self._in_face_mode = mode == "FACE_ONLY"
        self.toggle.setText("Leave Face Only mode" if self._in_face_mode
                            else "Start Face Only mode (microphone and hand gestures turn OFF)")
        self.refresh_model()
        if not self._in_face_mode:
            self.set_status(GestureStatus(), camera=False)

    # ---------------- live ----------------
    def set_status(self, st: GestureStatus, camera: bool) -> None:
        if not self._in_face_mode or not camera:
            self.headline.setText("Face Only mode is OFF")
            self.headline.setStyleSheet(f"font-size: 20pt; font-weight: 600; color: {theme.GREY};")
            self.right_bar.setValue(0)
            self.left_bar.setValue(0)
            self.hint.setText("Press the button above to start. You leave it with this page's button, the "
                              "tray menu, the Dashboard or Ctrl+Alt+R.")
            return
        self.right_bar.setValue(int(st.right_eye * 100))
        self.left_bar.setValue(int(st.left_eye * 100))
        if not st.face:
            self.headline.setText("No face in view")
            self.headline.setStyleSheet(f"font-size: 20pt; font-weight: 600; color: {theme.GREY};")
            self.hint.setText("Look at the camera." + ("  (idle: scanning slowly)" if st.idle else ""))
        elif st.calibrating:
            self.headline.setText("Learning your eyes...")
            self.headline.setStyleSheet(f"font-size: 20pt; font-weight: 600; color: {theme.AMBER};")
            self.hint.setText("Look at the camera with both eyes open for a second.")
        elif st.gesture == WINK_RIGHT:
            self._wink("RIGHT eye closed", st)
        elif st.gesture == WINK_LEFT:
            self._wink("LEFT eye closed", st)
        else:
            self.headline.setText("Both eyes open")
            self.headline.setStyleSheet(f"font-size: 20pt; font-weight: 600; color: {theme.ACCENT};")
            self.hint.setText(f"{st.fps:.1f} FPS. Close one eye and hold it to turn the page.")

    def _wink(self, text: str, st: GestureStatus) -> None:
        self.headline.setText(text)
        self.headline.setStyleSheet(f"font-size: 20pt; font-weight: 600; color: {theme.GREEN};")
        self.hint.setText("Done." if st.started else f"Keep it closed... {int(st.progress * 100)}%")
