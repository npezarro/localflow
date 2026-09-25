import collections
import io
import logging
import os
import threading
import time
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
    return resample(data, rate)


def resample(data, rate):
    """Resample to 16 kHz with a box low-pass first so downsampling doesn't alias."""
    if rate == SAMPLE_RATE or len(data) == 0:
        return data.astype(np.float32)
    if rate > SAMPLE_RATE:
        width = int(round(rate / SAMPLE_RATE))
        if width > 1:
            data = np.convolve(data, np.ones(width) / width, mode="same")
    n = int(len(data) * SAMPLE_RATE / rate)
    out = np.interp(np.linspace(0, len(data), n, endpoint=False), np.arange(len(data)), data)
    return out.astype(np.float32)


def to_wav_bytes(samples):
    pcm = (np.clip(samples, -1, 1) * 32767).astype(np.int16)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(pcm.tobytes())
    return buf.getvalue()


def save_wav(path, samples):
    with open(path, "wb") as f:
        f.write(to_wav_bytes(samples))


def stats(samples):
    if len(samples) == 0:
        return {"seconds": 0.0, "peak": 0.0, "rms": 0.0}
    return {"seconds": round(len(samples) / SAMPLE_RATE, 2),
            "peak": round(float(np.max(np.abs(samples))), 4),
            "rms": round(float(np.sqrt(np.mean(samples ** 2))), 5)}


def normalize(samples, target_peak=0.7, max_gain=12.0):
    """Quiet microphones make the voice-activity filter drop real speech; bring the level up."""
    if len(samples) == 0:
        return samples
    peak = float(np.max(np.abs(samples)))
    if peak < 1e-4:
        return samples
    gain = min(max_gain, target_peak / peak)
    if gain <= 1.0:
        return samples
    return (samples * gain).astype(np.float32)


def input_devices():
    import sounddevice as sd

    out = []
    for idx, dev in enumerate(sd.query_devices()):
        if dev.get("max_input_channels", 0) > 0:
            out.append((idx, dev["name"]))
    return out


class Recorder:
    """Microphone capture.

    With ``warm=True`` the input stream stays open and keeps the last ``preroll``
    seconds in a ring buffer, so the first syllable spoken as the hotkey goes down
    is not lost (opening a device takes 100 ms to over a second on Bluetooth).
    With ``warm=False`` the mic opens only while dictating.
    """

    def __init__(self, warm=True, preroll=0.5, tail=0.3):
        self.warm = warm
        self.preroll = preroll
        self.tail = tail
        self.device = None
        self._stream = None
        self._rate = SAMPLE_RATE
        self._recording = False
        self._chunks = []
        self.offset = 0.0  # seconds of this recording already dropped by trim_before()
        self._ring = collections.deque()
        self._ring_len = 0
        self._lock = threading.Lock()
        self.level = 0.0
        self.last_error = None
        # Test hook: feed a WAV file instead of the microphone.
        self._fake = os.environ.get("LOCALFLOW_FAKE_MIC")

    # --- stream management -----------------------------------------------------------
    def configure(self, device=None, warm=True):
        changed = device != self.device or warm != self.warm
        self.device, self.warm = device, warm
        if changed:
            self.close()
        if self.warm and not self._fake:
            self._ensure_stream()

    def _open(self, device, rate):
        import sounddevice as sd

        stream = sd.InputStream(samplerate=rate, channels=1, dtype="float32", device=device,
                                callback=self._callback, blocksize=int(rate * 0.03))
        stream.start()
        return stream

    def _ensure_stream(self):
        if self._stream is not None and self._stream.active:
            return
        self.close()
        import sounddevice as sd

        attempts = []
        for device in ([self.device, None] if self.device is not None else [None]):
            try:
                native = int(sd.query_devices(device, "input")["default_samplerate"])
            except Exception:
                native = 48000
            for rate in (SAMPLE_RATE, native):
                try:
                    self._stream = self._open(device, rate)
                    self._rate = rate
                    if device != self.device:
                        log.warning("input device %r failed; using system default", self.device)
                    log.info("mic open: device=%r rate=%d warm=%s", device, rate, self.warm)
                    self.last_error = None
                    return
                except Exception as exc:
                    attempts.append("%r@%d: %s" % (device, rate, exc))
        self.last_error = "; ".join(attempts)
        raise RuntimeError("could not open microphone (%s)" % self.last_error)

    def close(self):
        stream, self._stream = self._stream, None
        if stream is not None:
            try:
                stream.stop()
                stream.close()
            except Exception:
                log.debug("closing input stream", exc_info=True)

    def _callback(self, indata, frames, time_info, status):
        chunk = indata[:, 0].copy()
        rms = float(np.sqrt(np.mean(chunk ** 2))) if len(chunk) else 0.0
        self.level = min(1.0, rms * 12)
        with self._lock:
            if self._recording:
                self._chunks.append(chunk)
            else:
                self._ring.append(chunk)
                self._ring_len += len(chunk)
                limit = int(self.preroll * self._rate)
                while self._ring and self._ring_len - len(self._ring[0]) >= limit:
                    self._ring_len -= len(self._ring.popleft())

    # --- dictation -------------------------------------------------------------------
    def start(self):
        self.offset = 0.0
        if self._fake:
            self._recording = True
            self._fake_started = time.monotonic()
            return
        self._ensure_stream()
        with self._lock:
            self._chunks = list(self._ring) if self.warm else []
            self._ring.clear()
            self._ring_len = 0
            self._recording = True

    def stop(self, keep_tail=True):
        """Returns the audio since ``self.offset`` (all of it unless trim_before() was used)."""
        if self._fake:
            self._recording = False
            full = load_wav(self._fake)
            if os.environ.get("LOCALFLOW_FAKE_MIC_REALTIME") == "1":  # like a real mic: only what was "said"
                full = full[:int((time.monotonic() - self._fake_started + self.tail) * SAMPLE_RATE)]
            return full[int(self.offset * SAMPLE_RATE):]
        if keep_tail and self.tail > 0 and self._recording:
            time.sleep(self.tail)  # the last word often trails the key release
        with self._lock:
            chunks, self._chunks = self._chunks, []
            self._recording = False
        if not self.warm:
            self.close()
            self.level = 0.0
        if not chunks:
            return np.zeros(0, dtype=np.float32)
        return resample(np.concatenate(chunks), self._rate)

    def peek(self):
        """(audio since ``offset``, offset) without stopping. Used by live typing."""
        if self._fake:
            full = load_wav(self._fake)
            end = int((time.monotonic() - self._fake_started) * SAMPLE_RATE)
            return full[int(self.offset * SAMPLE_RATE):end], self.offset
        with self._lock:
            chunks, offset = list(self._chunks), self.offset
        if not chunks:
            return np.zeros(0, dtype=np.float32), offset
        return resample(np.concatenate(chunks), self._rate), offset

    def trim_before(self, seconds):
        """Forget audio before ``seconds`` into the recording (continuous live mode would
        otherwise keep every minute of audio in memory)."""
        with self._lock:
            if self._fake:
                self.offset = max(self.offset, seconds)
                return
            while self._chunks:
                dur = len(self._chunks[0]) / self._rate
                if self.offset + dur > seconds:
                    break
                self._chunks.pop(0)
                self.offset += dur

    @property
    def active(self):
        return self._recording


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
