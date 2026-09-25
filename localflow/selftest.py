"""Headless check used by CI on every platform: load the model, transcribe a WAV,
exercise the text pipeline and the clipboard. Writes JSON to --out (the Windows
build has no console) and exits non-zero on failure."""
import json
import os
import platform
import sys
import time

from . import __version__, audio, config, paths, textproc
from .transcriber import Transcriber, resolve_model


def _step(msg):
    import logging

    logging.getLogger("localflow.selftest").info(msg)
    if sys.stderr:
        sys.stderr.write("[selftest] %s\n" % msg)
        sys.stderr.flush()


def run(argv):
    import faulthandler

    if sys.stderr:
        faulthandler.dump_traceback_later(300, exit=True)  # CI: show where a hang is, then fail
    wav = None
    out = None
    model = None
    expect = "country"
    it = iter(argv)
    for arg in it:
        if arg == "--out":
            out = next(it)
        elif arg == "--model":
            model = next(it)
        elif arg == "--expect":
            expect = next(it)
        elif arg != "--selftest":
            wav = arg
    wav = wav or os.path.join(paths.bundle_dir(), "samples", "jfk.wav")
    cfg = dict(config.DEFAULTS)
    model = model or cfg["model"]
    result = {"version": __version__, "platform": platform.platform(), "machine": platform.machine(),
              "python": sys.version.split()[0], "model": model,
              "model_source": resolve_model(model)[0], "data_dir": paths.data_dir(), "ok": False}
    try:
        _step("loading model")
        t = Transcriber()
        result["load_s"] = round(t.load(model), 2)
        _step("transcribing")
        samples = audio.load_wav(wav)
        t0 = time.time()
        raw = t.transcribe(samples, cfg["language"], ["Kennedy"], cfg["beam_size"])
        result["transcribe_s"] = round(time.time() - t0, 2)
        result["audio_s"] = round(len(samples) / audio.SAMPLE_RATE, 2)
        result["text"] = textproc.clean(raw, cfg)
        _step("clipboard")
        try:
            from . import output

            output.set_clipboard(result["text"])
            result["clipboard_roundtrip"] = output.get_clipboard() == result["text"]
        except Exception as exc:  # headless Linux CI has no clipboard
            result["clipboard_roundtrip"] = "unavailable: %s" % exc
        from . import keystore

        result["keychain"] = keystore.backend_name() or "none (data/secrets.json fallback)"
        _step("pynput")
        try:
            import pynput.keyboard  # noqa: F401  (import check for the bundled backend)

            result["pynput"] = "ok"
        except Exception as exc:
            result["pynput"] = "import failed: %s" % exc
        result["ok"] = expect.lower() in result["text"].lower()
    except Exception as exc:
        import traceback

        result["error"] = traceback.format_exc() or str(exc)
    payload = json.dumps(result, indent=2)
    if out:
        with open(out, "w", encoding="utf-8") as f:
            f.write(payload)
    if sys.stdout:
        print(payload)
    return 0 if result["ok"] else 1
