"""Window helpers: enumerate, focus, minimize/maximize, polite close (WM_CLOSE).

Processes are never killed. Closing posts WM_CLOSE so apps can ask "save changes?".
"""
from __future__ import annotations

import ctypes
import logging
import os
from dataclasses import dataclass
from typing import Iterable, Optional

import psutil
import pywintypes
import win32api
import win32con
import win32gui
import win32process
import win32security

log = logging.getLogger(__name__)

# The desktop, the taskbar and its helpers must never be closed or minimized.
PROTECTED_CLASSES = {"Shell_TrayWnd", "Shell_SecondaryTrayWnd", "Progman", "WorkerW",
                     "NotifyIconOverflowWindow", "TopLevelWindowForOverflowXamlIsland"}
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


@dataclass
class WindowInfo:
    hwnd: int
    title: str
    class_name: str
    pid: int
    process: str
    elevated: bool       # target runs as administrator while we do not
    own: bool            # a Hand&Voice window
    protected: bool      # desktop / taskbar


def self_is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


_SELF_ADMIN = self_is_admin()


def is_elevated_process(pid: int) -> bool:
    """True when the process runs elevated and we do not (keyboard input would be blocked)."""
    if _SELF_ADMIN or pid == os.getpid():
        return False
    try:
        handle = win32api.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        try:
            token = win32security.OpenProcessToken(handle, win32con.TOKEN_QUERY)
            try:
                return bool(win32security.GetTokenInformation(token, win32security.TokenElevation))
            finally:
                win32api.CloseHandle(token)
        finally:
            win32api.CloseHandle(handle)
    except pywintypes.error:
        return True      # cannot even inspect it: treat as a protected/elevated process


def describe(hwnd: int) -> Optional[WindowInfo]:
    if not hwnd or not win32gui.IsWindow(hwnd):
        return None
    try:
        cls = win32gui.GetClassName(hwnd)
        title = win32gui.GetWindowText(hwnd)
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
    except pywintypes.error:
        return None
    try:
        name = psutil.Process(pid).name()
    except psutil.Error:
        name = ""
    return WindowInfo(hwnd, title, cls, pid, name, is_elevated_process(pid),
                      pid == os.getpid(), cls in PROTECTED_CLASSES)


def foreground_info() -> Optional[WindowInfo]:
    return describe(win32gui.GetForegroundWindow())


def can_minimize(hwnd: int) -> bool:
    return bool(win32gui.GetWindowLong(hwnd, win32con.GWL_STYLE) & win32con.WS_MINIMIZEBOX)


def can_maximize(hwnd: int) -> bool:
    return bool(win32gui.GetWindowLong(hwnd, win32con.GWL_STYLE) & win32con.WS_MAXIMIZEBOX)


def minimize(hwnd: int) -> None:
    win32gui.ShowWindow(hwnd, win32con.SW_MINIMIZE)


def maximize(hwnd: int) -> None:
    win32gui.ShowWindow(hwnd, win32con.SW_MAXIMIZE)


def post_close(hwnd: int) -> None:
    """Ask a window to close (WM_CLOSE). The app decides, e.g. "Save changes?"."""
    win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)


def is_window(hwnd: int) -> bool:
    return bool(hwnd) and bool(win32gui.IsWindow(hwnd))


def is_open(hwnd: int) -> bool:
    """Still exists and is visible (a window that is closing disappears quickly)."""
    return is_window(hwnd) and bool(win32gui.IsWindowVisible(hwnd))


def is_cloaked(hwnd: int) -> bool:
    """Windows 10/11 keep invisible 'cloaked' Store-app windows; they are not real windows."""
    value = ctypes.c_int(0)
    try:
        ctypes.windll.dwmapi.DwmGetWindowAttribute(
            ctypes.c_void_p(hwnd), 13, ctypes.byref(value), ctypes.sizeof(value))  # DWMWA_CLOAKED
    except Exception:
        return False
    return value.value != 0


# Windows' own helper processes: they show windows but are not apps the user opened.
SYSTEM_HOST_PROCESSES = {"textinputhost.exe", "searchhost.exe", "searchapp.exe",
                         "startmenuexperiencehost.exe", "shellexperiencehost.exe", "lockapp.exe",
                         "widgets.exe", "systemsettingsbroker.exe"}
FRAME_HOST = "applicationframehost.exe"


def filter_closable(infos: list[WindowInfo]) -> list[WindowInfo]:
    """Drop system helpers and the duplicate inner window of a Store app (its
    ApplicationFrameHost frame already represents it)."""
    infos = [i for i in infos if i.process.lower() not in SYSTEM_HOST_PROCESSES]
    frame_titles = {i.title for i in infos if i.process.lower() == FRAME_HOST}
    return [i for i in infos if i.process.lower() == FRAME_HOST or i.title not in frame_titles]


def closable_windows() -> list[WindowInfo]:
    """Real, visible, titled top-level windows of other apps (never ours, the desktop or taskbar)."""
    out: list[WindowInfo] = []
    for hwnd, title, _pid in visible_windows():
        if win32gui.GetWindow(hwnd, win32con.GW_OWNER) or is_cloaked(hwnd):
            continue                      # owned dialogs close with their main window
        info = describe(hwnd)
        if info is None or info.own or info.protected or title == "Program Manager":
            continue
        out.append(info)
    return filter_closable(out)


def get_foreground_hwnd() -> int:
    return win32gui.GetForegroundWindow()


def get_process_name(hwnd: int) -> Optional[str]:
    try:
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        return psutil.Process(pid).name()
    except (psutil.Error, pywintypes.error):
        return None


def visible_windows() -> list[tuple[int, str, int]]:
    """(hwnd, title, pid) of visible, titled, non-tool top-level windows in z-order."""
    out: list[tuple[int, str, int]] = []

    def cb(hwnd, _):
        if not win32gui.IsWindowVisible(hwnd):
            return True
        title = win32gui.GetWindowText(hwnd)
        if not title:
            return True
        if win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE) & win32con.WS_EX_TOOLWINDOW:
            return True
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        out.append((hwnd, title, pid))
        return True

    win32gui.EnumWindows(cb, None)
    return out


def windows_for_pids(pids: Iterable[int]) -> list[tuple[int, str, int]]:
    wanted = set(pids)
    return [w for w in visible_windows() if w[2] in wanted]


def bring_to_front(hwnd: int) -> bool:
    """Restore and focus a window. Windows limits focus stealing, so the input
    queue is briefly attached to the current foreground window's thread."""
    try:
        if win32gui.IsIconic(hwnd):
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        fg = win32gui.GetForegroundWindow()
        fg_tid, _ = win32process.GetWindowThreadProcessId(fg) if fg else (0, 0)
        cur_tid = win32api.GetCurrentThreadId()
        attached = False
        if fg_tid and cur_tid and fg_tid != cur_tid:
            try:
                win32process.AttachThreadInput(cur_tid, fg_tid, True)
                attached = True
            except pywintypes.error:
                pass
        try:
            win32gui.BringWindowToTop(hwnd)
            win32gui.SetForegroundWindow(hwnd)
        finally:
            if attached:
                win32process.AttachThreadInput(cur_tid, fg_tid, False)
        return win32gui.GetForegroundWindow() == hwnd
    except pywintypes.error as exc:
        log.warning("Could not focus window: %s", exc)
        return False
