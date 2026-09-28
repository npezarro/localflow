"""Spoken corrections: press the correction hotkey, say what was wrong, and LocalFlow
fixes your last transcript and learns the fix.

Understood without any AI (so it works offline), for example:
    "Kabir nets should be Kubernetes"          "change Tuesday to Wednesday"
    "replace sigma with Figma"                 "it's Priya, not Maria"
    "Kubernetes is spelled K U B E R N E T E S" "spell it P R I Y A"
    "Kubernetes" (just the right word: replaces whatever sounds most like it)
With an AI clean-up option set up, free-form instructions also work
("make the second sentence a question", "the name at the start is Priya").
"""
import difflib
import re

# 3+ single letters standing alone ("K U B E", "K-U-B-E"); the lookbehind stops the "s"
# of "it's" from being read as a spelled letter.
_LETTERS = re.compile(r"(?i)(?<![\w'’])(?:[a-z](?![\w'’])(?:[\s,.-]+|$)){3,}")


def _clean(s):
    return s.strip().strip(".,!?;:\"'").strip()


def spelled_out(text):
    """'K U B E R N E T E S' / 'K-U-B-E-R...' -> 'Kubernetes' (None if no spelling)."""
    m = _LETTERS.search(text)
    if not m:
        return None
    letters = re.findall(r"[a-zA-Z]", m.group(0))
    if len(letters) < 3:
        return None
    word = "".join(letters).lower()
    return word[0].upper() + word[1:]


def parse(feedback):
    """-> (wrong or None, right) from a spoken correction, or None if not understood.
    wrong=None means "find the part that sounds most like `right`"."""
    f = _clean(feedback)
    spelled = spelled_out(f)
    m = re.match(r"(?i)^(?:please\s+)?(?:change|replace|swap|switch)\s+(.+?)\s+(?:to|with|for|into)\s+(.+)$", f)
    if m:
        return _clean(m.group(1)), spelled or _clean(m.group(2))
    m = re.match(r"(?i)^(?:no[, ]+)?(?:it'?s|it is|that'?s|that is|i said|i meant|should be)\s+(.+?),?\s+not\s+(.+)$", f)
    if m:
        return _clean(m.group(2)), spelled or _clean(m.group(1))
    m = re.match(r"(?i)^(.+?)\s+(?:should be|should have been|is supposed to be|was meant to be|means)\s+(.+)$", f)
    if m and len(m.group(1).split()) <= 5:
        return _clean(m.group(1)), spelled or _clean(m.group(2))
    m = re.match(r"(?i)^(.+?)\s+is\s+spelled\s+(.+)$", f)
    if m:
        return None, spelled or _clean(m.group(1))
    if spelled:
        return None, spelled
    m = re.match(r"(?i)^(?:it'?s|it is|the (?:word|name) is|i said|i meant)\s+(.+)$", f)
    if m:
        return None, _clean(m.group(1))
    if 1 <= len(f.split()) <= 4:
        return None, f  # just the right word(s)
    return None


def _squash(s):
    return re.sub(r"[^a-z0-9]", "", s.lower())


def closest_span(text, target):
    """(start, end) character span in ``text`` of the 1-5 word stretch most similar to
    ``target``, or None if nothing is reasonably close."""
    words = list(re.finditer(r"\S+", text))
    n = max(1, len(target.split()))
    best, best_score = None, 0.0
    for size in range(max(1, n - 1), n + 3):
        for i in range(0, len(words) - size + 1):
            span = (words[i].start(), words[i + size - 1].end())
            score = difflib.SequenceMatcher(None, _squash(text[span[0]:span[1]]), _squash(target)).ratio()
            if score > best_score:
                best, best_score = span, score
    return best if best_score >= 0.5 else None


def apply(text, wrong, right):
    """Apply (wrong -> right) to ``text``. Returns the new text or None if it can't be placed."""
    if wrong:
        pattern = re.compile(r"(?i)(?<!\w)" + re.escape(wrong).replace(r"\ ", r"[\s-]+") + r"(?!\w)")
        if pattern.search(text):
            return pattern.sub(lambda _m: right, text, count=0)
        span = closest_span(text, wrong)
    else:
        span = closest_span(text, right)
    if not span:
        return None
    s, e = span
    chunk = text[s:e]
    trail = re.search(r"[.,!?;:]+$", chunk)  # keep the sentence's punctuation
    return text[:s] + right + (trail.group(0) if trail and not right.endswith(trail.group(0)) else "") + text[e:]


def correct(text, feedback):
    """Rule-based correction: -> (new_text, how) or (None, reason)."""
    parsed = parse(feedback)
    if not parsed:
        return None, "didn't understand “%s”" % feedback.strip()
    wrong, right = parsed
    new = apply(text, wrong, right)
    if not new or new.strip() == text.strip():
        return None, "couldn't find what to change for “%s”" % feedback.strip()
    return new, "%s → %s" % (wrong or "closest match", right)
