import numpy as np

from localflow.chunked import ChunkedTranscription, pick_cut

SR = 16000


def test_pick_cut_uses_last_long_enough_pause_after_min_chunk():
    speech = [(0.0, 3.0), (3.2, 9.0), (9.6, 14.0), (14.5, 19.0)]  # pauses: 0.2 s @3, 0.6 s @9, 0.5 s @14
    assert pick_cut(speech, 0.0, 20.0) == 14.25  # the last pause that fits
    assert pick_cut(speech, 0.0, 15.0) == 9.3  # 14-14.5 ends too close to "now"
    assert pick_cut(speech, 0.0, 9.5) is None  # nothing after 8 s of speech yet
    assert pick_cut([(0.0, 20.0)], 0.0, 22.0) is None  # no pause at all: never cut mid-speech
    assert pick_cut(speech, 5.0, 25.0) == 5.0 + 14.25  # regions are relative to start


class Fake:
    def __init__(self):
        self.calls = []

    def transcribe(self, samples, context):
        self.calls.append((round(len(samples) / SR, 2), context))
        return "part%d" % len(self.calls)

    def regions(self, samples):
        # speech with a 0.6 s pause every 5 s: [0,4.7] [5.3,9.7] [10.3,14.7] ...
        dur = len(samples) / SR
        out, t = [], 0.0
        while t < dur:
            out.append((t + (0.3 if t else 0.0), min(dur, t + 4.7)))
            t += 5.0
        return out


def test_long_dictation_is_cut_at_pauses_with_context_and_short_one_is_one_pass():
    f = Fake()
    ch = ChunkedTranscription(f.transcribe, f.regions)
    audio = np.zeros(SR * 23, dtype=np.float32)
    for t in np.arange(1.0, 23.0, 0.5):
        ch.update(audio[:int(t * SR)])
    text = ch.finish(audio)
    assert ch.chunks_done >= 3
    assert text == " ".join("part%d" % (i + 1) for i in range(len(f.calls)))
    assert f.calls[0][1] == "" and f.calls[1][1] == "part1"  # each chunk gets the text before it
    assert abs(sum(c[0] for c in f.calls) - 23.0) < 0.05  # every second transcribed exactly once

    f2 = Fake()
    short = ChunkedTranscription(f2.transcribe, f2.regions)
    clip = np.zeros(SR * 7, dtype=np.float32)
    for t in np.arange(1.0, 7.0, 0.5):
        short.update(clip[:int(t * SR)])
    assert short.finish(clip) == "part1" and f2.calls == [(7.0, "")]  # unchanged behaviour


def test_no_piece_exceeds_max_len_even_without_pauses():
    from localflow.chunked import MAX_LEN, transcribe_in_chunks

    lengths = []

    def tr(samples, ctx):
        lengths.append(len(samples) / SR)
        return "w%d" % len(lengths)

    # 65 s of continuous "speech" with one quiet dip at 15 s and one at 34 s: no real pauses.
    rng = np.random.RandomState(0)
    a = (rng.randn(SR * 65) * 0.2).astype(np.float32)
    for t in (15.0, 34.0):
        a[int(t * SR):int((t + 0.2) * SR)] *= 0.01
    text = transcribe_in_chunks(a, tr, lambda s: [(0.0, len(s) / SR)])
    assert all(n <= MAX_LEN + 0.01 for n in lengths), lengths
    assert abs(sum(lengths) - 65.0) < 0.05  # every second exactly once
    assert text == " ".join("w%d" % (i + 1) for i in range(len(lengths)))
    assert abs(lengths[0] - 15.1) < 0.2  # the forced cut landed in the quiet dip


def test_short_audio_is_one_pass_and_pauses_are_preferred():
    from localflow.chunked import transcribe_in_chunks

    calls = []
    short = np.zeros(SR * 18, dtype=np.float32)
    transcribe_in_chunks(short, lambda s, c: calls.append(len(s) / SR) or "x", lambda s: [])
    assert calls == [18.0]
    calls.clear()
    long = np.zeros(SR * 40, dtype=np.float32)
    regions = lambda s: [(0.0, 11.0), (11.6, 17.0), (17.5, len(s) / SR)]  # noqa: E731
    transcribe_in_chunks(long, lambda s, c: calls.append(round(len(s) / SR, 2)) or "x", regions)
    assert calls[0] == 17.25  # last pause within 20 s, not a forced cut


def test_background_chunker_forces_cuts_when_speaker_never_pauses():
    from localflow.chunked import MAX_LEN, ChunkedTranscription

    lengths = []
    ch = ChunkedTranscription(lambda s, c: lengths.append(len(s) / SR) or "p", lambda s: [(0.0, len(s) / SR)])
    a = (np.random.RandomState(1).randn(SR * 50) * 0.2).astype(np.float32)
    for t in np.arange(1.0, 50.0, 0.5):
        ch.update(a[:int(t * SR)])
    assert ch.forced >= 2 and all(n <= MAX_LEN + 0.01 for n in lengths)
    ch.finish(a)
    assert all(n <= MAX_LEN + 0.01 for n in lengths) and abs(sum(lengths) - 50.0) < 0.05
