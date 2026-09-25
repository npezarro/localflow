import json
import os
import sys

from . import paths

IS_MAC = sys.platform == "darwin"

DEFAULTS = {
    # Hold to talk. While holding, tap the lock key to go hands-free.
    "hotkey": "ctrl+alt" if IS_MAC else "ctrl+cmd",
    # "hold" = push-to-talk (tap lock key for hands-free); "toggle" = press once to start, again to stop
    "mode": "hold",
    "lock_key": "space",
    "paste_last_hotkey": "alt+shift+z",
    # Transcription engine: "local" (Whisper on this machine) or "cloud" (OpenAI-compatible API)
    "engine": "local",
    "model": "base.en",
    "cloud_provider": "groq",
    "cloud_base_url": "",  # custom provider only
    "cloud_model": "",  # blank = provider default
    "cloud_fallback_local": True,
    # Optional AI clean-up: "off", "claude" (Claude Code login), "codex" (ChatGPT login), "api"
    "polish": "off",
    "claude_model": "sonnet",
    "codex_model": "",
    "polish_api_provider": "groq",
    "polish_api_base_url": "",
    "polish_api_model": "",
    "polish_prompt": "",
    "polish_min_words": 4,
    "polish_timeout": 12,
    "polish_verified": "",  # provider whose setup last passed; anything else re-opens setup
    "setup_seen": False,
    "claude_path": "",
    "codex_path": "",
    "language": "en",  # "auto" to detect
    "beam_size": 5,
    "input_device": None,  # None = system default microphone
    "warm_mic": True,  # keep the mic open so the first word isn't clipped
    "save_last_recording": True,  # data/last-recording.wav, for troubleshooting
    "auto_paste": True,
    "restore_clipboard": False,  # False = transcript stays on the clipboard
    "remove_fillers": True,
    "trailing_space": True,
    "sounds": True,
    "min_hold_seconds": 0.3,
    "release_tail": 0.3,  # seconds the mic stays open after you let go of the hotkey
    "preroll": 0.5,  # seconds kept from just before the hotkey (needs warm_mic)
    "vocabulary": [],  # words/names Whisper should spell your way
    "replacements": {},  # spoken -> written, e.g. {"new line": "\n"}
    "history_limit": 500,
}

MODEL_CHOICES = [
    "tiny.en", "base.en", "small.en", "medium.en",
    "distil-large-v3", "large-v3-turbo",
    "tiny", "base", "small", "medium",
]


def config_path():
    return os.path.join(paths.data_dir(), "config.json")


def load():
    cfg = dict(DEFAULTS)
    try:
        with open(config_path(), encoding="utf-8") as f:
            stored = json.load(f)
        if isinstance(stored, dict):
            cfg.update({k: v for k, v in stored.items() if k in DEFAULTS})
    except FileNotFoundError:
        save(cfg)
    except (OSError, ValueError):
        pass  # corrupt config: run on defaults, don't overwrite the user's file
    return cfg


def save(cfg):
    tmp = config_path() + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)
    os.replace(tmp, config_path())
