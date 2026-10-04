"""Fuzzy matching for the Settings search box."""
import difflib
import re

# Words people search for that the settings call something else.
SYNONYMS = {
    "shortcut": ["hotkey"], "shortcuts": ["hotkey"], "keyboard": ["hotkey"], "key": ["hotkey"],
    "mic": ["microphone"], "audio": ["microphone"], "gpu": ["gpu", "processor"], "nvidia": ["gpu"],
    "cuda": ["gpu"], "mlx": ["apple gpu"], "speed": ["faster", "processor", "search"], "fast": ["faster"],
    "ai": ["clean-up"], "cleanup": ["clean-up"], "llm": ["clean-up"], "claude": ["clean-up"],
    "chatgpt": ["clean-up"], "openai": ["api"], "sync": ["account"], "login": ["sign in"],
    "signin": ["sign in"], "google": ["account"], "backup": ["back up"], "export": ["back up"],
    "update": ["updates"], "version": ["updates"], "sound": ["sounds"], "beep": ["sounds"],
    "paste": ["paste"], "clipboard": ["clipboard"], "learn": ["learn"], "words": ["vocabulary"],
    "spelling": ["vocabulary"], "live": ["live"], "typing": ["live typing"], "language": ["language"],
}


def fuzzy_match(query, text):
    """Every word of ``query`` must appear in ``text``: as part of a word, through a synonym, or
    as a close misspelling ("hotky", "microfone")."""
    text = text.lower()
    words = re.findall(r"[a-z0-9][a-z0-9.\-]*", text)
    for token in query.lower().split():
        options = [token] + SYNONYMS.get(token, [])
        if not any(opt in text or (len(opt) >= 4 and any(
                difflib.SequenceMatcher(None, opt, w).ratio() >= 0.8 for w in words if abs(len(w) - len(opt)) <= 3))
                for opt in options):
            return False
    return True
