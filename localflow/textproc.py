import re

FILLERS = r"(?:um+|uh+|erm+|er|ah+|hmm+|mm+)"
_FILLER_RE = re.compile(r"(?i)(^|[\s,.;:!?])(" + FILLERS + r")(?=$|[\s,.;:!?])[,.]?")
_CAP = "\x00"  # marks where a capitalised filler was removed


def remove_fillers(text):
    def drop(m):
        return m.group(1) + (_CAP if m.group(2)[0].isupper() or m.start() == 0 else "")

    out = _FILLER_RE.sub(drop, text)
    out = re.sub(r"\s+([,.;:!?])", r"\1", out)
    out = re.sub(r"([,;:])(?=[,.;:!?])", "", out)  # "so, , okay" -> "so, okay"
    out = re.sub(r"^[\s,.;:\x00]*?(\x00?)[\s,.;:]*", r"\1", out)
    out = re.sub(r"[ \t]{2,}", " ", out)
    # Capitalise the word that followed a removed sentence-initial filler ("Um, so" -> "So").
    out = re.sub(_CAP + r"[\s,.;:]*(\w)", lambda m: m.group(1).upper(), out)
    return out.replace(_CAP, "").strip()


def apply_replacements(text, replacements):
    for spoken, written in (replacements or {}).items():
        if not spoken:
            continue
        written = written.replace("\\n", "\n")
        # Spaces in the spoken phrase also match hyphens ("kabir nets" ~ "Kabir-nets").
        body = re.escape(spoken.strip()).replace(r"\ ", r"[\s-]+")
        if written.strip() == "":  # a spoken command ("new line"): swallow the dictated period
            pattern = re.compile(r"(?i)(?<!\w)" + body + r"(?!\w)[.,]?")
        else:  # a word: keep the sentence's punctuation
            pattern = re.compile(r"(?i)(?<!\w)" + body + r"(?!\w)")
        text = pattern.sub(lambda _m: written, text)
    return re.sub(r"[ \t]*\n[ \t]*", "\n", text)


def clean(text, cfg):
    text = " ".join(text.split()) if "\n" not in text else text.strip()
    if cfg.get("remove_fillers"):
        text = remove_fillers(text)
    text = apply_replacements(text, cfg.get("replacements"))
    text = text.strip()
    if text and cfg.get("trailing_space") and not text.endswith("\n"):
        text += " "
    return text
