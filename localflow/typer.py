"""Type text into the focused app as keystrokes (live typing), leaving the clipboard alone.

Characters are sent as Unicode keystrokes, independent of keyboard layout. If the
user is physically holding modifiers (push-to-talk: Ctrl+Win / Control+Option),
those would turn letters into shortcuts, so, like AutoHotkey's Send, the held
modifiers are released for the burst and pressed again afterwards (Windows); on
macOS each event carries explicit empty modifier flags.
"""
import ctypes
import logging
import sys
import time

log = logging.getLogger(__name__)
IS_WIN = sys.platform == "win32"
IS_MAC = sys.platform == "darwin"

TAG = 0x4C46  # dwExtraInfo on every event we send, so our own hotkey listener can skip them

# Our key names -> Windows virtual-key codes, for modifiers we may need to lift.
WIN_MOD_VK = {"ctrl_l": 0xA2, "ctrl_r": 0xA3, "alt_l": 0xA4, "alt_r": 0xA5, "shift_l": 0xA0,
              "shift_r": 0xA1, "cmd_l": 0x5B, "cmd_r": 0x5C}


if IS_WIN:
    from ctypes import wintypes

    ULONG_PTR = ctypes.c_size_t

    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                    ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]

    class MOUSEINPUT(ctypes.Structure):
        _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                    ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]

    class _U(ctypes.Union):
        _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT)]

    class INPUT(ctypes.Structure):
        _anonymous_ = ("u",)
        _fields_ = [("type", wintypes.DWORD), ("u", _U)]

    KEYEVENTF_KEYUP, KEYEVENTF_UNICODE, INPUT_KEYBOARD = 0x2, 0x4, 1
    VK_RETURN, VK_MASK = 0x0D, 0xE8


def _win_key(vk=0, scan=0, flags=0):
    return INPUT(type=INPUT_KEYBOARD, ki=KEYBDINPUT(wVk=vk, wScan=scan, dwFlags=flags, time=0, dwExtraInfo=TAG))


def _win_send(inputs):
    if not inputs:
        return
    arr = (INPUT * len(inputs))(*inputs)
    sent = ctypes.windll.user32.SendInput(len(inputs), arr, ctypes.sizeof(INPUT))
    if sent != len(inputs):
        log.warning("SendInput sent %d of %d events", sent, len(inputs))


def _win_text_events(text):
    events = []
    for ch in text.replace("\r\n", "\n"):
        if ch == "\n":
            events += [_win_key(VK_RETURN), _win_key(VK_RETURN, flags=KEYEVENTF_KEYUP)]
            continue
        data = ch.encode("utf-16-le")
        for i in range(0, len(data), 2):  # surrogate pairs become two code units
            unit = int.from_bytes(data[i:i + 2], "little")
            events += [_win_key(scan=unit, flags=KEYEVENTF_UNICODE),
                       _win_key(scan=unit, flags=KEYEVENTF_UNICODE | KEYEVENTF_KEYUP)]
    return events


def _type_windows(text, held):
    lift = [WIN_MOD_VK[k] for k in held if k in WIN_MOD_VK]
    before, after = [], []
    if lift:
        if any(vk in (0x5B, 0x5C) for vk in lift):
            before += [_win_key(VK_MASK), _win_key(VK_MASK, flags=KEYEVENTF_KEYUP)]  # no Start menu
        before += [_win_key(vk, flags=KEYEVENTF_KEYUP) for vk in lift]
        after = [_win_key(vk) for vk in lift]
    _win_send(before + _win_text_events(text) + after)


def _type_mac(text):
    import Quartz

    source = Quartz.CGEventSourceCreate(Quartz.kCGEventSourceStatePrivate)
    for ch in text:
        if ch == "\n":
            for down in (True, False):
                ev = Quartz.CGEventCreateKeyboardEvent(source, 36, down)  # Return
                Quartz.CGEventSetFlags(ev, 0)
                Quartz.CGEventPost(Quartz.kCGHIDEventTap, ev)
            continue
        for down in (True, False):
            ev = Quartz.CGEventCreateKeyboardEvent(source, 0, down)
            Quartz.CGEventKeyboardSetUnicodeString(ev, len(ch.encode("utf-16-le")) // 2, ch)
            Quartz.CGEventSetFlags(ev, 0)  # physically held Control/Option must not apply
            Quartz.CGEventPost(Quartz.kCGHIDEventTap, ev)
        time.sleep(0.002)


def type_text(text, held=()):
    """Type ``text`` into the focused window. ``held``: our names of physically held keys."""
    if not text:
        return
    if IS_WIN:
        _type_windows(text, held)
    elif IS_MAC:
        _type_mac(text)
    else:
        from pynput.keyboard import Controller

        Controller().type(text)
