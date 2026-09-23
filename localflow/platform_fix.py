"""Per-OS startup fixes."""
import contextlib
import logging
import sys

log = logging.getLogger(__name__)


def pin_macos_keyboard_layout():
    """pynput reads the keyboard layout (TSMGetInputSourceProperty) on its listener
    thread; recent macOS aborts the process when that API runs off the main thread.
    Read it once here, on the main thread, and hand pynput the cached value."""
    if sys.platform != "darwin":
        return
    try:
        from pynput._util import darwin as util_darwin
        from pynput.keyboard import _darwin as kb_darwin

        with util_darwin.keycode_context() as ctx:
            snapshot = ctx

        @contextlib.contextmanager
        def cached_context():
            yield snapshot

        util_darwin.keycode_context = cached_context
        kb_darwin.keycode_context = cached_context
    except Exception:
        log.exception("could not pin macOS keyboard layout")


def macos_accessibility_trusted(prompt=True):
    """True if we may listen to / synthesise keys. With prompt=True macOS shows its dialog."""
    if sys.platform != "darwin":
        return True
    try:
        from ApplicationServices import AXIsProcessTrustedWithOptions, kAXTrustedCheckOptionPrompt

        return bool(AXIsProcessTrustedWithOptions({kAXTrustedCheckOptionPrompt: bool(prompt)}))
    except Exception:
        log.debug("AX trust check unavailable", exc_info=True)
        return True


def windows_dpi_aware():
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass


def windows_no_activate(hwnd):
    """Make a window click-through and never take focus (for the overlay)."""
    if sys.platform != "win32":
        return
    import ctypes

    GWL_EXSTYLE = -20
    WS_EX_NOACTIVATE, WS_EX_TOOLWINDOW, WS_EX_TRANSPARENT, WS_EX_LAYERED, WS_EX_TOPMOST = (
        0x08000000, 0x80, 0x20, 0x80000, 0x8)
    user32 = ctypes.windll.user32
    style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
    user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW
                          | WS_EX_TRANSPARENT | WS_EX_LAYERED | WS_EX_TOPMOST)


def mask_windows_key():
    """Tap an unassigned virtual key so releasing Win after Ctrl+Win doesn't open Start
    (the same trick AutoHotkey uses)."""
    if sys.platform != "win32":
        return
    import ctypes

    VK_MASK, KEYEVENTF_KEYUP = 0xE8, 0x2
    ctypes.windll.user32.keybd_event(VK_MASK, 0, 0, 0)
    ctypes.windll.user32.keybd_event(VK_MASK, 0, KEYEVENTF_KEYUP, 0)
