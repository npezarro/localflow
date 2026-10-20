import hashlib
import io
import types

import numpy as np
import pytest

from localflow import sherpa_engine


class FakeRecognizer:
    def __init__(self, result):
        self.result = result

    def create_stream(self):
        return types.SimpleNamespace(accept_waveform=lambda sr, a: None, result=self.result)

    def decode_stream(self, stream):
        pass


def model_with(**result):
    m = sherpa_engine.SherpaModel.__new__(sherpa_engine.SherpaModel)
    m.recognizer = FakeRecognizer(types.SimpleNamespace(**result))
    return m


def test_words_join_pieces_and_punctuation():
    m = model_with(text="And so, my fellow", tokens=[" And", " so", ",", " my", " fe", "ll", "ow"],
                   timestamps=[0.2, 0.5, 0.8, 0.9, 1.2, 1.3, 1.5], durations=[0.1] * 7,
                   ys_log_probs=[0.0, 0.0, 0.0, 0.0, -0.1, -2.0, 0.0])
    words = m.words(np.zeros(16000, dtype=np.float32))
    assert [w[2] for w in words] == [" And", " so,", " my", " fellow"]
    assert words[1][:2] == (0.5, pytest.approx(0.9))  # the comma's end closes "so,"
    assert words[3][3] == pytest.approx(np.exp(-2.0))  # least sure piece


def test_words_without_durations_or_probs():
    m = model_with(text="hi", tokens=[" hi"], timestamps=[0.3], durations=[], ys_log_probs=[])
    assert m.words(np.zeros(1600, dtype=np.float32)) == [(0.3, pytest.approx(0.38), " hi", 1.0)]


def fake_spec(monkeypatch, tmp_path, payload, sha):
    monkeypatch.setattr(sherpa_engine.paths, "_data_dir", str(tmp_path))
    monkeypatch.setitem(sherpa_engine.MODELS, "fake", {"repo": "x/y", "rev": "abc",
                                                       "files": {"m.onnx": (len(payload), sha)}})
    calls = []

    def urlopen(url, timeout):
        calls.append(url)
        return io.BytesIO(payload)

    monkeypatch.setattr(sherpa_engine.urllib.request, "urlopen", urlopen)
    return calls


def test_download_checks_hash_and_marks_complete(monkeypatch, tmp_path):
    payload = b"weights" * 1000
    calls = fake_spec(monkeypatch, tmp_path, payload, hashlib.sha256(payload).hexdigest())
    seen = []
    assert not sherpa_engine.is_local("fake")
    path = sherpa_engine.model_path("fake", lambda msg, frac: seen.append(frac))
    assert open(path + "/m.onnx", "rb").read() == payload
    assert sherpa_engine.is_local("fake") and seen[-1] == 1.0
    assert calls == ["https://huggingface.co/x/y/resolve/abc/m.onnx"]
    sherpa_engine.model_path("fake")
    assert len(calls) == 1  # already there: no second download


def test_damaged_download_is_not_used(monkeypatch, tmp_path):
    fake_spec(monkeypatch, tmp_path, b"truncated", "0" * 64)
    with pytest.raises(RuntimeError, match="damaged"):
        sherpa_engine.model_path("fake")
    assert not sherpa_engine.is_local("fake")
