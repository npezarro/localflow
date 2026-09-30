"""Learns how *you* talk, on this computer only (data/learned.json).

Two sources:
- Corrections: when you fix a transcript in the Transcripts tab, the word-level diff
  becomes a replacement ("kabir nets" -> "Kubernetes") and the corrected words
  become vocabulary hints. These apply from the next dictation.
- Repetition: uncommon words you keep saying (names, jargon) are counted per
  dictation; after ``PROMOTE_AT`` dictations they become vocabulary hints.
  "Uncommon" = Whisper's tokenizer needs more than one token for it, which is
  exactly the set of words Whisper tends to misspell.
Everything can be reviewed, removed, or switched off in Settings.
"""
import difflib
import json
import os
import re
import threading
import time

from . import paths

PROMOTE_AT = 3
MAX_VOCAB_HINTS = 40
_WORD = re.compile(r"[A-Za-z][A-Za-z0-9'’\-]{2,}")
_STOP = {"the", "and", "you", "that", "with", "this", "have", "from", "they", "what", "there"}


TOMBSTONE_DAYS = 180


def _empty():
    return {"terms": {}, "replacements": {}, "deleted": {"terms": {}, "replacements": {}}}


def _deleted_tables(data):
    d = data.get("deleted") or {}
    return {"terms": dict(d.get("terms") or {}), "replacements": dict(d.get("replacements") or {})}


def merge_learned(local, remote):
    """Merge two learned.json dicts (this device's and the account copy). Per entry the most
    recently changed version wins; deletions win over older entries; repetition counts of
    words heard in dictation take the higher count."""
    out = _empty()
    now = time.time()
    for table in ("terms", "replacements"):
        dl, dr = _deleted_tables(local)[table], _deleted_tables(remote)[table]
        deleted = {k: max(dl.get(k, 0), dr.get(k, 0)) for k in set(dl) | set(dr)}
        la, ra = local.get(table) or {}, remote.get(table) or {}
        for key in set(la) | set(ra):
            a, b = la.get(key), ra.get(key)
            if a is None or b is None:
                best = dict(a or b)
            else:
                best = dict(a if a.get("t", 0) >= b.get("t", 0) else b)
                if table == "terms" and best.get("source", "heard") == "heard":
                    best["count"] = max(a.get("count", 0), b.get("count", 0))
            if deleted.get(key, 0) >= best.get("t", 0) and key in deleted:
                continue  # forgotten after this version was learned
            out[table][key] = best
        out["deleted"][table] = {k: t for k, t in deleted.items()
                                 if k not in out[table] and now - t < TOMBSTONE_DAYS * 86400}
    return out


def _clean_phrase(words):
    return " ".join(w.strip(".,!?;:\"()[]") for w in words).strip()


class Learner:
    def __init__(self):
        self.path = os.path.join(paths.data_dir(), "learned.json")
        self._lock = threading.Lock()
        self.data = _empty()
        self.on_change = None  # called after every save (account sync schedules an upload)
        try:
            with open(self.path, encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                self.data.update({k: loaded.get(k, {}) for k in ("terms", "replacements")})
                self.data["deleted"] = _deleted_tables(loaded)
        except (OSError, ValueError):
            pass

    def _forget(self, table, key):
        """Delete an entry and leave a dated marker, so a synced copy doesn't bring it back."""
        self.data[table].pop(key, None)
        self.data["deleted"][table][key] = time.time()

    def merge_from(self, remote):
        """Account sync: fold another device's learned.json into ours. Returns the merged data."""
        with self._lock:
            self.data = merge_learned(self.data, remote)
            self._save(notify=False)
            return json.loads(json.dumps(self.data))

    def _save(self, notify=True):
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.data, f, indent=1, ensure_ascii=False)
        os.replace(tmp, self.path)
        if notify and self.on_change:
            try:
                self.on_change()
            except Exception:
                pass

    # ------------------------------------------------------------------ learning
    def observe(self, text, is_uncommon):
        """Count uncommon words once per dictation. Returns newly promoted terms."""
        seen = {}
        for match in _WORD.finditer(text):
            word = match.group(0).strip("'’-")
            if len(word) < 3 or word.lower() in _STOP:
                continue
            seen.setdefault(word.lower(), word)
        promoted = []
        with self._lock:
            for key, word in seen.items():
                if not is_uncommon(word):
                    continue
                entry = self.data["terms"].setdefault(key, {"term": word, "count": 0, "source": "heard",
                                                            "enabled": True})
                entry["count"] += 1
                entry["last"] = entry["t"] = time.time()
                if word[:1].isupper():
                    entry["term"] = word  # prefer the capitalised spelling of names
                if entry["count"] == PROMOTE_AT:
                    promoted.append(entry["term"])
            self._save()
        return promoted

    @staticmethod
    def correction_pairs(original, corrected):
        """[(wrong, right)] word-level replacements between two versions of a transcript."""
        a, b = original.split(), corrected.split()
        norm = lambda ws: [w.lower().strip(".,!?;:\"()[]") for w in ws]  # noqa: E731
        pairs = []
        for op, i1, i2, j1, j2 in difflib.SequenceMatcher(a=norm(a), b=norm(b), autojunk=False).get_opcodes():
            if op != "replace" or i2 - i1 > 4 or j2 - j1 > 4:
                continue
            wrong, right = _clean_phrase(a[i1:i2]), _clean_phrase(b[j1:j2])
            if wrong and right and wrong.lower() != right.lower():
                pairs.append((wrong, right))
        return pairs

    def is_pending(self, original, corrected):
        """Is some part of this correction still waiting to be seen again before it applies?"""
        reps = self.data["replacements"]
        return any(wrong.lower() in reps and reps[wrong.lower()]["count"] < reps[wrong.lower()].get("needs", 1)
                   for wrong, _right in self.correction_pairs(original, corrected))

    def unlearn_correction(self, original, corrected):
        """Take back what a correction taught (you said it wasn't one). Returns the pairs undone."""
        undone = []
        with self._lock:
            reps = self.data["replacements"]
            for wrong, right in self.correction_pairs(original, corrected):
                rep = reps.get(wrong.lower())
                if not rep or rep["to"].lower() != right.lower():
                    continue
                rep["count"] -= 1
                rep["t"] = time.time()
                if rep["count"] <= 0:
                    self._forget("replacements", wrong.lower())
                undone.append((wrong, right))
                still_used = {w.lower() for r in reps.values() if r["count"] >= r.get("needs", 1)
                              for w in r["to"].split()}
                for word in right.split():
                    term = self.data["terms"].get(word.lower())
                    if term and term.get("source") == "correction" and word.lower() not in still_used:
                        self._forget("terms", word.lower())
            self._save()
        return undone

    def learn_correction(self, original, corrected, is_uncommon=lambda w: True, confirm_after=1):
        """Diff the transcript you fixed against what was typed. Returns the [(wrong, right)]
        pairs now in effect. ``confirm_after``: how many times the same fix must be seen before
        it applies (1 for an explicit correction; 2 for an edit made in another app, which may
        be a change of mind rather than a mishearing)."""
        learned = []
        with self._lock:
            for wrong, right in self.correction_pairs(original, corrected):
                rep = self.data["replacements"].setdefault(wrong.lower(), {"to": right, "count": 0,
                                                                           "enabled": True})
                if rep["to"].lower() != right.lower():
                    rep["count"] = 0  # a different fix than before: start counting again
                    rep.pop("needs", None)
                rep.update(to=right, enabled=True, t=time.time())
                rep["count"] += 1
                rep["needs"] = min(rep.get("needs", confirm_after), confirm_after)
                if rep["count"] < rep["needs"]:
                    continue  # pending: seen once in an app edit
                learned.append((wrong, right))
                for word in right.split():
                    if len(word) >= 3 and is_uncommon(word):
                        term = self.data["terms"].setdefault(word.lower(), {"term": word, "count": 0})
                        term.update(term=word, source="correction", enabled=True, t=time.time())
                        term["count"] = max(term["count"], PROMOTE_AT)
            self._save()
        return learned

    # ------------------------------------------------------------------ applying
    def vocabulary(self, limit=MAX_VOCAB_HINTS):
        terms = [t for t in self.data["terms"].values()
                 if t.get("enabled", True) and (t.get("source") == "correction" or t["count"] >= PROMOTE_AT)]
        terms.sort(key=lambda t: (t.get("source") != "correction", -t["count"]))
        return [t["term"] for t in terms[:limit]]

    def replacements(self):
        return {wrong: r["to"] for wrong, r in self.data["replacements"].items()
                if r.get("enabled", True) and r["count"] >= r.get("needs", 1)}

    def apply_to(self, cfg):
        """cfg with learned vocabulary/replacements merged in (your own entries win)."""
        merged = dict(cfg)
        vocab = list(cfg.get("vocabulary") or [])
        lower = {v.lower() for v in vocab}
        vocab += [t for t in self.vocabulary() if t.lower() not in lower]
        merged["vocabulary"] = vocab[:MAX_VOCAB_HINTS + len(cfg.get("vocabulary") or [])]
        reps = self.replacements()
        reps.update(cfg.get("replacements") or {})
        merged["replacements"] = reps
        return merged

    # ------------------------------------------------------------------ review
    def entries(self):
        """Rows for the Settings list: (kind, key, label)."""
        rows = []
        for wrong, r in sorted(self.data["replacements"].items(), key=lambda kv: -kv[1]["count"]):
            if not r.get("enabled", True):
                continue
            if r["count"] >= r.get("needs", 1):
                rows.append(("replacement", wrong, "“%s” → “%s”  (your correction)" % (wrong, r["to"])))
            else:
                rows.append(("replacement", wrong, "“%s” → “%s”  (pending: seen once in an edit; "
                                                   "learned if you make it again)" % (wrong, r["to"])))
        for key, t in sorted(self.data["terms"].items(), key=lambda kv: -kv[1]["count"]):
            if not t.get("enabled", True):
                continue
            if t.get("source") == "correction":
                rows.append(("term", key, "%s  (from your correction)" % t["term"]))
            elif t["count"] >= PROMOTE_AT:
                rows.append(("term", key, "%s  (heard in %d dictations)" % (t["term"], t["count"])))
        return rows

    def remove(self, kind, key):
        """Forget an entry and keep it from being re-learned by repetition."""
        with self._lock:
            table = self.data["replacements" if kind == "replacement" else "terms"]
            if key in table:
                table[key]["enabled"] = False
                table[key]["t"] = time.time()
                self._save()

    def clear(self):
        with self._lock:
            for table in ("terms", "replacements"):
                for key in list(self.data[table]):
                    self._forget(table, key)
            self._save()
