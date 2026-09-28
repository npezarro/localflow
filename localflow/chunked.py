"""Transcribe while you talk, so less is left to do when you let go of the hotkey.

Whisper pads every input to a 30-second window, so a pass costs roughly the same
whether it covers 2 s or 25 s of speech. The only way to shorten the wait after a long
dictation is to have most of it done already. While you talk, each time there is a
natural pause after at least ``min_chunk`` seconds, the finished stretch is transcribed
in the background. Cuts happen only in silence, so no word is ever split, and each chunk
is given the previous chunks' text as context so the pieces read as one transcript.
When you stop, only the audio since the last cut remains. A dictation shorter than
``min_chunk`` never gets cut: it is one normal full pass, exactly as before.
"""
import logging
import time

from . import audio

log = logging.getLogger(__name__)

SR = audio.SAMPLE_RATE


def pick_cut(speech, start, end, min_chunk=8.0, min_gap=0.35, keep_after=1.0):
    """Choose where to cut between ``start`` and ``end`` (seconds), given speech regions
    [(s, e), ...] relative to ``start``. Returns the cut time or None.
    The cut goes in the middle of the LAST pause of at least ``min_gap`` seconds that
    begins after ``min_chunk`` seconds and ends at least ``keep_after`` seconds before
    ``end`` (the speaker may still be mid-word at the very end)."""
    best = None
    for (s1, e1), (s2, _e2) in zip(speech, speech[1:]):
        gap_start, gap_end = start + e1, start + s2
        if gap_end - gap_start >= min_gap and gap_start - start >= min_chunk and gap_end <= end - keep_after:
            best = (gap_start + gap_end) / 2
    return best


class ChunkedTranscription:
    """Feed it the growing recording with ``update``; call ``finish`` when it ends."""

    def __init__(self, transcribe, speech_regions, min_chunk=8.0):
        # transcribe(samples, context_text) -> text ; speech_regions(samples) -> [(s, e) seconds]
        self.transcribe = transcribe
        self.speech_regions = speech_regions
        self.min_chunk = min_chunk
        self.cut = 0.0  # seconds of the recording already transcribed
        self.parts = []

    @property
    def context(self):
        return " ".join(self.parts)[-400:]

    def update(self, samples, offset=0.0):
        """Transcribe the next finished stretch if there is one. Returns True if it did."""
        end = offset + len(samples) / SR
        if end - self.cut < self.min_chunk + 1.5:
            return False
        chunk = samples[int((self.cut - offset) * SR):]
        cut = pick_cut(self.speech_regions(chunk), self.cut, end, self.min_chunk)
        if cut is None:
            return False
        piece = samples[int((self.cut - offset) * SR):int((cut - offset) * SR)]
        t0 = time.time()
        text = self.transcribe(piece, self.context).strip()
        log.info("chunk %.1f-%.1fs of %.1fs heard, transcribed in %.2fs", self.cut, cut, end, time.time() - t0)
        if text:
            self.parts.append(text)
        self.cut = cut
        return True

    def finish(self, samples, offset=0.0):
        """Transcribe what's left since the last cut; returns the whole text."""
        rest = samples[int((self.cut - offset) * SR):]
        if len(rest) >= SR * 0.3:
            text = self.transcribe(rest, self.context).strip()
            if text:
                self.parts.append(text)
        return " ".join(self.parts).strip()

    @property
    def chunks_done(self):
        return len(self.parts)
