"""Learn from the edits you make to a transcript after LocalFlow pastes or types it.

Right after a transcript lands in a text field, LocalFlow keeps an eye on that one field
(through the OS accessibility interface: UI Automation on Windows, the Accessibility API on
macOS) for a few minutes. If you change the words it wrote ("Kabir nets" -> "Kubernetes"),
the change is learned exactly like a *Save correction*. It reads only the field it just
wrote into, never password fields or terminals, and keeps nothing except the edited
transcript itself.
"""
import difflib
import logging
import re
import sys
import threading
import time

log = logging.getLogger(__name__)
WATCH_SECONDS = 180
POLL_SECONDS = 2.0
MAX_CHARS = 20000  # longer documents aren't diffed (too slow, and too much to hold)


def _norm(text):
    return text.replace("\r\n", "\n").replace("\r", "\n")


def find_edit(baseline, final, transcript):
    """The user's edited version of ``transcript`` inside ``final`` (the field's text now),
    given ``baseline`` (the field right after LocalFlow wrote it). None if the transcript
    wasn't changed, can't be found, or the change doesn't look like a correction (text typed
    before or after it, a rewrite into something else, the whole thing deleted)."""
    baseline, final, t = _norm(baseline), _norm(final), _norm(transcript).strip()
    if not t or len(baseline) > MAX_CHARS or len(final) > MAX_CHARS:
        return None
    s = baseline.rfind(t)
    if s < 0:
        return None
    e = s + len(t)
    ops = difflib.SequenceMatcher(None, baseline, final, autojunk=False).get_opcodes()
    ns = ne = None
    for tag, i1, i2, j1, j2 in ops:
        if ns is None and i1 <= s < i2:  # insertions right before the transcript stay outside
            ns = j1 + (s - i1) if tag == "equal" else j1
        if ne is None and i1 < e <= i2:  # ... and so does anything typed right after it
            ne = j1 + (e - i1) if tag == "equal" else j2
    if ns is None or ne is None or ne <= ns:
        return None
    edited = final[ns:ne].strip()
    if edited == t:
        return None
    old_words, new_words = t.split(), edited.split()
    if not new_words or not 0.6 <= len(new_words) / len(old_words) <= 1.5:
        return None
    if difflib.SequenceMatcher(None, [w.lower() for w in old_words], [w.lower() for w in new_words]).ratio() < 0.6:
        return None  # rewritten, not corrected
    return edited


# ------------------------------------------------------------------ reading the focused field
class _WindowsField:
    """The focused UI Automation element, read through its Value or Text pattern."""

    def __init__(self):
        import comtypes
        import comtypes.client

        try:
            comtypes.CoInitialize()  # this thread may already be set up (importing comtypes does it)
        except OSError:
            pass
        uia = comtypes.client.GetModule("UIAutomationCore.dll")
        self._uia = uia
        self._auto = comtypes.client.CreateObject(uia.CUIAutomation, interface=uia.IUIAutomation)
        self._el = self._auto.GetFocusedElement()
        if self._el is None or self._el.CurrentIsPassword:
            raise LookupError("no readable focused field")

    def read(self):
        uia = self._uia
        for pattern_id, read in (
                (uia.UIA_TextPatternId,
                 lambda p: p.QueryInterface(uia.IUIAutomationTextPattern).DocumentRange.GetText(MAX_CHARS + 1)),
                (uia.UIA_ValuePatternId,
                 lambda p: p.QueryInterface(uia.IUIAutomationValuePattern).CurrentValue)):
            try:
                pat = self._el.GetCurrentPattern(pattern_id)
                if pat:
                    return read(pat)
            except Exception as exc:
                log.debug("edit watch: pattern %s unreadable: %r", pattern_id, exc)
        return None


class _MacField:
    """The focused Accessibility element's value."""

    def __init__(self):
        import HIServices

        self._hi = HIServices
        err, el = HIServices.AXUIElementCopyAttributeValue(
            HIServices.AXUIElementCreateSystemWide(), HIServices.kAXFocusedUIElementAttribute, None)
        if err or el is None:
            raise LookupError("no focused element")
        err, subrole = HIServices.AXUIElementCopyAttributeValue(el, HIServices.kAXSubroleAttribute, None)
        if not err and str(subrole) == "AXSecureTextField":
            raise LookupError("password field")
        self._el = el

    def read(self):
        err, value = self._hi.AXUIElementCopyAttributeValue(self._el, self._hi.kAXValueAttribute, None)
        return None if err or value is None else str(value)


def focused_field():
    if sys.platform == "win32":
        return _WindowsField()
    if sys.platform == "darwin":
        return _MacField()
    raise LookupError("unsupported platform")


class EditWatcher:
    """Watches one field after each transcript; calls ``on_edit(item_id, old, new)``."""

    def __init__(self, on_edit, field_factory=focused_field, watch_seconds=WATCH_SECONDS, poll=POLL_SECONDS):
        self.on_edit = on_edit
        self.field_factory = field_factory
        self.watch_seconds = watch_seconds
        self.poll = poll
        self._current = None  # stop Event of the running watch

    def watch(self, item_id, transcript):
        """Start watching the focused field (call right after the paste/typing)."""
        self.stop()
        stop = threading.Event()
        self._current = stop
        threading.Thread(target=self._run, args=(item_id, transcript, stop), daemon=True,
                         name="edit-watch").start()

    def stop(self):
        """End the current watch now (it still reports an edit made so far)."""
        if self._current:
            self._current.set()
            self._current = None

    def _run(self, item_id, transcript, stop):
        time.sleep(0.4)  # let the target app take the paste
        try:
            field = self.field_factory()
            baseline = field.read()
        except Exception as exc:
            log.debug("edit watch: can't read the field (%s)", exc)
            return
        if not baseline or _norm(transcript).strip() not in _norm(baseline):
            log.debug("edit watch: the field doesn't expose the text we wrote; not watching")
            return
        last = baseline
        deadline = time.monotonic() + self.watch_seconds
        while not stop.wait(self.poll) and time.monotonic() < deadline:
            try:
                value = field.read()
            except Exception:
                value = None
            if value is None:
                break  # the field is gone (window closed, page changed)
            last = value
        edited = find_edit(baseline, last, transcript)
        if edited:
            log.info("learned from an edit to transcript %s", item_id)
            try:
                self.on_edit(item_id, _norm(transcript).strip(), edited)
            except Exception:
                log.exception("applying an edit failed")


def words_changed(old, new):
    """[("Kabir nets", "Kubernetes"), ...] for a short status line."""
    a, b = re.findall(r"\S+", old), re.findall(r"\S+", new)
    out = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b).get_opcodes():
        if tag != "equal":
            out.append((" ".join(a[i1:i2]), " ".join(b[j1:j2])))
    return out
