import logging
import os
import threading
import time

import numpy as np

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
        self.mlx = None  # apple_gpu.MLXModel when running on the Apple Silicon GPU
        self._tokenizer = None  # for is_uncommon() when the model isn't a CTranslate2 one
        self.model_name = None
        self._lock = threading.Lock()
        self.ready = threading.Event()
        self.device = "cpu"
        self.device_error = ""

    def load(self, name, device="auto"):
        """device: "auto" (a GPU when its support is installed, else CPU), "cuda", "apple", "cpu"."""
        from . import apple_gpu

        t0 = time.time()
        use_apple = (device == "apple" or (device == "auto" and apple_gpu.available())) and apple_gpu.supports(name)
        if use_apple:
            try:
                mlx = apple_gpu.MLXModel(name)
                with self._lock:
                    self.model, self.mlx, self.model_name = None, mlx, name
                    self._tokenizer = self._load_tokenizer()
                self.device, self.device_error = "apple", ""
                self.ready.set()
                log.info("model %s ready on the Apple GPU in %.1fs", name, time.time() - t0)
                return time.time() - t0
            except Exception as exc:
                log.warning("Apple GPU load failed (%s); using the CPU", exc)
                self.device_error = str(exc)[:200]
        target, download_root = resolve_model(name)
        from faster_whisper import WhisperModel

        from . import gpu

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
            self.model, self.mlx, self.model_name = model, None, name
        self.ready.set()
        log.info("model %s ready in %.1fs", name, time.time() - t0)
        return time.time() - t0

    def is_uncommon(self, word):
        """True if Whisper's tokenizer needs several tokens for the word: names and jargon,
        i.e. what it tends to misspell. Common words (and names it knows) are one token."""
        model = self.model
        tok = model.hf_tokenizer if model is not None else self._tokenizer
        if tok is None:
            return False
        try:
            return len(tok.encode(" " + word, add_special_tokens=False).ids) > 1
        except Exception:
            return False

    @staticmethod
    def _load_tokenizer():
        """Whisper's tokenizer from the bundled model (same vocabulary for "uncommon word" checks)."""
        try:
            from tokenizers import Tokenizer

            for folder in (paths.bundled_models_dir(), os.path.join(paths.bundle_dir(), "build", "models"),
                           paths.models_dir()):  # packaged app, source checkout, downloads
                path = os.path.join(folder, "base.en", "tokenizer.json")
                if os.path.exists(path):
                    return Tokenizer.from_file(path)
            return None
        except Exception:
            log.info("no tokenizer for uncommon-word checks", exc_info=True)
            return None

    def transcribe(self, audio, language="en", vocabulary=None, beam_size=5, context=None):
        """``context``: text that came just before this audio (earlier chunks of the same
        dictation), so a chunk continues the sentence instead of starting fresh."""
        with self._lock:
            model = self.model
            if model is None and self.mlx is None:
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
            if self.mlx is not None:
                return self._mlx_text(audio, kwargs["language"], prompt)
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
            if model is None and self.mlx is None:
                raise RuntimeError("model not loaded")
            if self.mlx is not None:
                return self._mlx_words(audio, None if language in (None, "", "auto") else language, prompt)
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

    # ------------------------------------------------------------------ Apple GPU (MLX)
    def _mlx_text(self, audio, language, prompt):
        """Like the CTranslate2 path: transcribe only the speech (VAD), so Whisper can't invent
        text over silence; if VAD finds nothing in an audible clip, try the whole clip."""
        regions = speech_regions(audio)
        if regions:
            pad = int(0.2 * 16000)
            clip = np.concatenate([audio[max(0, int(s * 16000) - pad):int(e * 16000) + pad] for s, e in regions])
        elif len(audio) > 16000 * 0.6 and float(abs(audio).max()) > 0.02:
            clip = audio
        else:
            return ""
        result = self.mlx.transcribe(clip, language, prompt)
        return " ".join(s["text"].strip() for s in result.get("segments", [])
                        if s.get("no_speech_prob", 0) < 0.6).strip()

    def _mlx_words(self, audio, language, prompt):
        result = self.mlx.transcribe(audio, language, prompt, word_timestamps=True, temperature=0.0,
                                     sample_len=min(440, int(len(audio) / 16000 * 6) + 16))
        words, last = [], None
        for seg in result.get("segments", []):
            text = seg.get("text", "").strip().lower()
            if text and text == last:
                break  # hallucination loop on a cut-off word
            last = text
            if seg.get("no_speech_prob", 0) > 0.6:
                continue
            for w in seg.get("words", []):
                words.append((float(w["start"]), float(w["end"]), w["word"], float(w.get("probability", 1.0))))
        return words
