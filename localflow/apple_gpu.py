"""Apple Silicon GPU support (MLX).

faster-whisper's engine (CTranslate2) has no Metal backend, so on a Mac it runs on the CPU.
MLX, Apple's machine-learning framework, runs Whisper on the Apple Silicon GPU instead.
"Download Apple GPU support" in Settings fetches MLX and mlx-whisper from PyPI (about 45 MB,
each file checked against PyPI's published SHA-256) into data/apple-gpu/, and models in MLX
format from Hugging Face (mlx-community) into data/models/mlx/. The app stays the same size
and nothing changes for Intel Macs or Windows.

mlx-whisper declares PyTorch, numba and scipy as dependencies, but transcription needs none
of them: PyTorch only converts models, and numba/scipy serve one word-timing helper, for
which we supply small numpy stand-ins (``_install_shims``).
"""
import hashlib
import io
import json
import logging
import os
import platform
import shutil
import sys
import types
import urllib.request
import zipfile

import numpy as np

from . import paths

log = logging.getLogger(__name__)
IS_APPLE_SILICON = sys.platform == "darwin" and platform.machine() == "arm64"
# Pinned: tested together (2026-09-30). mlx needs macOS 14 or later.
PACKAGES = [("mlx", "0.32.3"), ("mlx-metal", "0.32.3"), ("mlx-whisper", "0.4.3"), ("tiktoken", "0.14.0"),
            ("regex", None), ("tqdm", None)]
# Our model names -> MLX conversions on Hugging Face (mlx-community).
MODELS = {
    "tiny.en": "whisper-tiny.en-mlx", "base.en": "whisper-base.en-mlx", "small.en": "whisper-small.en-mlx",
    "medium.en": "whisper-medium.en-mlx", "tiny": "whisper-tiny-mlx", "base": "whisper-base-mlx",
    "small": "whisper-small-mlx", "medium": "whisper-medium-mlx", "large-v3": "whisper-large-v3-mlx",
    "large-v3-turbo": "whisper-large-v3-turbo", "distil-large-v3": "distil-whisper-large-v3",
}
_activated = False


def runtime_dir():
    return os.path.join(paths.data_dir(), "apple-gpu")


def installed():
    return os.path.isdir(os.path.join(runtime_dir(), "mlx_whisper")) and \
        os.path.isdir(os.path.join(runtime_dir(), "mlx"))


def available():
    return IS_APPLE_SILICON and installed()


def supports(model_name):
    return model_name in MODELS


# ------------------------------------------------------------------ download
def _mac_version():
    try:
        major, minor = (platform.mac_ver()[0] or "0.0").split(".")[:2]
        return int(major), int(minor)
    except ValueError:
        return 0, 0


def _pick_wheel(files, mac=None):
    """Best wheel for this Python (cp312) on this macOS on arm64, or a pure-Python one."""
    mac = mac or _mac_version()
    py = "cp%d%d" % sys.version_info[:2]
    best, best_key = None, None
    for f in files:
        name = f["filename"]
        if not name.endswith(".whl"):
            continue
        tags = name[:-4].split("-")[-3:]  # python, abi, platform
        pytag, abi, plat = tags
        if plat == "any":
            key = (0, 0)
        elif "macosx" in plat and ("arm64" in plat or "universal2" in plat):
            if not (pytag == py or abi == "abi3" or pytag.startswith("py3")):
                continue
            ver = plat.split("_")[1:3]
            try:
                need = (int(ver[0]), int(ver[1]))
            except (ValueError, IndexError):
                continue
            if need > mac:
                continue
            key = (1,) + need  # newest macOS build this Mac can run
        else:
            continue
        if best_key is None or key > best_key:
            best, best_key = f, key
    return best


def _release_files(package, version):
    url = "https://pypi.org/pypi/%s/%sjson" % (package, (version + "/") if version else "")
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "LocalFlow"}), timeout=30) as r:
        return json.load(r)["urls"]


def download(progress=lambda msg, frac: None):
    """Install the MLX runtime into data/apple-gpu. Returns the size in MB."""
    if not IS_APPLE_SILICON:
        raise RuntimeError("Apple GPU support needs a Mac with Apple Silicon")
    if _mac_version() < (14, 0):
        raise RuntimeError("Apple GPU support needs macOS 14 (Sonoma) or later")
    wheels = []
    for package, version in PACKAGES:
        wheel = _pick_wheel(_release_files(package, version))
        if not wheel:
            raise RuntimeError("no %s build for this Mac" % package)
        wheels.append(wheel)
    total = sum(w["size"] for w in wheels)
    tmp = runtime_dir() + ".partial"
    shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(tmp)
    done = 0
    for w in wheels:
        buf = io.BytesIO()
        req = urllib.request.Request(w["url"], headers={"User-Agent": "LocalFlow"})
        with urllib.request.urlopen(req, timeout=120) as resp:
            while True:
                block = resp.read(1 << 20)
                if not block:
                    break
                buf.write(block)
                progress("Downloading Apple GPU support", (done + buf.tell()) / total)
        data = buf.getvalue()
        if hashlib.sha256(data).hexdigest() != w["digests"]["sha256"]:
            shutil.rmtree(tmp, ignore_errors=True)
            raise RuntimeError("%s failed its checksum; nothing was installed" % w["filename"])
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            z.extractall(tmp)
        done += len(data)
    shutil.rmtree(runtime_dir(), ignore_errors=True)
    os.replace(tmp, runtime_dir())
    return round(total / 1e6)


def remove():
    shutil.rmtree(runtime_dir(), ignore_errors=True)
    shutil.rmtree(os.path.join(paths.models_dir(), "mlx"), ignore_errors=True)


# ------------------------------------------------------------------ running
def _install_shims():
    """numpy stand-ins for the two numba/scipy calls in mlx_whisper.timing (word timestamps)."""
    try:
        import numba  # noqa: F401
    except ImportError:
        numba = types.ModuleType("numba")

        def jit(*args, **kwargs):
            if args and callable(args[0]):
                return args[0]
            return lambda f: f

        numba.jit, numba.njit, numba.prange = jit, jit, range
        sys.modules["numba"] = numba
    try:
        from scipy import signal  # noqa: F401
    except ImportError:
        scipy = types.ModuleType("scipy")
        signal = types.ModuleType("scipy.signal")

        def medfilt(x, kernel_size):
            """Median filter along the last axis (zero padded, like scipy's)."""
            width = kernel_size[-1] if isinstance(kernel_size, (tuple, list)) else kernel_size
            pad = width // 2
            padded = np.pad(x, [(0, 0)] * (x.ndim - 1) + [(pad, pad)])
            windows = np.lib.stride_tricks.sliding_window_view(padded, width, axis=-1)
            return np.median(windows, axis=-1).astype(x.dtype)

        signal.medfilt = medfilt
        scipy.signal = signal
        sys.modules["scipy"], sys.modules["scipy.signal"] = scipy, signal


def activate():
    global _activated
    if _activated:
        return
    site = runtime_dir()
    if site not in sys.path:
        sys.path.insert(0, site)
    _install_shims()
    _activated = True


def model_path(name, progress=lambda msg, frac: None):
    """Local folder with the MLX version of ``name``, downloading it the first time."""
    repo = MODELS.get(name)
    if not repo:
        raise RuntimeError("%s has no Apple GPU version" % name)
    path = os.path.join(paths.models_dir(), "mlx", repo)
    if not os.path.isfile(os.path.join(path, "config.json")):
        from huggingface_hub import snapshot_download

        progress("Downloading %s for the Apple GPU" % name, 0.0)
        snapshot_download(repo_id="mlx-community/" + repo, local_dir=path)
    return path


class MLXModel:
    """Whisper on the Apple GPU through mlx-whisper."""

    def __init__(self, name, path=None):
        activate()
        import mlx.core as mx
        import mlx_whisper
        from mlx_whisper.transcribe import ModelHolder

        self.name = name
        self.path = path or model_path(name)
        self._mw = mlx_whisper
        ModelHolder.get_model(self.path, mx.float16)  # load now, not on the first dictation
        # The GPU's first run compiles its kernels (seconds); do it now rather than on the
        # first dictation.
        self.transcribe(np.zeros(16000, dtype=np.float32), "en", temperature=0.0, sample_len=8)

    def transcribe(self, audio, language=None, prompt=None, word_timestamps=False,
                   temperature=(0.0, 0.2, 0.4, 0.6, 0.8), sample_len=None):
        options = dict(path_or_hf_repo=self.path, language=language, initial_prompt=prompt,
                       condition_on_previous_text=False, word_timestamps=word_timestamps,
                       temperature=temperature, verbose=None)
        if sample_len:
            options["sample_len"] = sample_len
        return self._mw.transcribe(np.asarray(audio, dtype=np.float32), **options)
