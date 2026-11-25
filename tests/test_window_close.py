"""The X button hides to the tray; quit() and system shutdown must never be cancelled."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QApplication

from core.app_controller import AppController
from services.config_store import ConfigStore
from ui.main_window import MainWindow


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(tmp_path, qapp):
    store = ConfigStore(data_dir=tmp_path / "d"); store.ensure_initialized()
    w = MainWindow(store, AppController(store))
    w.show()
    yield w
    w._really_quit = True
    w.close()


def test_a_close_that_is_not_the_users_click_is_accepted(window):
    """QApplication.quit() and Windows logoff send non-spontaneous close events."""
    event = QCloseEvent()
    assert not event.spontaneous()
    window.closeEvent(event)
    assert event.isAccepted()


def test_the_tray_quit_path_closes_for_real(window):
    window.request_quit()
    assert not window.isVisible()


def test_programmatic_quit_is_not_cancelled_by_a_visible_window(window, qapp):
    from PySide6.QtCore import QTimer
    QTimer.singleShot(100, qapp.quit)
    qapp.exec()                                   # returns only if quit() was not cancelled
    assert True


class SpontaneousEvent(QCloseEvent):
    def spontaneous(self):                        # what Qt reports for a click on the window's X
        return True


def test_the_x_button_hides_to_the_tray_instead_of_quitting(window):
    got = []
    window.closed_to_tray.connect(lambda: got.append(1))
    event = SpontaneousEvent()
    window.closeEvent(event)
    assert not event.isAccepted() and not window.isVisible() and got == [1]
