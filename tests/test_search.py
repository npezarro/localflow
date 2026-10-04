import pytest

from localflow.search import fuzzy_match


@pytest.mark.parametrize("query,text", [
    ("hotky", "Hotkey Record…"),                                   # typo
    ("microfone", "Microphone Input System default"),               # typo
    ("gpu", "Processor Auto (GPU when available) CPU only"),
    ("shortcut", "Paste last transcript Record… hotkey"),           # synonym
    ("large-v3-turbo", "Local model base.en small.en large-v3-turbo"),  # dropdown option
    ("sync", "Account Sign in with Google…"),                       # synonym
    ("test mic", "Test microphone"),                                 # every word, any order
])
def test_matches(query, text):
    assert fuzzy_match(query, text)


@pytest.mark.parametrize("query,text", [("xyzzy", "Hotkey"), ("gpu mic", "Processor GPU")])
def test_rejects(query, text):
    assert not fuzzy_match(query, text)
