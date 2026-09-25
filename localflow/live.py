"""Live typing: turn a growing recording into text that is safe to type immediately.

Whisper isn't a streaming model, so every ~second we re-transcribe the part of the
recording that isn't final yet and only *commit* the words two consecutive passes
agree on (the "local agreement" policy from whisper_streaming). Committed text is
never revised, so it can be typed straight into another app without backspacing.
When dictation stops, the rest is transcribed once more and committed in full.
"""
import re

from . import audio, textproc

SR = audio.SAMPLE_RATE
_FILLER = re.compile(r"(?i)^\W*(?:%s)\W*$" % textproc.FILLERS)


def _norm(word):
    return re.sub(r"[^\w']", "", word.lower())


class LiveSession:
    def __init__(self, transcribe_words, cfg, min_audio=1.0, max_buffer=12.0):
        self.transcribe_words = transcribe_words  # fn(samples, language, prompt) -> [(start, end, word)]
        self.cfg = cfg
        self.min_audio = min_audio
        self.max_buffer = max_buffer
        self.buffer_start = 0.0  # seconds into the recording where the working buffer begins
        self.committed = []  # [(start, end, word)] in recording time
        self.committed_end = 0.0
        self.previous = []  # last pass's uncommitted words
        self.typed_any = False
        self.typed = ""  # exactly what has been handed out for typing
        self._cap_next = False

    # ------------------------------------------------------------------ helpers
    def _prompt(self):
        parts = []
        vocab = self.cfg.get("vocabulary")
        if vocab:
            parts.append("Vocabulary: " + ", ".join(vocab) + ".")
        # Only text whose audio is no longer in the buffer: if the prompt repeats words that
        # are still in the audio, Whisper skips them and every timestamp shifts.
        tail = "".join(w for _s, e, w in self.committed if e <= self.buffer_start)[-200:].strip()
        if tail:
            parts.append(tail)
        return " ".join(parts) or None

    def _hypothesis(self, samples):
        start = int(self.buffer_start * SR)
        chunk = samples[start:]
        if len(chunk) < SR * 0.3:
            return []
        words = self.transcribe_words(audio.normalize(chunk), self.cfg.get("language"), self._prompt())
        words = [(s + self.buffer_start, e + self.buffer_start, w) for s, e, w in words]
        # Drop what was already committed: by time, then any repeated n-gram at the seam.
        # Timestamps jitter between passes, so filter on the word's END and let the n-gram
        # check below remove a committed word that straddles the seam.
        words = [w for w in words if w[1] > self.committed_end - 0.5]
        tail = [_norm(w) for _s, e, w in self.committed if e > self.buffer_start][-8:]
        for k in range(min(len(tail), len(words)), 0, -1):
            if [_norm(w) for _s, _e, w in words[:k]] == tail[-k:]:
                words = words[k:]
                break
        return words

    def _format(self, words):
        """Committed words -> text to type (filler removal, spacing, replacements)."""
        out = []
        for _s, _e, word in words:
            if self.cfg.get("remove_fillers") and _FILLER.match(word):
                if word.strip()[:1].isupper() or not self.typed_any and not out:
                    self._cap_next = True
                continue
            if self._cap_next:
                stripped = word.lstrip()
                word = word[:len(word) - len(stripped)] + stripped[:1].upper() + stripped[1:]
                self._cap_next = False
            out.append(word)
        text = "".join(out)
        if not self.typed_any:
            text = text.lstrip()
        if text:
            text = textproc.apply_replacements(text, self.cfg.get("replacements"))
            self.typed_any = True
        return text

    def _commit(self, words):
        if not words:
            return ""
        self.committed.extend(words)
        self.committed_end = words[-1][1]
        text = self._format(words)
        self.typed += text
        return text

    # ------------------------------------------------------------------ public API
    def update(self, samples):
        """Feed the whole recording so far; returns newly committed text ('' if none)."""
        if len(samples) < SR * self.min_audio:
            return ""
        hyp = self._hypothesis(samples)
        agreed = []
        for new, old in zip(hyp, self.previous):
            if _norm(new[2]) != _norm(old[2]) or not _norm(new[2]):
                break
            agreed.append(new)
        self.previous = hyp[len(agreed):]
        text = self._commit(agreed)
        # Keep the working buffer short so each pass stays fast.
        if len(samples) / SR - self.buffer_start > self.max_buffer and self.committed_end > self.buffer_start:
            self.buffer_start = self.committed_end
        return text

    def finish(self, samples):
        """Recording over: commit everything that's left. Returns the remaining text."""
        text = self._commit(self._hypothesis(samples))
        if self.typed_any and self.cfg.get("trailing_space"):
            text += " "
            self.typed += " "
        return text
