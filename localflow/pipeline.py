"""Audio in, final text out: normalise -> transcribe (local or cloud, with fallback)
-> rule-based clean-up -> optional AI clean-up. UI-free so it can be tested."""
import logging
import time

from . import audio, cloud, keystore, polish, textproc

log = logging.getLogger(__name__)


def cloud_settings(cfg):
    preset = cloud.STT_PRESETS[cfg.get("cloud_provider", "groq")]
    base = cfg.get("cloud_base_url") if cfg.get("cloud_provider") == "custom" else ""
    return (base or preset["base_url"], cfg.get("cloud_model") or preset["model"], preset["key_name"])


def transcribe(samples, cfg, transcriber, get_key=keystore.get):
    """Returns (raw_text, engine_used, note)."""
    samples = audio.normalize(samples)
    note = ""
    if cfg.get("engine") == "cloud":
        base_url, model, key_name = cloud_settings(cfg)
        try:
            key = get_key(key_name)
            if not key and cfg.get("cloud_provider") != "custom":
                raise cloud.CloudError("no API key saved for %s" % cfg.get("cloud_provider"))
            text = cloud.transcribe(samples, base_url, model, key, cfg.get("language"), cfg.get("vocabulary"))
            return text, "cloud:%s" % model, note
        except Exception as exc:
            log.warning("cloud transcription failed: %s", exc)
            if not cfg.get("cloud_fallback_local", True):
                raise
            note = "Cloud failed (%s); used local model" % str(exc)[:80]
    if not transcriber.ready.wait(timeout=600):
        raise RuntimeError("model is still loading")
    text = transcriber.transcribe(samples, cfg.get("language"), cfg.get("vocabulary"), cfg.get("beam_size", 5))
    return text, "local:%s" % transcriber.model_name, note


def process(samples, cfg, transcriber, get_key=keystore.get, on_stage=None):
    t0 = time.time()
    raw, engine, note = transcribe(samples, cfg, transcriber, get_key)
    t_stt = time.time() - t0
    text = textproc.clean(raw, cfg)
    polished = False
    t_polish = 0.0
    if cfg.get("polish", "off") != "off" and len(text.split()) >= int(cfg.get("polish_min_words", 4)):
        t1 = time.time()
        if on_stage:
            on_stage("polish")
        try:
            cleaned = polish.polish(text.strip(), cfg, get_key)
            text = textproc.apply_replacements(cleaned, cfg.get("replacements"))
            if cfg.get("trailing_space") and text and not text.endswith("\n"):
                text += " "
            polished = True
        except Exception as exc:
            log.warning("AI clean-up failed: %s", exc)
            note = (note + "; " if note else "") + "AI clean-up failed (%s)" % str(exc)[:80]
        t_polish = time.time() - t1
    return {"text": text, "raw": raw, "engine": engine, "polished": polished, "note": note,
            "stt_s": round(t_stt, 2), "polish_s": round(t_polish, 2)}
