import pytest

from localflow import feedback

LAST = "Hey Maria, can you push the Kabir nets config to staging before Tuesday's standup?"


@pytest.mark.parametrize("spoken,expect", [
    ("Kabir nets should be Kubernetes", "Hey Maria, can you push the Kubernetes config to staging before Tuesday's standup?"),
    ("change Tuesday's to Wednesday's.", "Hey Maria, can you push the Kabir nets config to staging before Wednesday's standup?"),
    ("replace Maria with Priya", "Hey Priya, can you push the Kabir nets config to staging before Tuesday's standup?"),
    ("It's Priya, not Maria.", "Hey Priya, can you push the Kabir nets config to staging before Tuesday's standup?"),
    ("Kubernetes is spelled K U B E R N E T E S", "Hey Maria, can you push the Kubernetes config to staging before Tuesday's standup?"),
    ("spell it K-U-B-E-R-N-E-T-E-S", "Hey Maria, can you push the Kubernetes config to staging before Tuesday's standup?"),
    ("Kubernetes", "Hey Maria, can you push the Kubernetes config to staging before Tuesday's standup?"),
])
def test_spoken_corrections(spoken, expect):
    new, how = feedback.correct(LAST, spoken)
    assert new == expect, how


def test_keeps_sentence_punctuation_on_fuzzy_replace():
    new, _ = feedback.correct("We deployed it to Cabernets.", "Kubernetes")
    assert new == "We deployed it to Kubernetes."


def test_refuses_what_it_cannot_place_or_understand():
    assert feedback.correct(LAST, "Zanzibar")[0] is None  # nothing sounds like it
    assert feedback.correct(LAST, "please make the whole thing more formal and add a greeting at the end")[0] is None


def test_spelled_out():
    assert feedback.spelled_out("it's P R I Y A") == "Priya"
    assert feedback.spelled_out("no letters here at all") is None
