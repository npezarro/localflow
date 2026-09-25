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


class Transcriber:
    def __init__(self):
        self.model = None
        self.model_name = None
        self._lock = threading.Lock()
        self.ready = threading.Event()

    def load(self, name):
        target, download_root = resolve_model(name)
        from faster_whisper import WhisperModel

        log.info("loading model %s from %s", name, target)
        t0 = time.time()
        threads = max(1, min(8, (os.cpu_count() or 4)))
        model = WhisperModel(target, device="cpu", compute_type="int8", cpu_threads=threads,
                             download_root=download_root)
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

    def transcribe(self, audio, language="en", vocabulary=None, beam_size=5):
        with self._lock:
            model = self.model
            if model is None:
                raise RuntimeError("model not loaded")
            prompt = None
            if vocabulary:
                prompt = "Vocabulary: " + ", ".join(vocabulary) + "."
            kwargs = dict(
                language=None if language in (None, "", "auto") else language,
                beam_size=beam_size,
                condition_on_previous_text=False,
                initial_prompt=prompt,
                without_timestamps=True,
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
        """Word-level hypothesis for live typing: [(start_s, end_s, text_with_leading_space)]."""
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
                    words.append((float(w.start), float(w.end), w.word))
            return words
