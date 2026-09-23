from localflow.hotkey import (BLOCKED, HOLDING, IDLE, LOCKED, HotkeyMachine, combo_matches,
                              combo_text, format_combo, parse_combo)


class Clock:
    t = 0.0

    def __call__(self):
        return self.t


def make(mode="hold", combo="ctrl+cmd"):
    events = []
    clock = Clock()
    m = HotkeyMachine(combo, mode=mode, min_hold=0.3, clock=clock,
                      on_start=lambda: events.append("start"), on_stop=lambda: events.append("stop"),
                      on_cancel=lambda: events.append("cancel"), on_lock=lambda: events.append("lock"))
    return m, events, clock


def test_parse_aliases():
    assert parse_combo("Ctrl + Win") == {"ctrl", "cmd"}
    assert parse_combo("control+option") == {"ctrl", "alt"}
    assert parse_combo("right_option") == {"alt_r"}


def test_matching_sides_and_exactness():
    assert combo_matches({"ctrl", "cmd"}, {"ctrl_l", "cmd_l"})
    assert combo_matches({"ctrl", "cmd"}, {"ctrl_r", "cmd_l"})
    assert not combo_matches({"ctrl", "cmd"}, {"ctrl_l"})
    assert not combo_matches({"ctrl", "cmd"}, {"ctrl_l", "cmd_l", "d"})
    assert combo_matches({"alt_r"}, {"alt_r"})
    assert not combo_matches({"alt_r"}, {"alt_l"})


def test_hold_to_talk():
    m, ev, clock = make()
    m.press("ctrl_l")
    assert ev == []
    m.press("cmd_l")
    assert ev == ["start"] and m.state == HOLDING
    m.press("cmd_l")  # auto-repeat is ignored
    clock.t = 2.0
    m.release("cmd_l")
    assert ev == ["start", "stop"] and m.state == BLOCKED
    m.release("ctrl_l")
    assert m.state == IDLE


def test_short_tap_is_cancelled():
    m, ev, clock = make()
    m.press("ctrl_l"); m.press("cmd_l")
    clock.t = 0.1
    m.release("ctrl_l")
    assert ev == ["start", "cancel"]


def test_other_shortcut_cancels():
    m, ev, clock = make()
    m.press("ctrl_l"); m.press("cmd_l"); m.press("d")
    assert ev == ["start", "cancel"]
    m.release("d"); m.release("cmd_l"); m.release("ctrl_l")
    assert m.state == IDLE and ev == ["start", "cancel"]


def test_hands_free_lock_then_finish():
    m, ev, clock = make()
    m.press("ctrl_l"); m.press("cmd_l"); m.press("space")
    assert m.state == LOCKED and ev == ["start", "lock"]
    assert m.wants_suppress("space")
    m.release("space"); m.release("cmd_l"); m.release("ctrl_l")
    assert m.state == LOCKED and ev == ["start", "lock"]  # keeps recording hands-free
    clock.t = 30
    m.press("ctrl_l"); m.press("cmd_l")
    assert ev == ["start", "lock", "stop"]
    m.release("cmd_l"); m.release("ctrl_l")
    assert m.state == IDLE


def test_escape_cancels_hands_free():
    m, ev, clock = make()
    m.press("ctrl_l"); m.press("cmd_l"); m.press("space")
    m.release("space"); m.release("cmd_l"); m.release("ctrl_l")
    m.press("esc")
    assert ev[-1] == "cancel"
    m.release("esc")
    assert m.state == IDLE


def test_typing_while_hands_free_does_not_stop():
    m, ev, clock = make()
    m.press("ctrl_l"); m.press("cmd_l"); m.press("space")
    m.release("space"); m.release("cmd_l"); m.release("ctrl_l")
    m.press("ctrl_l"); m.release("ctrl_l"); m.press("a"); m.release("a")
    assert m.state == LOCKED and ev == ["start", "lock"]


def test_toggle_mode():
    m, ev, clock = make(mode="toggle", combo="alt_r")
    m.press("alt_r"); m.release("alt_r")
    assert m.state == LOCKED and ev == ["start", "lock"]
    m.press("alt_r")
    assert ev == ["start", "lock", "stop"]
    m.release("alt_r")
    assert m.state == IDLE


def test_combo_text_and_format():
    assert combo_text({"ctrl_l", "cmd_l"}) == "ctrl+cmd"
    assert combo_text({"alt_r"}) == "alt_r"
    assert combo_text({"alt_l", "shift_r", "z"}) == "alt+shift_r+z"
    assert format_combo("ctrl+cmd") in ("Ctrl+Win", "Ctrl+Cmd")
