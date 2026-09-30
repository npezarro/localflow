import numpy as np

from localflow import apple_gpu, transcriber


def _f(name, size=1):
    return {"filename": name, "size": size, "url": "u/" + name, "digests": {"sha256": "x"}}


def test_picks_the_newest_wheel_this_mac_can_run():
    files = [_f("mlx-0.32.3-cp312-cp312-macosx_14_0_arm64.whl"), _f("mlx-0.32.3-cp312-cp312-macosx_15_0_arm64.whl"),
             _f("mlx-0.32.3-cp312-cp312-macosx_26_0_arm64.whl"), _f("mlx-0.32.3-cp311-cp311-macosx_15_0_arm64.whl"),
             _f("mlx-0.32.3-cp312-cp312-manylinux_2_35_x86_64.whl"), _f("mlx-0.32.3-cp312-cp312-win_amd64.whl")]
    import sys
    if sys.version_info[:2] != (3, 12):
        return  # the tags above are for the 3.12 the app ships with
    assert apple_gpu._pick_wheel(files, (15, 3))["filename"].endswith("macosx_15_0_arm64.whl")
    assert apple_gpu._pick_wheel(files, (14, 6))["filename"].endswith("macosx_14_0_arm64.whl")
    assert apple_gpu._pick_wheel(files, (13, 0)) is None  # MLX needs macOS 14
    assert apple_gpu._pick_wheel([_f("tqdm-4.70.1-py3-none-any.whl")], (14, 0))


def test_numpy_medfilt_matches_a_plain_median_filter():
    import sys

    saved = {k: sys.modules.pop(k, None) for k in ("scipy", "scipy.signal", "numba")}
    try:
        apple_gpu._install_shims()
        from scipy import signal

        import numba
        x = np.array([[[1, 9, 2, 8, 3, 7, 4]]], dtype=np.float32)
        out = signal.medfilt(x, kernel_size=(1, 1, 3))
        assert out.tolist() == [[[1, 2, 8, 3, 7, 4, 4]]]
        assert numba.jit(nopython=True)(len) is len and numba.jit(len) is len
    finally:
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v


def test_transcriber_routes_to_the_apple_gpu_and_keeps_only_speech(monkeypatch):
    calls = []

    class FakeMLX:
        def transcribe(self, audio, language=None, prompt=None, **kw):
            calls.append((len(audio), prompt, kw))
            if kw.get("word_timestamps"):
                return {"segments": [{"text": " hi there", "no_speech_prob": 0.1,
                                      "words": [{"start": 0.0, "end": 0.3, "word": " hi", "probability": 0.9}]}]}
            return {"segments": [{"text": " hi there ", "no_speech_prob": 0.1},
                                 {"text": " Thanks for watching!", "no_speech_prob": 0.9}]}

    monkeypatch.setattr(transcriber, "speech_regions", lambda a: [(1.0, 2.0)])
    t = transcriber.Transcriber()
    t.mlx = FakeMLX()
    audio = np.zeros(16000 * 5, dtype=np.float32)
    assert t.transcribe(audio, "en", ["Kubernetes"], 1) == "hi there"  # no-speech segment dropped
    n, prompt, _kw = calls[0]
    assert n == int(1.4 * 16000) and "Kubernetes" in prompt  # only the speech (+ padding) is sent
    assert t.transcribe_words(audio, "en") == [(0.0, 0.3, " hi", 0.9)]
    monkeypatch.setattr(transcriber, "speech_regions", lambda a: [])
    assert t.transcribe(audio, "en", None, 1) == ""  # silence: nothing sent, nothing invented
