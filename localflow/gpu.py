"""NVIDIA GPU support for local transcription (Windows).

CTranslate2 can run Whisper on an NVIDIA GPU (about 3-5x faster than the CPU in our
measurements), but it needs NVIDIA's cuBLAS and cuDNN libraries, about 1 GB, which are
too big to ship to everyone. "Download GPU support" in Settings fetches the official
NVIDIA wheels from PyPI into data/gpu/ once; after that the GPU is used automatically.
Macs have no NVIDIA GPU, so this is Windows-only.
"""
import glob
import json
import logging
import os
import sys
import urllib.request
import zipfile

from . import paths

log = logging.getLogger(__name__)
IS_WIN = sys.platform == "win32"
# Versions verified with CTranslate2 4.8 (CUDA 12, cuDNN 9).
PACKAGES = {"nvidia-cublas-cu12": None, "nvidia-cudnn-cu12": "9"}
# Measured 2026-09-28 (GTX 1060, CTranslate2 4.8.2): Whisper inference needs only these three
# of the 16 DLLs in the wheels (735 MB instead of 2.1 GB). Without cudnn64_9.dll it silently
# falls back to the CPU. We read just these members out of the remote wheels (HTTP range
# requests), so the download is also ~450 MB instead of ~1.2 GB.
KEEP = {"cublas64_12.dll", "cublasLt64_12.dll", "cudnn64_9.dll"}
_activated = False


def gpu_dir():
    return os.path.join(paths.data_dir(), "gpu")


def libraries_present():
    d = gpu_dir()
    return os.path.isfile(os.path.join(d, "cublas64_12.dll")) and bool(glob.glob(os.path.join(d, "cudnn64_9.dll")))


def activate():
    """Make the downloaded DLLs findable. CTranslate2 locates them through PATH (Python's
    add_dll_directory alone is not enough), so set both."""
    global _activated
    if _activated or not IS_WIN:
        return
    d = gpu_dir()
    if os.path.isdir(d):
        os.environ["PATH"] = d + os.pathsep + os.environ.get("PATH", "")
        try:
            os.add_dll_directory(d)
        except (AttributeError, OSError):
            pass
    _activated = True


def nvidia_present():
    """Is there an NVIDIA GPU at all? (Checked without the CUDA libraries.)"""
    if not IS_WIN:
        return False
    import ctypes

    try:
        ctypes.WinDLL("nvcuda.dll")  # installed with the NVIDIA display driver
        return True
    except OSError:
        return False


def device_count():
    try:
        import ctranslate2

        return ctranslate2.get_cuda_device_count()
    except Exception:
        return 0


def available():
    """Libraries downloaded and a CUDA device visible."""
    if not IS_WIN or not libraries_present():
        return False
    activate()
    return device_count() > 0


def _wheel_url(package, major=None):
    with urllib.request.urlopen("https://pypi.org/pypi/%s/json" % package, timeout=30) as resp:
        info = json.load(resp)
    versions = sorted(info["releases"], key=lambda v: [int(x) if x.isdigit() else 0 for x in v.split(".")],
                      reverse=True)
    for version in versions:
        if major and not version.startswith(major + "."):
            continue
        for f in info["releases"][version]:
            if f["filename"].endswith("win_amd64.whl") and not f.get("yanked"):
                return version, f["url"], f["size"]
    raise RuntimeError("no Windows build of %s found on PyPI" % package)


class RemoteFile:
    """A read-only, seekable file over HTTP range requests, so zipfile can pull single
    members out of a remote wheel without downloading all of it."""

    def __init__(self, url, on_bytes=lambda n: None):
        self.url, self.pos, self.on_bytes = url, 0, on_bytes
        req = urllib.request.Request(url, method="HEAD")
        with urllib.request.urlopen(req, timeout=60) as resp:
            self.size = int(resp.headers["Content-Length"])
        self._buf_start, self._buf = 0, b""

    def seekable(self):
        return True

    def tell(self):
        return self.pos

    def seek(self, offset, whence=0):
        self.pos = {0: offset, 1: self.pos + offset, 2: self.size + offset}[whence]
        return self.pos

    def read(self, n=-1):
        if n is None or n < 0:
            n = self.size - self.pos
        n = min(n, self.size - self.pos)
        if n <= 0:
            return b""
        start = self.pos - self._buf_start
        if 0 <= start and start + n <= len(self._buf):
            data = self._buf[start:start + n]
        else:
            want = max(n, 1 << 20)  # read ahead 1 MB: zipfile makes many small reads
            end = min(self.size, self.pos + want) - 1
            req = urllib.request.Request(self.url, headers={"Range": "bytes=%d-%d" % (self.pos, end)})
            with urllib.request.urlopen(req, timeout=120) as resp:
                self._buf = resp.read()
            self._buf_start = self.pos
            self.on_bytes(len(self._buf))
            data = self._buf[:n]
        self.pos += len(data)
        return data


def download(progress=lambda msg, frac: None):
    """Fetch only the needed DLLs from NVIDIA's official wheels into data/gpu/. Returns MB installed.
    ``progress(message, fraction or None)`` is called often: None while preparing, then the
    overall fraction of the whole download (not per file)."""
    import time

    os.makedirs(gpu_dir(), exist_ok=True)
    progress("Finding NVIDIA's GPU libraries…", None)
    got = {"bytes": 0}
    plan = []  # (zipfile, member) for every DLL we need, across both wheels
    for pkg, major in PACKAGES.items():
        version, url, _size = _wheel_url(pkg, major)
        progress("Reading %s %s…" % (pkg, version), None)
        z = zipfile.ZipFile(RemoteFile(url, lambda n: got.__setitem__("bytes", got["bytes"] + n)))
        plan += [(z, i) for i in z.infolist() if os.path.basename(i.filename) in KEEP]
    total = sum(i.file_size for _z, i in plan) or 1
    done, start, last = 0, time.monotonic(), 0.0
    for z, info in plan:
        target = os.path.join(gpu_dir(), os.path.basename(info.filename))
        tmp = target + ".part"
        with z.open(info) as src, open(tmp, "wb") as dst:
            while True:
                chunk = src.read(1 << 20)
                if not chunk:
                    break
                dst.write(chunk)
                done += len(chunk)
                now = time.monotonic()
                if now - last > 0.25 or done == total:
                    last = now
                    speed = done / max(now - start, 0.1) / 1e6
                    progress("Downloading GPU support: %d of %d MB (%.0f MB/s)"
                             % (done / 1e6, total / 1e6, speed), done / total)
        os.replace(tmp, target)
    for z in {z for z, _i in plan}:
        z.close()
    log.info("GPU support: downloaded %.0f MB", got["bytes"] / 1e6)
    return round(sum(os.path.getsize(os.path.join(gpu_dir(), f)) for f in os.listdir(gpu_dir())) / 1e6)


def remove():
    import shutil

    shutil.rmtree(gpu_dir(), ignore_errors=True)
