import logging
import os
import threading
import wave

import numpy as np

SAMPLE_RATE = 16000
log = logging.getLogger(__name__)


def load_wav(path):
    """16 kHz mono float32 from a PCM WAV (no ffmpeg needed)."""
    with wave.open(path, "rb") as w:
        channels, rate, width = w.getnchannels(), w.getframerate(), w.getsampwidth()
        raw = w.readframes(w.getnframes())
    dtype = {1: np.uint8, 2: np.int16, 4: np.int32}[width]
    data = np.frombuffer(raw, dtype=dtype).astype(np.float32)
    if width == 1:
        data = (data - 128) / 128.0
    else:
        data /= float(2 ** (8 * width - 1))
    if channels > 1:
        data = data.reshape(-1, channels).mean(axis=1)
    if rate != SAMPLE_RATE:
        n = int(len(data) * SAMPLE_RATE / rate)
        data = np.interp(np.linspace(0, len(data), n, endpoint=False), np.arange(len(data)), data)
    return data.astype(np.float32)


def input_devices():
    import sounddevice as sd

    out = []
    for idx, dev in enumerate(sd.query_devices()):
        if dev.get("max_input_channels", 0) > 0:
            out.append((idx, dev["name"]))
    return out


class Recorder:
    """Opens the mic only while dictating (so the OS mic indicator means something)."""

    def __init__(self):
        self._stream = None
        self._chunks = []
        self._lock = threading.Lock()
        self.level = 0.0
        # Test hook: feed a WAV file instead of the microphone.
        self._fake = os.environ.get("LOCALFLOW_FAKE_MIC")

    def start(self, device=None):
        with self._lock:
            self._chunks = []
        self.level = 0.0
        if self._fake:
            return
        import sounddevice as sd

        try:
            self._stream = sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="float32",
                                          device=device, callback=self._callback)
        except Exception:
            if device is None:
                raise
            log.warning("input device %r failed, using default", device)
            self._stream = sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="float32",
                                          callback=self._callback)
        self._stream.start()

    def _callback(self, indata, frames, time_info, status):
        chunk = indata[:, 0].copy()
        with self._lock:
            self._chunks.append(chunk)
        rms = float(np.sqrt(np.mean(chunk ** 2))) if len(chunk) else 0.0
        self.level = min(1.0, rms * 12)

    def stop(self):
        if self._fake:
            return load_wav(self._fake)
        stream, self._stream = self._stream, None
        if stream is not None:
            try:
                stream.stop()
                stream.close()
            except Exception:
                log.exception("closing input stream")
        with self._lock:
            chunks, self._chunks = self._chunks, []
        self.level = 0.0
        if not chunks:
            return np.zeros(0, dtype=np.float32)
        return np.concatenate(chunks)

    @property
    def active(self):
        return self._stream is not None


def tone(freq, ms=70, volume=0.12, rate=44100):
    t = np.linspace(0, ms / 1000, int(rate * ms / 1000), endpoint=False)
    env = np.minimum(1, np.minimum(t, t[::-1]) * 200)  # 5 ms fade in/out
    return (np.sin(2 * np.pi * freq * t) * env * volume).astype(np.float32)


def play(samples, rate=44100):
    try:
        import sounddevice as sd

        sd.play(samples, rate)
    except Exception:
        log.debug("sound playback failed", exc_info=True)
