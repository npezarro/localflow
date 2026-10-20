"""NVIDIA Parakeet models, run by sherpa-onnx on the CPU.

Parakeet is a transducer, not Whisper: on our benchmark (docs/MODELS.md) it is more accurate
than large-v3-turbo and several times faster than small.en on a CPU, with no GPU needed.
It has no text prompt, so the vocabulary list doesn't steer it; your replacements and
learned corrections still apply afterwards. Models download once from Hugging Face into
data/models/sherpa/<name>/ and only count as downloaded once every file arrived whole
(marker file).
"""
import hashlib
import logging
import os
import time
import urllib.request

import numpy as np

from . import paths

log = logging.getLogger(__name__)

# Our model names -> int8 ONNX exports on Hugging Face (sherpa-onnx), pinned to a revision,
# with each file's size and (for the weights) SHA-256 so a bad download never gets loaded.
MODELS = {
    "parakeet-v2": {
        "repo": "csukuangfj/sherpa-onnx-nemo-parakeet-tdt-0.6b-v2-int8",
        "rev": "1ab9323565ddb038682214b292f588070a538ce2",
        "files": {
            "encoder.int8.onnx": (652184296, "a32b12d17bbbc309d0686fbbcc2987b5e9b8333a7da83fa6b089f0a2acd651ab"),
            "decoder.int8.onnx": (7257753, "b6bb64963457237b900e496ee9994b59294526439fbcc1fecf705b31a15c6b4e"),
            "joiner.int8.onnx": (1739080, "7946164367946e7f9f29a122407c3252b680dbae9a51343eb2488d057c3c43d2"),
            "tokens.txt": (9384, None),
        },
    },
    "parakeet-v3": {  # 25 European languages; picks the language itself
        "repo": "csukuangfj/sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8",
        "rev": "2bda32ec70b097a55adaa07d9a7173915b43cc78",
        "files": {
            "encoder.int8.onnx": (652184281, "acfc2b4456377e15d04f0243af540b7fe7c992f8d898d751cf134c3a55fd2247"),
            "decoder.int8.onnx": (11845275, "179e50c43d1a9de79c8a24149a2f9bac6eb5981823f2a2ed88d655b24248db4e"),
            "joiner.int8.onnx": (6355277, "3164c13fc2821009440d20fcb5fdc78bff28b4db2f8d0f0b329101719c0948b3"),
            "tokens.txt": (93939, None),
        },
    },
}
DONE_MARKER = ".localflow-complete"


def supports(name):
    return name in MODELS


def size_mb(name):
    return sum(size for size, _sha in MODELS[name]["files"].values()) / 1e6


def folder(name):
    return os.path.join(paths.models_dir(), "sherpa", name)


def is_local(name):
    return os.path.isfile(os.path.join(folder(name), DONE_MARKER))


def model_path(name, progress=lambda msg, frac: None):
    """Local folder with ``name``, downloading it the first time (resumes nothing: a file that
    isn't whole is fetched again)."""
    spec = MODELS[name]
    path = folder(name)
    if is_local(name):
        return path
    os.makedirs(path, exist_ok=True)
    total = sum(size for size, _sha in spec["files"].values())
    done, t0 = 0, time.time()
    for fname, (size, sha) in spec["files"].items():
        dest = os.path.join(path, fname)
        if os.path.isfile(dest) and os.path.getsize(dest) == size and (sha is None or _sha256(dest) == sha):
            done += size
            continue
        url = "https://huggingface.co/%s/resolve/%s/%s" % (spec["repo"], spec["rev"], fname)
        digest = hashlib.sha256()
        with urllib.request.urlopen(url, timeout=60) as resp, open(dest + ".part", "wb") as out:
            while True:
                block = resp.read(1 << 20)
                if not block:
                    break
                out.write(block)
                digest.update(block)
                done += len(block)
                rate = done / 1e6 / max(time.time() - t0, 0.1)
                progress("Downloading %s: %d of %d MB (%.0f MB/s)" % (name, done / 1e6, total / 1e6, rate), done / total)
        if os.path.getsize(dest + ".part") != size or (sha and digest.hexdigest() != sha):
            os.remove(dest + ".part")
            raise RuntimeError("the %s download was damaged (%s); try again" % (name, fname))
        os.replace(dest + ".part", dest)
    open(os.path.join(path, DONE_MARKER), "w").close()
    return path


def _sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


class SherpaModel:
    def __init__(self, name, threads, progress=None):
        # onnxruntime first: on Windows a DLL loaded by name is shared, and the onnxruntime
        # package (Silero VAD) needs its own, newer build; sherpa-onnx works with either.
        import onnxruntime  # noqa: F401
        import sherpa_onnx

        path = model_path(name, progress or (lambda msg, frac: None))
        p = lambda f: os.path.join(path, f)  # noqa: E731
        self.name = name
        self.recognizer = sherpa_onnx.OfflineRecognizer.from_transducer(
            p("encoder.int8.onnx"), p("decoder.int8.onnx"), p("joiner.int8.onnx"), p("tokens.txt"),
            num_threads=threads, model_type="nemo_transducer",
            # Greedy search: sherpa-onnx's beam search (needed for hotwords) returns empty or
            # made-up text for ~20% of Parakeet clips (k2-fsa/sherpa-onnx#3267). A whisper of
            # dither avoids the empty results that exact-zero dither gives (#2258).
            decoding_method="greedy_search", dither=0.00003)

    def _decode(self, audio):
        stream = self.recognizer.create_stream()
        stream.accept_waveform(16000, np.ascontiguousarray(audio, dtype=np.float32))
        self.recognizer.decode_stream(stream)
        return stream.result

    def transcribe(self, audio):
        return self._decode(audio).text.strip()

    def words(self, audio):
        """[(start_s, end_s, " word", probability)] from the token timestamps."""
        result = self._decode(audio)
        tokens, starts = list(result.tokens), list(result.timestamps)
        durations = list(result.durations) or [0.08] * len(tokens)
        logp = list(result.ys_log_probs) or [0.0] * len(tokens)
        words = []
        for tok, start, dur, lp in zip(tokens, starts, durations, logp):
            if tok.startswith(" ") or not words:  # a leading space starts a word; "," joins the last one
                words.append([start, start + max(dur, 0.04), " " + tok.strip(), lp])
            else:
                words[-1][1] = start + max(dur, 0.04)
                words[-1][2] += tok
                words[-1][3] = min(words[-1][3], lp)  # a word is as sure as its least sure piece
        return [(s, e, w, float(np.exp(lp))) for s, e, w, lp in words if w.strip()]
