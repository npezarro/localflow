"""Pronunciation dictionary: teach LocalFlow a word by saying it.

Stored as plain files in the data folder, so it's easy to copy or back up:

    data/dictionary/<word-slug>/entry.json     word, what LocalFlow heard for each take
    data/dictionary/<word-slug>/take-1.wav ... your recordings (16 kHz mono)

How it works: each take is transcribed WITHOUT any hints, which shows how the current
speech engine hears you say the word ("Kabir nets"). Each distinct hearing becomes a
replacement to the real spelling, and the word itself becomes a spelling hint. The
recordings are kept so "Re-check" can re-derive the hearings after a model or engine
change without re-recording. A hearing made only of common words ("sigma" for "Figma")
starts disabled, because replacing it would rewrite every real use of that word.
"""
import json
import os
import re
import shutil
import threading
import time

from . import audio, paths

_PUNCT = re.compile(r"[^\w'\s-]")


def normalize(text):
    return " ".join(_PUNCT.sub(" ", text.lower()).split())


def variant_for(heard, word):
    """The part of what was heard that stands for ``word``, or None if nothing to correct.
    A take can be the word alone or said inside a sentence ("we run it on Cabernets");
    only the stretch that sounds most like the word becomes a rule."""
    import difflib

    h, target = normalize(heard), normalize(word)
    if not h or target in h:
        return None  # heard correctly (alone or inside the sentence)
    words, n = h.split(), len(target.split())
    if len(words) <= n + 1:
        return h
    squash = lambda s: s.replace(" ", "").replace("'", "")  # noqa: E731
    best, best_score = None, 0.0
    for size in range(max(1, n - 1), n + 3):
        for i in range(0, len(words) - size + 1):
            chunk = " ".join(words[i:i + size])
            score = difflib.SequenceMatcher(None, squash(chunk), squash(target)).ratio()
            if score > best_score:
                best, best_score = chunk, score
    return best if best_score >= 0.6 else None  # measured: mishearings >= 0.61, unrelated <= 0.59


def slug(word):
    s = re.sub(r"[^a-z0-9]+", "-", word.lower()).strip("-")
    return s or "word"


class Dictionary:
    def __init__(self, root=None):
        self.root = root or os.path.join(paths.data_dir(), "dictionary")
        os.makedirs(self.root, exist_ok=True)
        self._lock = threading.Lock()
        self.entries = {}
        self.reload()

    # ------------------------------------------------------------------ storage
    def reload(self):
        entries = {}
        for name in sorted(os.listdir(self.root)):
            path = os.path.join(self.root, name, "entry.json")
            try:
                with open(path, encoding="utf-8") as f:
                    entry = json.load(f)
                entry["_dir"] = os.path.join(self.root, name)
                entries[entry["word"].lower()] = entry
            except (OSError, ValueError, KeyError):
                continue
        self.entries = entries

    def _save(self, entry):
        data = {k: v for k, v in entry.items() if not k.startswith("_")}
        tmp = os.path.join(entry["_dir"], "entry.json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=1, ensure_ascii=False)
        os.replace(tmp, os.path.join(entry["_dir"], "entry.json"))

    def _entry(self, word):
        key = word.strip().lower()
        if key not in self.entries:
            base = slug(word)
            folder, n = os.path.join(self.root, base), 2
            while os.path.exists(folder):
                folder, n = os.path.join(self.root, "%s-%d" % (base, n)), n + 1
            os.makedirs(folder)
            self.entries[key] = {"word": word.strip(), "created": time.time(), "takes": [],
                                 "disabled": [], "_dir": folder}
        return self.entries[key]

    # ------------------------------------------------------------------ teaching
    def add_take(self, word, samples, heard, is_common=lambda phrase: False):
        """Store one recording of ``word`` and what the engine heard. Returns the entry."""
        with self._lock:
            entry = self._entry(word)
            n = 1 + max([t["n"] for t in entry["takes"]] or [0])
            audio.save_wav(os.path.join(entry["_dir"], "take-%d.wav" % n), samples)
            entry["takes"].append({"n": n, "heard": heard.strip(), "at": time.time()})
            h = variant_for(heard, entry["word"])
            if h and is_common(h) and h not in entry["disabled"]:
                entry["disabled"].append(h)  # common words: hint only, unless you turn it on
            self._save(entry)
            return entry

    def recheck(self, word, hear):
        """Re-transcribe every stored take with ``hear(samples) -> text`` (e.g. after a model change)."""
        with self._lock:
            entry = self.entries.get(word.lower())
            if not entry:
                return None
            for take in entry["takes"]:
                path = os.path.join(entry["_dir"], "take-%d.wav" % take["n"])
                if os.path.exists(path):
                    take["heard"] = hear(audio.load_wav(path)).strip()
            self._save(entry)
            return entry

    def take_path(self, word, n):
        entry = self.entries.get(word.lower())
        return os.path.join(entry["_dir"], "take-%d.wav" % n) if entry else None

    def delete_take(self, word, n):
        with self._lock:
            entry = self.entries.get(word.lower())
            if not entry:
                return
            entry["takes"] = [t for t in entry["takes"] if t["n"] != n]
            try:
                os.remove(os.path.join(entry["_dir"], "take-%d.wav" % n))
            except OSError:
                pass
            self._save(entry)

    def delete(self, word):
        with self._lock:
            entry = self.entries.pop(word.lower(), None)
            if entry:
                shutil.rmtree(entry["_dir"], ignore_errors=True)

    def set_variant(self, word, heard, enabled):
        with self._lock:
            entry = self.entries.get(word.lower())
            if not entry:
                return
            h = normalize(heard)
            disabled = [d for d in entry["disabled"] if d != h]
            if not enabled:
                disabled.append(h)
            entry["disabled"] = disabled
            self._save(entry)

    # ------------------------------------------------------------------ applying
    def variants(self, entry):
        """{normalized hearing: enabled} for every hearing that differs from the word."""
        out = {}
        for take in entry["takes"]:
            h = variant_for(take["heard"], entry["word"])
            if h:
                out[h] = h not in entry["disabled"]
        return out

    def vocabulary(self):
        return [e["word"] for e in self.entries.values()]

    def replacements(self):
        reps = {}
        for entry in self.entries.values():
            for heard, enabled in self.variants(entry).items():
                if enabled:
                    reps[heard] = entry["word"]
                    if "'" in heard:  # "cabernet's" also covers "cabernets"
                        reps[heard.replace("'", "")] = entry["word"]
        return reps

    def apply_to(self, cfg):
        """cfg with the dictionary merged in. Your typed Vocabulary/Replacements still win."""
        merged = dict(cfg)
        vocab = list(cfg.get("vocabulary") or [])
        lower = {v.lower() for v in vocab}
        merged["vocabulary"] = vocab + [w for w in self.vocabulary() if w.lower() not in lower]
        reps = self.replacements()
        reps.update(cfg.get("replacements") or {})
        merged["replacements"] = reps
        return merged
