"""Global hotkey handling.

``HotkeyMachine`` is a pure state machine (unit tested); ``HotkeyListener``
feeds it from pynput and handles key suppression for the hands-free lock key.
"""
import sys
import threading
import time

IDLE, HOLDING, LOCKED, BLOCKED = "idle", "holding", "locked", "blocked"

ALIASES = {
    "control": "ctrl", "option": "alt", "opt": "alt", "win": "cmd", "windows": "cmd",
    "super": "cmd", "command": "cmd", "meta": "cmd", "escape": "esc", "return": "enter",
    "altgr": "alt_r", "right_alt": "alt_r", "right_option": "alt_r", "right_ctrl": "ctrl_r",
    "right_cmd": "cmd_r", "right_shift": "shift_r",
}
SIDED = {"ctrl", "alt", "cmd", "shift"}


def parse_combo(text):
    """'Ctrl+Win' -> frozenset({'ctrl', 'cmd'})."""
    tokens = []
    for raw in str(text).lower().replace(" ", "").split("+"):
        if raw:
            tokens.append(ALIASES.get(raw, raw))
    if not tokens:
        raise ValueError("empty hotkey")
    return frozenset(tokens)


def format_combo(text):
    mac = sys.platform == "darwin"
    names = {"ctrl": "Ctrl", "alt": "Option" if mac else "Alt", "cmd": "Cmd" if mac else "Win",
             "shift": "Shift", "space": "Space", "esc": "Esc"}
    order = ["ctrl", "alt", "shift", "cmd"]
    tokens = sorted(parse_combo(text), key=lambda t: (order.index(t) if t in order else 9, t))
    return "+".join(names.get(t, t.replace("_r", " (right)").replace("_l", " (left)").title()) for t in tokens)


def combo_text(keys):
    """{'ctrl_l', 'cmd_l'} -> 'ctrl+cmd' (left-side keys generalised, right-side kept)."""
    order = ["ctrl", "alt", "shift", "cmd"]
    tokens = set()
    for k in keys:
        base = k[:-2] if k.endswith("_l") and k[:-2] in SIDED else k
        tokens.add(base)
    # A lone right-hand modifier stays specific (e.g. right Option as a one-key hotkey).
    return "+".join(sorted(tokens, key=lambda t: (order.index(t) if t in order else 9, t)))


def token_matches(token, key):
    if token == key:
        return True
    return token in SIDED and key in (token + "_l", token + "_r")


def combo_matches(combo, pressed):
    """True when the pressed keys are exactly the combo (no extra keys)."""
    if not pressed:
        return False
    return (all(any(token_matches(t, k) for k in pressed) for t in combo)
            and all(any(token_matches(t, k) for t in combo) for k in pressed))


class HotkeyMachine:
    def __init__(self, combo, mode="hold", lock_key="space", min_hold=0.3,
                 on_start=None, on_stop=None, on_cancel=None, on_lock=None, clock=time.monotonic):
        self.combo = parse_combo(combo) if isinstance(combo, str) else frozenset(combo)
        self.mode = mode
        self.lock_key = lock_key
        self.min_hold = min_hold
        self.on_start = on_start or (lambda: None)
        self.on_stop = on_stop or (lambda: None)
        self.on_cancel = on_cancel or (lambda: None)
        self.on_lock = on_lock or (lambda: None)
        self.clock = clock
        self.state = IDLE
        self.pressed = set()
        self.started_at = 0.0
        self._lock = threading.RLock()

    def wants_suppress(self, key):
        """Swallow the lock key while holding (so it isn't typed into the app)."""
        return self.state in (HOLDING, LOCKED) and key == self.lock_key

    def press(self, key):
        with self._lock:
            if key in self.pressed:
                return  # auto-repeat
            self.pressed.add(key)
            if self.state == IDLE:
                if combo_matches(self.combo, self.pressed):
                    self.started_at = self.clock()
                    self.state = LOCKED if self.mode == "toggle" else HOLDING
                    self.on_start()
                    if self.state == LOCKED:
                        self.on_lock()
            elif self.state == HOLDING:
                if key == self.lock_key:
                    self.pressed.discard(key)
                    self.state = LOCKED
                    self.on_lock()
                elif key == "esc":
                    self._cancel()
                elif not any(token_matches(t, key) for t in self.combo):
                    # User is chording a different shortcut (e.g. Ctrl+Win+D): back off.
                    self._cancel()
            elif self.state == LOCKED:
                if key == self.lock_key:
                    self.pressed.discard(key)
                elif key == "esc":
                    self._cancel()
                elif combo_matches(self.combo, self.pressed):
                    self.state = BLOCKED
                    self.on_stop()

    def release(self, key):
        with self._lock:
            self.pressed.discard(key)
            if self.state == HOLDING and any(token_matches(t, key) for t in self.combo):
                if self.clock() - self.started_at < self.min_hold:
                    self._cancel()
                else:
                    self.state = BLOCKED if self.pressed else IDLE
                    self.on_stop()
            if self.state == BLOCKED and not self.pressed:
                self.state = IDLE

    def _cancel(self):
        self.state = BLOCKED if self.pressed else IDLE
        self.on_cancel()

    def reset(self):
        with self._lock:
            self.pressed.clear()
            self.state = IDLE


def key_name(key):
    """Normalise a pynput key to our names ('ctrl_l', 'cmd_r', 'space', 'v', ...)."""
    from pynput import keyboard

    if isinstance(key, keyboard.Key):
        name = key.name
        mapping = {"ctrl": "ctrl_l", "alt": "alt_l", "alt_gr": "alt_r", "cmd": "cmd_l",
                   "shift": "shift_l"}
        return mapping.get(name, name)
    if isinstance(key, keyboard.KeyCode):
        vk = getattr(key, "vk", None)
        if sys.platform == "win32" and vk is not None:
            if 0x41 <= vk <= 0x5A or 0x30 <= vk <= 0x39:
                return chr(vk).lower()
        if key.char:
            return key.char.lower()
        if vk is not None:
            return "vk%d" % vk
    return None


class HotkeyListener:
    """Runs the pynput listener and dispatches to the dictation machine + one-shot hotkeys."""

    WIN_SPACE_VK = 0x20
    MAC_SPACE_KEYCODE = 49

    def __init__(self, machine, oneshots=None):
        self.machine = machine
        self.oneshots = oneshots or {}  # combo(frozenset) -> callback
        self._listener = None
        self._oneshot_fired = set()
        self._capture = None  # (callback, keys seen) while the user records a new hotkey

    def capture_next(self, callback):
        """Record the next chord the user presses; callback(combo_text) once all keys are up."""
        self.machine.reset()
        self._capture = (callback, set(), set())

    def start(self):
        from pynput import keyboard

        kwargs = {}
        if sys.platform == "win32":
            kwargs["win32_event_filter"] = self._win_filter
        elif sys.platform == "darwin":
            kwargs["darwin_intercept"] = self._mac_intercept
        self._listener = keyboard.Listener(on_press=self._on_press, on_release=self._on_release, **kwargs)
        self._listener.daemon = True
        self._listener.start()

    def stop(self):
        if self._listener:
            self._listener.stop()
            self._listener = None
        self.machine.reset()

    @property
    def is_trusted(self):
        return getattr(self._listener, "IS_TRUSTED", True)

    # --- suppression of the hands-free lock key -------------------------------------
    def _win_filter(self, msg, data):
        if data.vkCode == self.WIN_SPACE_VK and self.machine.lock_key == "space":
            if self.machine.wants_suppress("space") and not (data.flags & 0x10):  # 0x10 = injected
                if msg in (0x0100, 0x0104):  # key down
                    self.machine.press("space")
                else:
                    self.machine.release("space")
                self._listener.suppress_event()
        return True

    def _mac_intercept(self, event_type, event):
        # On macOS pynput has already run on_press/on_release for this event; we only
        # decide whether the event reaches the focused app.
        try:
            import Quartz

            code = Quartz.CGEventGetIntegerValueField(event, Quartz.kCGKeyboardEventKeycode)
            if code == self.MAC_SPACE_KEYCODE and self.machine.lock_key == "space" \
                    and self.machine.wants_suppress("space"):
                return None
        except Exception:
            pass
        return event

    # --- normal dispatch -------------------------------------------------------------
    def _on_press(self, key, injected=False):
        if injected:
            return
        name = key_name(key)
        if not name:
            return
        if self._capture:
            self._capture[1].add(name)
            self._capture[2].add(name)
            return
        self.machine.press(name)
        for combo, callback in self.oneshots.items():
            if combo_matches(combo, self.machine.pressed) and combo not in self._oneshot_fired:
                self._oneshot_fired.add(combo)
                callback()

    def _on_release(self, key, injected=False):
        if injected:
            return
        name = key_name(key)
        if not name:
            return
        if self._capture:
            callback, seen, down = self._capture
            down.discard(name)
            if seen and not down:
                self._capture = None
                callback(combo_text(seen))
            return
        self.machine.release(name)
        self._oneshot_fired = {c for c in self._oneshot_fired
                               if any(token_matches(t, k) for t in c for k in self.machine.pressed)}
