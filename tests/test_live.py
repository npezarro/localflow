import numpy as np

from localflow.live import LiveSession

SR = 16000
WORDS = [(0.2, 0.5, " Um,"), (0.6, 0.9, " so"), (1.0, 1.4, " we"), (1.5, 1.9, " ship"), (2.0, 2.4, " it"),
         (2.5, 2.9, " on"), (3.0, 3.6, " Tuesday.")]


def fake_model(noise_at_end=True):
    """Transcribes whatever audio it's given as the scripted words that fit, plus an
    unstable guess for the word being spoken right at the edge."""
    calls = []

    def transcribe(samples, language, prompt):
        dur = len(samples) / SR
        offset = fake.offset
        visible = [(s - offset, e - offset, w) for s, e, w in WORDS if e <= offset + dur and s >= offset - 0.01]
        if noise_at_end and fake.noise:
            visible.append((dur - 0.2, dur, " uhh%d" % len(calls)))  # edge guess changes every pass
        calls.append((round(offset, 2), round(dur, 2), prompt))
        return visible

    fake = transcribe
    fake.offset = 0.0
    fake.noise = True
    fake.calls = calls
    return fake


def run(session, model, until, step=0.5):
    typed, t = [], step
    while t <= until:
        model.offset = session.buffer_start
        piece = session.update(np.zeros(int(t * SR), dtype=np.float32))
        if piece:
            typed.append(piece)
        t += step
    model.offset = session.buffer_start
    model.noise = False  # the final pass sees the complete audio, no half-spoken word at the edge
    typed.append(session.finish(np.zeros(int((until + 0.3) * SR), dtype=np.float32)))
    return typed


def cfg(**kw):
    base = {"remove_fillers": True, "trailing_space": True, "replacements": {}, "vocabulary": [], "language": "en"}
    base.update(kw)
    return base


def test_commits_only_agreed_words_and_finishes_the_rest():
    model = fake_model()
    s = LiveSession(model, cfg())
    pieces = run(s, model, 4.0)
    assert "".join(pieces) == "So we ship it on Tuesday. "
    assert all("uhh" not in p for p in pieces)  # unstable edge guesses are never typed
    assert len([p for p in pieces if p.strip()]) >= 2  # text arrived before the end, not all at once
    assert s.typed == "So we ship it on Tuesday. "


def test_keeps_fillers_when_disabled_and_applies_replacements():
    model = fake_model(noise_at_end=False)
    s = LiveSession(model, cfg(remove_fillers=False, replacements={"Tuesday": "Wednesday"}, trailing_space=False))
    assert "".join(run(s, model, 4.0)) == "Um, so we ship it on Wednesday."


def test_short_audio_types_nothing_until_finish():
    model = fake_model(noise_at_end=False)
    s = LiveSession(model, cfg())
    assert s.update(np.zeros(int(0.5 * SR), dtype=np.float32)) == ""
    model.offset = 0.0
    assert s.finish(np.zeros(int(4 * SR), dtype=np.float32)) == "So we ship it on Tuesday. "


def test_prompt_never_repeats_words_still_in_the_audio_buffer():
    model = fake_model()
    s = LiveSession(model, cfg(vocabulary=["Kubernetes"]), max_buffer=1.5)
    run(s, model, 4.0)
    for offset, _dur, prompt in model.calls:
        if offset == 0.0:
            assert prompt in (None, "Vocabulary: Kubernetes.")


def test_windows_unicode_events_handle_surrogates_and_newlines():
    import sys

    if sys.platform != "win32":
        import pytest

        pytest.skip("SendInput structures are Windows-only")
    from localflow import typer

    events = typer._win_text_events("a😀\n")
    assert len(events) == 2 + 4 + 2  # 'a' down/up, surrogate pair = 2 units x down/up, Enter down/up


def test_new_sentence_after_a_pause_is_capitalised():
    s = LiveSession(None, cfg(trailing_space=False))
    assert s._commit([(0, 1, " Americans.")]) == "Americans."
    assert s._commit([(2, 3, " ask"), (3, 4, " not")]) == " Ask not"
