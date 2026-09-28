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
