"""Which monitor is the user working on? Returns that monitor's work area (excluding
taskbar / Dock / menu bar) in Tk screen coordinates: origin at the top-left of the
primary display, y growing downward."""
import logging
import sys

log = logging.getLogger(__name__)


def _windows_work_area():
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32

    class MONITORINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                    ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]

    user32.MonitorFromWindow.restype = ctypes.c_void_p
    user32.MonitorFromWindow.argtypes = [ctypes.c_void_p, wintypes.DWORD]
    user32.MonitorFromPoint.restype = ctypes.c_void_p
    user32.MonitorFromPoint.argtypes = [wintypes.POINT, wintypes.DWORD]
    user32.GetMonitorInfoW.argtypes = [ctypes.c_void_p, ctypes.POINTER(MONITORINFO)]
    user32.GetForegroundWindow.restype = ctypes.c_void_p
    MONITOR_DEFAULTTONEAREST = 2

    hwnd = user32.GetForegroundWindow()
    if hwnd:
        monitor = user32.MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST)
    else:
        pt = wintypes.POINT()
        user32.GetCursorPos(ctypes.byref(pt))
        monitor = user32.MonitorFromPoint(pt, MONITOR_DEFAULTTONEAREST)
    info = MONITORINFO()
    info.cbSize = ctypes.sizeof(MONITORINFO)
    if not monitor or not user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
        return None
    r = info.rcWork
    return r.left, r.top, r.right - r.left, r.bottom - r.top


def mac_to_tk(visible_frame, primary_height):
    """Cocoa rect (x, y-from-bottom, w, h) -> Tk rect (x, y-from-top, w, h)."""
    x, y, w, h = visible_frame
    return int(x), int(primary_height - (y + h)), int(w), int(h)


def pick_screen(point, frames):
    """Index of the frame containing point (Cocoa coords), else 0."""
    px, py = point
    for i, (x, y, w, h) in enumerate(frames):
        if x <= px < x + w and y <= py < y + h:
            return i
    return 0


def _mac_work_area():
    from AppKit import NSEvent, NSScreen

    screens = list(NSScreen.screens())
    if not screens:
        return None
    primary_h = screens[0].frame().size.height
    frames = [(s.frame().origin.x, s.frame().origin.y, s.frame().size.width, s.frame().size.height)
              for s in screens]
    loc = NSEvent.mouseLocation()  # the screen under the pointer is where the user is working
    screen = screens[pick_screen((loc.x, loc.y), frames)]
    vf = screen.visibleFrame()
    return mac_to_tk((vf.origin.x, vf.origin.y, vf.size.width, vf.size.height), primary_h)


def active_work_area(root=None):
    try:
        if sys.platform == "win32":
            area = _windows_work_area()
        elif sys.platform == "darwin":
            area = _mac_work_area()
        else:
            area = None
        if area:
            return area
    except Exception:
        log.debug("monitor lookup failed", exc_info=True)
    if root is not None:
        return 0, 0, root.winfo_screenwidth(), root.winfo_screenheight()
    return 0, 0, 1280, 720
