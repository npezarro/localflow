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


def _drop_loops(words):
    """Cut a hallucination loop: a 1-3 word phrase repeated 3+ times in a row, or a 4-12
    word phrase repeated twice in a row (people don't immediately repeat whole clauses)."""
    norm = [_norm(w[2]) for w in words]
    for i in range(len(norm)):
        for n in range(1, 13):
            chunk = norm[i:i + n]
            if len(chunk) < n or not any(chunk):
                break
            repeats = 3 if n < 4 else 2
            if all(norm[i + k * n:i + (k + 1) * n] == chunk for k in range(1, repeats)):
                return words[:i + n]  # keep the first occurrence
    return words


class LiveSession:
    def __init__(self, transcribe_words, cfg, min_audio=1.0, max_buffer=12.0, speech_in=None):
        self.transcribe_words = transcribe_words  # fn(samples, language, prompt) -> [(start, end, word)]
        # Optional voice-activity check fn(samples) -> bool. With it, a pause flushes the
        # pending words right away and silence is skipped (continuous mode).
        self.speech_in = speech_in
        self.last_speech = 0.0  # recording time of the most recent speech heard
        self.last_flush = -1.0  # a pause is flushed once; silence after it is never transcribed
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

    def _hypothesis(self, samples, offset=0.0, trim_unsure=False):
        start = max(0, int((self.buffer_start - offset) * SR))
        chunk = samples[start:]
        if len(chunk) < SR * 0.3:
            return []
        raw = self.transcribe_words(audio.normalize(chunk), self.cfg.get("language"), self._prompt())
        if trim_unsure:  # Whisper pads trailing silence with a low-confidence "and", "you", ...
            while raw and len(raw[-1]) > 3 and raw[-1][3] < 0.35:
                raw = raw[:-1]
        raw = _drop_loops(raw)
        words = [(w[0] + self.buffer_start, w[1] + self.buffer_start, w[2]) for w in raw]
        # Drop what was already committed: by time, then any repeated n-gram at the seam.
        # Timestamps jitter between passes, so filter on the word's END and let the n-gram
        # check below remove a committed word that straddles the seam.
        words = [w for w in words if w[1] > self.committed_end - 0.5]
        # Compare with the most recent committed words by time, not by buffer: after a pause
        # the buffer restarts, and a word committed just before it can be heard again.
        tail = [_norm(w) for _s, e, w in self.committed if e > self.committed_end - 3.0][-8:]
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
            ends_sentence = (self.typed + "".join(out)).rstrip()[-1:] in ".!?"
            if self._cap_next or (ends_sentence and word.lstrip()[:1].islower()):
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
    def update(self, samples, offset=0.0):
        """Feed the recording so far (``samples`` begin ``offset`` seconds into it).
        Returns newly committed text ('' if none)."""
        total = offset + len(samples) / SR
        if total < self.min_audio:
            return ""
        if self.speech_in is not None:
            if self.speech_in(samples[-int(SR * 1.2):]):
                self.last_speech = total
            else:  # you've paused (>1.2 s: longer than a comma or a breath)
                start = max(0, int((self.buffer_start - offset) * SR))
                if self.last_speech > self.last_flush and (self.previous or self.speech_in(samples[start:])):
                    self.last_flush = total
                    return self.flush(samples, offset)  # finish the utterance now
                self.buffer_start = max(self.buffer_start, total - 0.5)  # skip pure silence
                return ""
        hyp = self._hypothesis(samples, offset)
        agreed = []
        for new, old in zip(hyp, self.previous):
            if _norm(new[2]) != _norm(old[2]) or not _norm(new[2]):
                break
            agreed.append(new)
        self.previous = hyp[len(agreed):]
        text = self._commit(agreed)
        # Keep the working buffer short so each pass stays fast.
        if total - self.buffer_start > self.max_buffer and self.committed_end > self.buffer_start:
            self.buffer_start = self.committed_end
        return text

    def flush(self, samples, offset=0.0):
        """End of an utterance: commit the words that finished before the pause began, then
        continue from shortly before 'now' (a little overlap so a word just starting isn't cut)."""
        total = offset + len(samples) / SR
        # The voice check confirmed the last 1.2 s are silent, so every real word has ended;
        # anything Whisper places in the final 0.3 s is padding. Restart inside the silence:
        # word timestamps are too loose to cut the audio at a word boundary.
        words = [w for w in self._hypothesis(samples, offset, trim_unsure=True) if w[1] <= total - 0.3]
        text = self._commit(words)
        self.previous = []
        self.buffer_start = max(self.buffer_start, total - 0.6)
        return text

    def finish(self, samples, offset=0.0):
        """Recording over: commit everything that's left. Returns the remaining text."""
        text = self._commit(self._hypothesis(samples, offset, trim_unsure=True))
        if self.typed_any and self.cfg.get("trailing_space"):
            text += " "
            self.typed += " "
        return text
