"""CPU benchmark: WER, latency, empty/runaway outputs, and vocabulary recall for LocalFlow candidates."""
import json, os, re, sys, time, statistics as st
import numpy as np, soundfile as sf, jiwer
from whisper_normalizer.english import EnglishTextNormalizer
norm = EnglishTextNormalizer()
THREADS = int(os.environ.get("THREADS", "6"))  # LocalFlow: physical cores, max 8
M = "models/"  # sherpa-onnx model folders (k2-fsa/sherpa-onnx releases, tag asr-models)
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_TOK = None
def uncommon(ref):
    """LocalFlow's own test (transcriber.is_uncommon): words Whisper's tokenizer splits into several pieces."""
    global _TOK
    if _TOK is None:
        import glob
        from tokenizers import Tokenizer
        _TOK = Tokenizer.from_file(os.path.join(REPO, "build", "models", "base.en", "tokenizer.json"))
    words = {w.strip(".,?!'\"") for w in ref.split()}
    return sorted(w for w in words if len(w) > 3 and w.isalpha() and len(_TOK.encode(" " + w, add_special_tokens=False).ids) > 1)

def vocab_of(ref):
    """Proper nouns / jargon in a cased reference: capitalised words that aren't sentence-initial."""
    words = re.findall(r"[A-Za-z][A-Za-z'&.-]*", ref)
    out = []
    for i, w in enumerate(words):
        prev = ref[:ref.find(w)].rstrip()
        if w[0].isupper() and w not in ("I", "I'm", "I've", "I'll", "I'd", "Um", "Uh", "OK", "Okay") and i and not prev.endswith((".", "?", "!")):
            out.append(w.strip(".'"))
    return sorted(set(o for o in out if len(o) > 2))

def sherpa_offline(kind, d, **kw):
    import sherpa_onnx as so
    O = so.OfflineRecognizer; p = M + d + "/"
    q = lambda n: p + n
    if kind == "transducer":
        r = O.from_transducer(q("encoder.int8.onnx"), q("decoder.int8.onnx"), q("joiner.int8.onnx"), q("tokens.txt"),
                              num_threads=THREADS, model_type="nemo_transducer", **kw)
    elif kind == "canary":
        r = O.from_nemo_canary(encoder=q("encoder.int8.onnx"), decoder=q("decoder.int8.onnx"), tokens=q("tokens.txt"),
                               num_threads=THREADS, src_lang="en", tgt_lang="en")
    elif kind == "moonshine2":
        r = O.from_moonshine_v2(q("encoder_model.ort"), q("decoder_model_merged.ort"), q("tokens.txt"), num_threads=THREADS)
    elif kind == "cohere":
        r = O.from_cohere_transcribe(q("encoder.int8.onnx"), q("decoder.int8.onnx"), q("tokens.txt"), num_threads=THREADS, language="en")
    elif kind == "qwen3":
        r = O.from_qwen3_asr(q("conv_frontend.onnx"), q("encoder.int8.onnx"), q("decoder.int8.onnx"), q("tokenizer"), num_threads=THREADS)
    def run(audio, vocab=None):
        s = r.create_stream(hotwords="/".join(vocab)) if (vocab and kw.get("hotwords_file") is not None) else r.create_stream()
        s.accept_waveform(16000, audio); r.decode_stream(s); return s.result.text
    return run

def sherpa_online(d):
    import sherpa_onnx as so
    p = M + d + "/"
    r = so.OnlineRecognizer.from_transducer(tokens=p + "tokens.txt", encoder=p + "encoder.int8.onnx", decoder=p + "decoder.int8.onnx",
                                            joiner=p + "joiner.int8.onnx", num_threads=THREADS, model_type="nemo_transducer" if False else "")
    def run(audio, vocab=None):
        s = r.create_stream(); s.accept_waveform(16000, audio)
        s.accept_waveform(16000, np.zeros(16000, dtype=np.float32)); s.input_finished()
        while r.is_ready(s): r.decode_stream(s)
        return r.get_result(s)
    return run

def whisper(name, beam=1):
    from faster_whisper import WhisperModel
    m = WhisperModel(name, device="cpu", compute_type="int8", cpu_threads=THREADS)
    def run(audio, vocab=None):  # same options as localflow/transcriber.py
        prompt = ("Vocabulary: " + ", ".join(vocab) + ".") if vocab else None
        segs, _ = m.transcribe(audio, language="en", beam_size=beam, condition_on_previous_text=False, initial_prompt=prompt,
                               without_timestamps=False, vad_filter=True,
                               vad_parameters={"threshold": 0.35, "min_silence_duration_ms": 1000, "speech_pad_ms": 400})
        return " ".join(s.text.strip() for s in segs).strip()
    return run

def localflow(name):
    """The real app path: localflow.Transcriber (VAD trim, dither, greedy) from the worktree."""
    import sys as _s
    _s.path.insert(0, REPO)  # set LOCALFLOW_DATA_DIR to keep downloads out of your real data folder
    from localflow.transcriber import Transcriber
    t = Transcriber(); t.load(name)
    return lambda audio, vocab=None: t.transcribe(audio, "en", vocab, 1)

ENGINES = {
    "app-parakeet-v2": lambda: localflow("parakeet-v2"),
    "app-parakeet-v3": lambda: localflow("parakeet-v3"),
    "app-parakeet-small": lambda: localflow("parakeet-small"),
    "whisper-base.en": lambda: whisper("base.en"),
    "whisper-small.en": lambda: whisper("small.en"),
    "whisper-large-v3-turbo": lambda: whisper("large-v3-turbo"),
    "parakeet-tdt-0.6b-v2": lambda: sherpa_offline("transducer", "sherpa-onnx-nemo-parakeet-tdt-0.6b-v2-int8"),
    "parakeet-tdt-0.6b-v3": lambda: sherpa_offline("transducer", "sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8"),
    "parakeet-unified-en-0.6b": lambda: sherpa_offline("transducer", "sherpa-onnx-nemo-parakeet-unified-en-0.6b-int8-non-streaming"),
    "parakeet-tdt-110m": lambda: sherpa_offline("transducer", "sherpa-onnx-nemo-parakeet_tdt_transducer_110m-en-36000-int8"),
    "canary-180m-flash": lambda: sherpa_offline("canary", "sherpa-onnx-nemo-canary-180m-flash-en-es-de-fr-int8"),
    "moonshine-base-v2": lambda: sherpa_offline("moonshine2", "sherpa-onnx-moonshine-base-en-quantized-2026-02-27"),
    "cohere-transcribe-2b": lambda: sherpa_offline("cohere", "sherpa-onnx-cohere-transcribe-14-lang-int8-2026-04-01"),
    "qwen3-asr-0.6b": lambda: sherpa_offline("qwen3", "sherpa-onnx-qwen3-asr-0.6B-int8-2026-03-25"),
    "nemotron-stream-en-560ms": lambda: sherpa_online("sherpa-onnx-nemotron-speech-streaming-en-0.6b-560ms-int8-2026-04-25"),
    "nemotron-3.5-stream-560ms": lambda: sherpa_online("sherpa-onnx-nemotron-3.5-asr-streaming-0.6b-560ms-int8-2026-06-11"),
}

def main(name, limit=None, vocab_mode=False):
    meta = json.load(open("clips/meta.json"))[:limit]
    if vocab_mode: meta = [e for e in meta if e["set"].startswith("earnings22") and vocab_of(e["ref"])]
    t0 = time.time(); run = ENGINES[name](); load = time.time() - t0
    run(sf.read("clips/" + meta[0]["file"], dtype="float32")[0][:16000 * 3])  # warm-up
    rows = []
    for e in meta:
        a = sf.read("clips/" + e["file"], dtype="float32")[0]
        v = vocab_of(e["ref"]) if vocab_mode else None
        t = time.time(); hyp = run(a, v); dt = time.time() - t
        rows.append({**e, "hyp": hyp, "sec": round(dt, 3), "vocab": v})
    out = f"results/{name}{'-vocab' if vocab_mode else ''}.json"
    os.makedirs("results", exist_ok=True); json.dump({"load": load, "rows": rows}, open(out, "w"), indent=1)
    print(name, "done", len(rows), "clips, load %.1fs" % load)

if __name__ == "__main__":
    main(sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 and sys.argv[2].isdigit() else None, "--vocab" in sys.argv)
