import pytest

from localflow import learn

UNCOMMON = {"priya", "kubernetes", "figma", "pezant"}


def uncommon(word):
    return word.lower() in UNCOMMON


@pytest.fixture()
def learner(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALFLOW_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(learn.paths, "_data_dir", None)
    return learn.Learner()


def test_repeated_uncommon_words_are_promoted_after_three_dictations(learner):
    assert learner.observe("Ask Priya about the Figma file.", uncommon) == []
    assert learner.observe("Priya said the Figma link works, priya agrees.", uncommon) == []  # once per dictation
    assert learner.vocabulary() == []
    assert learner.observe("Send it to Priya.", uncommon) == ["Priya"]
    assert learner.vocabulary() == ["Priya"]  # Figma only seen twice
    assert "meeting" not in learner.data["terms"]  # common words never counted


def test_correction_learns_replacement_and_vocabulary(learner):
    pairs = learner.learn_correction("Push the Kabir nets config to staging.",
                                     "Push the Kubernetes config to staging.", uncommon)
    assert pairs == [("Kabir nets", "Kubernetes")]
    assert learner.replacements() == {"kabir nets": "Kubernetes"}
    assert learner.vocabulary() == ["Kubernetes"]  # corrections apply immediately
    # Punctuation/case-only edits are not "mishearings".
    assert learner.learn_correction("hello there.", "Hello there!", uncommon) == []


def test_apply_to_merges_but_user_entries_win(learner):
    learner.learn_correction("the kabir nets thing", "the Kubernetes thing", uncommon)
    cfg = {"vocabulary": ["LocalFlow"], "replacements": {"kabir nets": "K8s"}}
    merged = learner.apply_to(cfg)
    assert merged["vocabulary"] == ["LocalFlow", "Kubernetes"]
    assert merged["replacements"]["kabir nets"] == "K8s"
    assert cfg["vocabulary"] == ["LocalFlow"]  # the user's settings are not modified


def test_forget_is_sticky_and_state_persists(learner, tmp_path):
    for _ in range(3):
        learner.observe("Pezant rocks", uncommon)
    assert learner.vocabulary() == ["Pezant"]
    learner.remove("term", "pezant")
    learner.observe("Pezant again", uncommon)
    assert learner.vocabulary() == []  # removed terms don't come back by repetition
    again = learn.Learner()  # reload from data/learned.json
    assert again.vocabulary() == [] and "pezant" in again.data["terms"]
    again.clear()
    assert learn.Learner().entries() == []
