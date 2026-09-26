import numpy as np

from localflow import apps, audio
from localflow.hotkey import win_vk_name
from localflow.live import LiveSession

SR = 16000
CHROME = {"name": "chrome.exe", "exe": "chrome.exe", "title": "Inbox", "id": "chrome.exe"}
SLACK_MAC = {"name": "Slack", "exe": "Slack", "title": "Slack", "id": "com.tinyspeck.slackmacgap"}


def test_app_rules():
    assert apps.allowed(CHROME, {"type_into": "all"})
    assert apps.allowed(None, {"type_into": "all"})
    only = {"type_into": "only", "app_list": ["Chrome", "slack"]}
    assert apps.allowed(CHROME, only) and apps.allowed(SLACK_MAC, only)
    assert not apps.allowed({"name": "notepad.exe", "exe": "notepad.exe", "id": "notepad.exe"}, only)
    assert not apps.allowed(None, only)  # unknown app: don't type when restricted to a list
    block = {"type_into": "except", "app_list": ["com.tinyspeck.slackmacgap"]}
    assert not apps.allowed(SLACK_MAC, block) and apps.allowed(CHROME, block) and apps.allowed(None, block)


class Script:
    """Fake model + VAD over a timeline: speech 0-2 s ("hello there"), silence 2-6 s,
    speech 6-8 s ("second part")."""
    WORDS = [(0.2, 0.8, " Hello"), (0.9, 1.6, " there."), (6.2, 6.9, " Second"), (7.0, 7.7, " part.")]

    def __init__(self):
        self.calls = 0

    def transcribe(self, samples, language, prompt):
        self.calls += 1
        start, dur = self.start, len(samples) / SR
        return [(s - start, e - start, w) for s, e, w in self.WORDS if s >= start - 0.01 and e <= start + dur]

    def speech(self, samples):
        # the fake audio encodes "speech" as non-zero samples
        return bool(np.any(samples != 0))


def timeline(until):
    t = np.zeros(int(until * SR), dtype=np.float32)
    for a, b in ((0.0, 2.0), (6.0, 8.0)):
        t[int(a * SR):int(min(b, until) * SR)] = 0.1
    return t


def test_pause_flushes_utterance_and_silence_is_skipped():
    sc = Script()
    s = LiveSession(None, {"trailing_space": True, "remove_fillers": True, "replacements": {}},
                    speech_in=sc.speech)
    s.transcribe_words = lambda a, lang, prompt: (setattr(sc, "start", s.buffer_start), sc.transcribe(a, lang, prompt))[1]
    typed = {}
    t = 1.0
    while t <= 11.0:
        piece = s.update(timeline(t))
        if piece:
            typed[t] = piece
        t += 1.0
    # "Hello" is agreed on while speaking; the rest is flushed on the first silent second.
    assert "".join(v for k, v in typed.items() if k <= 3.0) == "Hello there."
    assert typed.get(3.0, "").endswith("there.")
    calls_after_flush = sc.calls
    assert "Second part." in "".join(typed.values())  # the next utterance flushes the same way
    assert s.buffer_start >= 8.0  # silence and finished speech are dropped from the buffer
    assert sc.calls <= calls_after_flush + 3  # silent seconds cost no transcription
    assert s.last_speech >= 8.0


def test_recorder_trim_keeps_offsets_consistent():
    rec = audio.Recorder(warm=True, preroll=0.0, tail=0)
    rec._rate, rec._fake = SR, None
    rec._stream = type("S", (), {"active": True})()
    rec.start()
    for i in range(100):  # 3 s of 30 ms blocks, value = block index
        rec._callback(np.full((480, 1), i, dtype=np.float32), 480, None, None)
    rec.trim_before(1.0)
    samples, offset = rec.peek()
    assert abs(offset - 0.99) < 0.031 and abs(offset + len(samples) / SR - 3.0) < 1e-6
    assert samples[0] == int(round(offset / 0.03))  # the first kept block is the right one
    assert len(rec.stop(keep_tail=False)) == len(samples)


def test_win_vk_names():
    assert win_vk_name(0x20) == "space" and win_vk_name(0x4C) == "l" and win_vk_name(0x77) == "f8"
    assert win_vk_name(0xA2) is None  # modifiers are never swallowed


def test_hallucination_loops_are_cut():
    from localflow.live import _drop_loops

    words = [(0, 0, " " + w) for w in "and so we can make sure that we can make sure that we can make sure that".split()]
    assert " ".join(w[2].strip() for w in _drop_loops(words)) == "and so we can make sure that"
    normal = [(0, 0, " " + w) for w in "ask what you can do for your country".split()]
    assert _drop_loops(normal) == normal
    assert len(_drop_loops([(0, 0, " no"), (0, 0, " no")])) == 2  # a doubled word is fine


def test_repeated_clause_is_cut():
    from localflow.live import _drop_loops

    clause = "ask not what your country can do for you".split()
    words = [(0, 0, " " + w) for w in clause + clause + ["ask"]]
    assert [w[2].strip() for w in _drop_loops(words)] == clause


def test_data_folder_decides_portable_vs_installed(tmp_path, monkeypatch):
    from localflow import paths

    monkeypatch.delenv("LOCALFLOW_DATA_DIR", raising=False)
    monkeypatch.setattr(paths, "_user_data_dir", lambda: str(tmp_path / "user" / "LocalFlow"))
    portable, installed = tmp_path / "zip" / "LocalFlow", tmp_path / "installed"
    (portable / "data").mkdir(parents=True)
    installed.mkdir()
    for root, expected in ((portable, portable / "data"), (installed, None)):
        monkeypatch.setattr(paths, "_data_dir", None)
        monkeypatch.setattr(paths, "install_root", lambda r=root: str(r))
        got = paths.data_dir()
        if expected:
            assert got == str(expected) and paths.is_portable()
        else:
            assert got.startswith(str(tmp_path / "user")) and not paths.is_portable()
            assert not (installed / "data").exists()  # an installed copy never creates one
