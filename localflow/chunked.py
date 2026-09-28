"""Chunked transcription: no single Whisper call ever sees more than ``MAX_LEN`` seconds.

Two reasons:
1. Reliability. Whisper works in 30-second windows and, when it stops writing early in
   a window, it has to work out where to resume; any slip there silently drops speech
   (LocalFlow shipped exactly that bug: 38 of 94 words lost in a 32 s dictation). A
   piece shorter than one window can't hit that failure at all, however long you talk.
2. Speed. While you talk, finished pieces are transcribed in the background, so when you
   let go only the last piece is left, whatever the total length.

Pieces are cut at natural pauses (silence, so no word is split). If you talk for
``MAX_LEN`` seconds without a pause, the cut goes at the quietest moment instead.
Each piece is given the text before it as context, so the pieces read as one transcript.
Audio no longer than ``MAX_LEN`` is a single ordinary pass.
"""
import logging
import time

import numpy as np

from . import audio

log = logging.getLogger(__name__)

SR = audio.SAMPLE_RATE
MIN_LEN = 8.0    # don't cut pieces shorter than this (short pieces lose context)
MAX_LEN = 20.0   # never hand Whisper more than this in one call (one window is 30 s)
KEEP_AFTER = 1.0  # while recording, leave the last second alone: the speaker may be mid-word


def pick_cut(speech, start, end, min_chunk=MIN_LEN, min_gap=0.35, keep_after=KEEP_AFTER, max_len=None):
    """Middle of the LAST pause of at least ``min_gap`` s that begins ``min_chunk`` s after
    ``start``, ends ``keep_after`` s before ``end``, and (if ``max_len``) starts within
    ``max_len`` s of ``start``. ``speech`` regions are relative to ``start``. None if no pause fits."""
    best = None
    for (s1, e1), (s2, _e2) in zip(speech, speech[1:]):
        gap_start, gap_end = start + e1, start + s2
        if (gap_end - gap_start >= min_gap and gap_start - start >= min_chunk
                and gap_end <= end - keep_after and (max_len is None or gap_start - start <= max_len)):
            best = (gap_start + gap_end) / 2
    return best


def quietest_point(samples, lo, hi, frame=0.02, smooth=0.2):
    """Time (s, relative to ``samples``) of the quietest ``smooth``-second stretch in [lo, hi]."""
    a, b = int(lo * SR), int(hi * SR)
    seg = samples[a:b]
    n = int(frame * SR)
    if len(seg) < n * 2:
        return (lo + hi) / 2
    frames = len(seg) // n
    rms = np.sqrt(np.mean(seg[:frames * n].reshape(frames, n) ** 2, axis=1))
    k = max(1, int(smooth / frame))
    smoothed = np.convolve(rms, np.ones(k) / k, mode="same")
    return lo + (int(np.argmin(smoothed)) + 0.5) * frame


def next_cut(samples, start, end, regions, closed, max_len=MAX_LEN):
    """Where to end the piece that starts at ``start`` (seconds; ``samples`` begins at
    ``start``). ``closed``: the recording is over (no need to leave room at the end).
    Returns None when no cut is needed yet."""
    keep = 0.0 if closed else KEEP_AFTER
    if end - start <= (max_len if closed else MIN_LEN + 1.5):
        return None
    cut = pick_cut(regions, start, end, keep_after=keep, max_len=max_len)
    if cut is not None:
        return cut
    if end - start - keep > max_len:  # a long stretch with no usable pause
        return start + quietest_point(samples, MIN_LEN, max_len)
    return None


def transcribe_in_chunks(samples, transcribe, speech_regions, context="", max_len=MAX_LEN):
    """Transcribe a whole recording piece by piece. ``transcribe(samples, context) -> text``."""
    parts, start, total = [], 0.0, len(samples) / SR
    while True:
        rest = samples[int(start * SR):]
        long = total - start > max_len
        cut = next_cut(rest, start, total, speech_regions(rest) if long else [], closed=True, max_len=max_len)
        piece = rest if cut is None else rest[:int((cut - start) * SR)]
        if len(piece) >= SR * 0.3:
            ctx = " ".join(([context] if context else []) + parts)[-400:]
            text = transcribe(piece, ctx).strip()
            if text:
                parts.append(text)
        if cut is None:
            return " ".join(parts).strip()
        start = cut


class ChunkedTranscription:
    """Transcribe while recording: feed the growing recording to ``update``; call ``finish`` at the end."""

    def __init__(self, transcribe, speech_regions, min_chunk=MIN_LEN, max_len=MAX_LEN):
        # transcribe(samples, context_text) -> text ; speech_regions(samples) -> [(s, e) seconds]
        self.transcribe = transcribe
        self.speech_regions = speech_regions
        self.min_chunk = min_chunk
        self.max_len = max_len
        self.cut = 0.0  # seconds of the recording already transcribed
        self.parts = []
        self.forced = 0  # cuts made at a quiet point because there was no pause

    @property
    def context(self):
        return " ".join(self.parts)[-400:]

    def update(self, samples, offset=0.0):
        """Transcribe the next finished piece if there is one. Returns True if it did."""
        end = offset + len(samples) / SR
        if end - self.cut < self.min_chunk + 1.5:
            return False
        chunk = samples[int((self.cut - offset) * SR):]
        regions = self.speech_regions(chunk)
        cut = next_cut(chunk, self.cut, end, regions, closed=False, max_len=self.max_len)
        if cut is None:
            return False
        if pick_cut(regions, self.cut, end, max_len=self.max_len) is None:
            self.forced += 1
        piece = chunk[:int((cut - self.cut) * SR)]
        t0 = time.time()
        text = self.transcribe(piece, self.context).strip()
        log.info("chunk %.1f-%.1fs of %.1fs heard, transcribed in %.2fs", self.cut, cut, end, time.time() - t0)
        if text:
            self.parts.append(text)
        self.cut = cut
        return True

    def finish(self, samples, offset=0.0):
        """Transcribe what's left since the last cut (in pieces if it's long); returns the whole text."""
        rest = samples[int((self.cut - offset) * SR):]
        if len(rest) >= SR * 0.3:
            text = transcribe_in_chunks(rest, self.transcribe, self.speech_regions, self.context, self.max_len)
            if text:
                self.parts.append(text)
        return " ".join(self.parts).strip()

    @property
    def chunks_done(self):
        return len(self.parts)
