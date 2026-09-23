from localflow.textproc import apply_replacements, clean, remove_fillers


def test_remove_fillers():
    assert remove_fillers("Um, so I think, uh, we should go.") == "So I think, we should go."
    assert remove_fillers("I was, um, thinking") == "I was, thinking"
    assert remove_fillers("The umbrella is here") == "The umbrella is here"
    assert remove_fillers("Uh. Okay then.") == "Okay then."


def test_replacements():
    assert apply_replacements("Email me at my email.", {"my email": "nick@example.com"}) == \
        "Email me at nick@example.com"
    assert apply_replacements("Hello new line world", {"new line": "\\n"}) == "Hello\nworld"


def test_clean_trailing_space():
    cfg = {"remove_fillers": True, "trailing_space": True, "replacements": {}}
    assert clean("  hello   world. ", cfg) == "hello world. "
    assert clean("", cfg) == ""
