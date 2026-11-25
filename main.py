"""Hand&Voice entry point."""
from __future__ import annotations

import atexit
import logging
import sys

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QMessageBox

from core.app_controller import AppController
from core.feedback import FeedbackEvent
from services.config_store import ConfigStore
from services.logger import setup_logging
from ui import theme
from ui.main_window import MainWindow
from ui.overlay import Overlay
from ui.tray import Tray, make_app_icon


def main() -> int:
    store = ConfigStore()
    store.ensure_initialized()
    log_file = setup_logging(store.data_dir, store.get("log_level", "INFO"))
    log = logging.getLogger("main")
    log.info("Hand&Voice starting. Data folder: %s (log: %s)", store.data_dir, log_file)

    app = QApplication(sys.argv)
    app.setApplicationName("Hand&Voice")
    app.setWindowIcon(make_app_icon())
    app.setStyleSheet(theme.STYLESHEET)
    app.setQuitOnLastWindowClosed(False)  # closing the window minimizes to tray

    controller = AppController(store)
    window = MainWindow(store, controller)
    overlay = Overlay()
    tray = Tray(window)

    # ---- controller -> UI ----
    def on_feedback(ev: FeedbackEvent) -> None:
        if ev.kind == "command":
            window.header.set_last_command(ev.title)
        elif ev.kind == "success":
            window.header.set_last_action(ev.title)
            window.header.set_last_error("")
            overlay.show_message(ev.title, ev.body)
        elif ev.kind == "error":
            window.header.set_last_error(ev.title)
            overlay.show_message("Error", ev.title + (f"\n{ev.body}" if ev.body else ""), error=True)
        elif ev.kind == "info":
            overlay.show_message(ev.title, ev.body)
        elif ev.kind == "prompt":
            overlay.show_message(ev.title, ev.body, persistent=True)
        elif ev.kind == "picker":
            overlay.show_picker(ev.title, ev.options)
            window.dashboard.show_picker(ev.title, ev.options)
        elif ev.kind == "clear":
            overlay.clear()
            window.dashboard.hide_picker()
        window.refresh_history()

    last_mic = {"state": None}

    def on_state(kind: str, value: str) -> None:
        window.apply_state(kind, value)
        if kind == "mode":
            tray.set_mode(value)
        if not store.get("show_indicators", True):
            return
        # privacy indicators in the overlay (the header always shows them)
        if kind == "camera":
            overlay.show_message("Camera " + ("ON" if value == "on" else "OFF"))
        elif kind == "mic":
            previous, last_mic["state"] = last_mic["state"], value
            if previous is None and value == "off":
                return                                    # app start: nothing has changed yet
            if previous == "window" and value == "listening":
                return                                    # the command window just closed
            from ui import theme

            overlay.show_message(theme.MIC_STATES[value][0])

    def on_gesture(kind: str, payload) -> None:
        window.on_gesture(kind, payload)
        if kind == "status" and payload.hand and payload.gesture != "NONE":
            from gestures.types import EMOJI, Gesture

            overlay.show_gesture(f"{EMOJI[Gesture(payload.gesture)]}  {payload.gesture}", payload.progress)

    def toggle_overlay() -> None:
        if overlay.info_open:
            overlay.clear()
        else:
            title, body = controller.overlay_info_lines()
            overlay.show_info(title, body)

    controller.gesture_ui.toggle_overlay = toggle_overlay      # open palm
    controller.gesture_ui.close_overlay = overlay.clear        # escape gesture
    # Alt may be held by the gesture switcher: release it however the app ends
    atexit.register(controller.gesture_controller.release_all)

    controller.feedback.add_listener(on_feedback)
    controller.add_state_listener(on_state)
    controller.add_gesture_listener(on_gesture)

    # ---- UI -> controller ----
    def show_window() -> None:
        window.showNormal()
        window.raise_()
        window.activateWindow()

    def toggle_pause() -> None:
        if controller.mode_manager.mode.value == "PAUSED":
            controller.mode_manager.resume()
        else:
            controller.mode_manager.pause()

    def request_mode(key: str) -> None:
        """Every mode switch from the tray, the Dashboard or a page. Face Only needs the model and a
        confirmation, because it turns the microphone and hand gestures OFF."""
        current = controller.mode_manager.mode.value
        if key == "FACE_ONLY" and current != "FACE_ONLY":
            from gestures.face_tracker import MODEL_FILE

            parent = window if window.isVisible() else None
            if not MODEL_FILE.is_file():
                QMessageBox.information(parent, "Face model missing",
                                        "Download the face model first: Settings > Download missing models "
                                        "(about 4 MB).")
                key = current
            elif QMessageBox.question(
                    parent, "Face Only mode",
                    "Face Only turns the MICROPHONE and the HAND GESTURES off. Only your face controls the "
                    "computer: close your right eye = next page, left eye = previous page.\n\n"
                    "To leave: tray menu, the Dashboard buttons, or Ctrl+Alt+R.\n\nStart it?") != QMessageBox.Yes:
                key = current
        if key == current:
            tray.set_mode(current)                      # undo the check mark the click already set
            window.dashboard.set_mode(current)
            return
        controller.mode_manager.set_mode(key)

    tray.show_requested.connect(show_window)
    tray.pause_toggle_requested.connect(toggle_pause)
    tray.mode_requested.connect(request_mode)
    window.dashboard.pause_toggle_requested.connect(toggle_pause)
    window.dashboard.retry_requested.connect(controller.retry_devices)
    window.dashboard.mode_requested.connect(request_mode)
    window.face_page.start_requested.connect(lambda: request_mode("FACE_ONLY"))
    window.face_page.leave_requested.connect(controller.mode_manager.resume)
    tray.set_mode(controller.mode_manager.mode.value)
    window.dashboard.set_mode(controller.mode_manager.mode.value)
    tray.quit_requested.connect(lambda: (window.request_quit(), app.quit()))
    window.closed_to_tray.connect(
        lambda: tray.notify("Hand&Voice", "Still running in the tray. Right-click the icon to quit.")
    )
    window.dashboard.overlay_test_requested.connect(
        lambda: overlay.show_message("Overlay test", "Click-through and auto-hides after 3 seconds.")
    )
    window.dashboard.pick_requested.connect(controller.dispatcher.pick)

    # The UI thread polls the worker queue every 30 ms (workers never touch widgets).
    timer = QTimer()
    timer.setInterval(30)
    timer.timeout.connect(controller.poll)
    timer.start()

    app.aboutToQuit.connect(window.settings_page.shutdown)
    app.aboutToQuit.connect(controller.shutdown)

    tray.show()
    window.show()
    controller.start()
    try:
        code = app.exec()
    finally:
        controller.gesture_controller.release_all()
    timer.stop()
    tray.hide()
    log.info("Hand&Voice exited cleanly")
    return code


if __name__ == "__main__":
    sys.exit(main())
