import time

import pytest

from localflow.edits import EditWatcher, classify, final_span, find_edit

T = "Please ask Maria to send the Kabir nets report by Friday."


@pytest.mark.parametrize("before,after,expected", [
    # fixed a word inside the transcript
    ("Hi team, " + T + " ", "Hi team, " + T.replace("Kabir nets", "Kubernetes") + " ",
     T.replace("Kabir nets", "Kubernetes")),
    # fixed a word AND kept typing after it: the continuation isn't part of the correction
    ("Hi team, " + T + " ", "Hi team, " + T.replace("Maria", "Priya") + " Thanks!",
     T.replace("Maria", "Priya")),
    # Windows line endings in the field
    ("Dear Sam,\r\n" + T, "Dear Sam,\r\n" + T.replace("Friday", "Thursday"), T.replace("Friday", "Thursday")),
])
def test_finds_the_corrected_transcript(before, after, expected):
    assert find_edit(before, after, T) == expected


@pytest.mark.parametrize("after", [
    "Hi team, " + T + " Also, lunch?",          # only typed after it
    "Quick note. Hi team, " + T,                # only typed before it
    "Hi team, ",                                # deleted it
    "Hi team, Let's meet next week instead to go over everything in person.",  # rewrote it
])
def test_ignores_things_that_are_not_corrections(after):
    assert find_edit("Hi team, " + T, after, T) is None


def test_watcher_reports_an_edit_from_a_field():
    class Field:
        value = "x " + T

        def read(self):
            return self.value

    field = Field()
    got = []
    w = EditWatcher(lambda *a: got.append(a), field_factory=lambda: field, watch_seconds=5, poll=0.1)
    w.watch("id1", T)
    time.sleep(0.6)
    field.value = "x " + T.replace("Kabir nets", "Kubernetes")
    time.sleep(0.3)
    w.stop()  # e.g. the next dictation started
    deadline = time.time() + 2
    while not got and time.time() < deadline:
        time.sleep(0.05)
    assert got == [("id1", T, "corrected", T.replace("Kabir nets", "Kubernetes"), None)]


@pytest.mark.parametrize("after,status,final", [
    ("Hi team, " + T + " Thanks!", "unchanged", T),
    ("Hi team, " + T.replace("Maria", "Priya"), "corrected", T.replace("Maria", "Priya")),
    ("Hi team, Let's meet next week instead to go over everything in person.", "changed",
     "Let's meet next week instead to go over everything in person."),
    ("Hi team, ", "removed", None),
])
def test_every_watch_records_what_the_transcript_became(after, status, final):
    span = final_span("Hi team, " + T, after, T)
    assert (classify(T, span), span) == (status, final)


def test_an_app_that_hides_its_text_is_reported_not_guessed():
    class Hidden:
        def read(self):
            return ""

    got = []
    w = EditWatcher(lambda *a: got.append(a), field_factory=Hidden, watch_seconds=1, poll=0.1)
    w.watch("id2", T, "Some App")
    deadline = time.time() + 5  # it retries for ~2.5 s before calling the field unreadable
    while not got and time.time() < deadline:
        time.sleep(0.05)
    assert got == [("id2", T, "unreadable", None, "Some App")]


@pytest.mark.parametrize("span", [
    T.replace("Please", "please"),                            # the app changed the capital
    T.replace("Friday.", "Friday"),                           # dropped the full stop
    T[:-1] + " and Monday.",                                  # kept writing where it ended
    "Hi Sam, " + T,                                           # added a greeting before it
])
def test_harmless_changes_are_not_corrections(span):
    assert classify(T, span) == "unchanged"
