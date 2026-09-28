import logging
import os
import threading
import time

from . import paths

log = logging.getLogger(__name__)


def resolve_model(name):
    """Return (path_or_name, download_root). Prefer a local copy so no network is needed."""
    for base in (paths.models_dir(), paths.bundled_models_dir()):
        candidate = os.path.join(base, name)
        if os.path.isfile(os.path.join(candidate, "model.bin")):
            return candidate, None
    return name, paths.models_dir()


def is_local(name):
    """True if the model is on disk (bundled, or downloaded earlier into the HF cache layout)."""
    if resolve_model(name)[1] is None:
        return True
    try:
        from faster_whisper.utils import _MODELS

        repo = _MODELS.get(name, name)
    except Exception:
        repo = name
    cached = os.path.join(paths.models_dir(), "models--" + repo.replace("/", "--"), "snapshots")
    return os.path.isdir(cached) and bool(os.listdir(cached))


def speech_in(samples, threshold=0.3):
    """Does this audio contain speech? (Silero VAD, bundled with faster-whisper.)
    Deliberately sensitive: calling a pause 'silence' too early cuts a word in half,
    while missing a pause only delays text by a second."""
    if len(samples) < 16000 * 0.25:
        return False
    from faster_whisper.vad import VadOptions, get_speech_timestamps

    opts = VadOptions(threshold=threshold, min_speech_duration_ms=80, min_silence_duration_ms=300)
    return bool(get_speech_timestamps(samples, opts))


def speech_regions(samples, threshold=0.35):
    """[(start_s, end_s)] of speech in ``samples`` (Silero VAD)."""
    if len(samples) < 16000 * 0.25:
        return []
    from faster_whisper.vad import VadOptions, get_speech_timestamps

    # speech_pad_ms defaults to 400, which widens every region and shrinks a 0.9 s pause to
    # 0.1 s; pauses are what we look for here, so keep the padding small.
    opts = VadOptions(threshold=threshold, min_speech_duration_ms=100, min_silence_duration_ms=300,
                      speech_pad_ms=30)
    return [(r["start"] / 16000, r["end"] / 16000) for r in get_speech_timestamps(samples, opts)]


class Transcriber:
    def __init__(self):
        self.model = None
        self.model_name = None
        self._lock = threading.Lock()
        self.ready = threading.Event()
        self.device = "cpu"
        self.device_error = ""

    def load(self, name, device="auto"):
        """device: "auto" (NVIDIA GPU when its libraries are installed, else CPU), "cuda", "cpu"."""
        target, download_root = resolve_model(name)
        from faster_whisper import WhisperModel

        from . import gpu

        t0 = time.time()
        # CTranslate2 runs best with one thread per physical core (hyper-threads add nothing).
        threads = max(1, min(8, (os.cpu_count() or 4) // 2))
        use_cuda = device == "cuda" or (device == "auto" and gpu.available())
        model = None
        if use_cuda:
            try:
                gpu.activate()
                model = WhisperModel(target, device="cuda", compute_type="int8", download_root=download_root)
                self.device = "cuda"
            except Exception as exc:
                log.warning("GPU load failed (%s); using the CPU", exc)
                self.device_error = str(exc)[:200]
        if model is None:
            model = WhisperModel(target, device="cpu", compute_type="int8", cpu_threads=threads,
                                 download_root=download_root)
            self.device = "cpu"
        log.info("loading model %s from %s on %s", name, target, self.device)
        with self._lock:
            self.model, self.model_name = model, name
        self.ready.set()
        log.info("model %s ready in %.1fs", name, time.time() - t0)
        return time.time() - t0

    def is_uncommon(self, word):
        """True if Whisper's tokenizer needs several tokens for the word: names and jargon,
        i.e. what it tends to misspell. Common words (and names it knows) are one token."""
        model = self.model
        if model is None:
            return False
        try:
            return len(model.hf_tokenizer.encode(" " + word, add_special_tokens=False).ids) > 1
        except Exception:
            return False

    def transcribe(self, audio, language="en", vocabulary=None, beam_size=5, context=None):
        """``context``: text that came just before this audio (earlier chunks of the same
        dictation), so a chunk continues the sentence instead of starting fresh."""
        with self._lock:
            model = self.model
            if model is None:
                raise RuntimeError("model not loaded")
            parts = []
            if vocabulary:
                parts.append("Vocabulary: " + ", ".join(vocabulary) + ".")
            if context:
                parts.append(context.strip()[-300:])
            prompt = " ".join(parts) or None
            kwargs = dict(
                language=None if language in (None, "", "auto") else language,
                beam_size=beam_size,
                condition_on_previous_text=False,
                initial_prompt=prompt,
                # Timestamps must stay ON: without them, when Whisper stops writing early in a
                # 30-second window it can't tell where it stopped and skips the rest of the
                # window, silently dropping speech from dictations over ~20 s.
                without_timestamps=False,
            )
            # Voice-activity filtering stops Whisper inventing text over silence; keep it
            # permissive so soft or clipped speech isn't thrown away.
            segments, _info = model.transcribe(
                audio, vad_filter=True,
                vad_parameters={"threshold": 0.35, "min_silence_duration_ms": 1000,
                                "speech_pad_ms": 400},
                **kwargs)
            text = " ".join(s.text.strip() for s in segments).strip()
            if not text and len(audio) > 16000 * 0.6 and float(abs(audio).max()) > 0.02:
                log.info("VAD found no speech in audible clip; retrying without VAD")
                segments, _info = model.transcribe(audio, vad_filter=False, **kwargs)
                text = " ".join(s.text.strip() for s in segments
                                if s.no_speech_prob < 0.6).strip()
            return text

    def transcribe_words(self, audio, language="en", prompt=None, beam_size=1):
        """Word-level hypothesis for live typing: [(start_s, end_s, text_with_leading_space, probability)]."""
        with self._lock:
            model = self.model
            if model is None:
                raise RuntimeError("model not loaded")
            segments, _info = model.transcribe(
                audio,
                language=None if language in (None, "", "auto") else language,
                beam_size=beam_size,
                condition_on_previous_text=False,
                initial_prompt=prompt,
                word_timestamps=True,
                temperature=0.0,  # one decoding pass; fallback retries made live passes take seconds
                # Speech runs ~3 tokens/s; a loop on a cut-off word ("All right. All right…")
                # otherwise decodes until the 448-token limit and stalls the live text.
                max_new_tokens=min(440, int(len(audio) / 16000 * 6) + 16),
                vad_filter=True,
                vad_parameters={"threshold": 0.35, "min_silence_duration_ms": 1000, "speech_pad_ms": 400},
            )
            words = []
            last = None
            for seg in segments:  # decoded lazily: breaking out skips the remaining work
                text = seg.text.strip().lower()
                if text and text == last:
                    break  # "All right. All right. …": a hallucination loop on a cut-off word
                last = text
                if seg.no_speech_prob > 0.6:
                    continue
                for w in seg.words or []:
                    words.append((float(w.start), float(w.end), w.word, float(w.probability)))
            return words
