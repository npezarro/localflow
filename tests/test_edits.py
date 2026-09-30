import time

import pytest

from localflow.edits import EditWatcher, find_edit

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
    assert got == [("id1", T, T.replace("Kabir nets", "Kubernetes"))]
