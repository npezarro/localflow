import logging
import sys
import time

log = logging.getLogger(__name__)
IS_MAC = sys.platform == "darwin"


def get_clipboard():
    try:
        if IS_MAC:
            from AppKit import NSPasteboard, NSPasteboardTypeString

            value = NSPasteboard.generalPasteboard().stringForType_(NSPasteboardTypeString)
            return str(value) if value is not None else ""
        import pyperclip

        return pyperclip.paste()
    except Exception:
        log.debug("clipboard read failed", exc_info=True)
        return None


def set_clipboard(text):
    if IS_MAC:
        # AppKit directly: pbcopy mangles non-ASCII when launched from Finder (no LANG).
        from AppKit import NSPasteboard, NSPasteboardTypeString

        pb = NSPasteboard.generalPasteboard()
        pb.clearContents()
        pb.setString_forType_(text, NSPasteboardTypeString)
        return
    import pyperclip

    pyperclip.copy(text)


class Paster:
    def __init__(self):
        from pynput.keyboard import Controller

        # Created on the main thread (the macOS layout lookup must happen there).
        self._kb = Controller()

    def paste_shortcut(self):
        from pynput.keyboard import Key

        mod = Key.cmd if IS_MAC else Key.ctrl
        with self._kb.pressed(mod):
            self._kb.press("v")
            self._kb.release("v")

    def deliver(self, text, auto_paste=True, restore_clipboard=False):
        previous = get_clipboard() if restore_clipboard else None
        set_clipboard(text)
        if not auto_paste:
            return
        time.sleep(0.05)
        self.paste_shortcut()
        if restore_clipboard and previous is not None:
            time.sleep(0.4)  # let the target app read the clipboard first
            set_clipboard(previous)
