import json
import os
import zipfile

import numpy as np
import pytest

from localflow import backup, paths
from localflow.dictionary import Dictionary, normalize

SR = 16000
tone = np.sin(np.linspace(0, 200, SR)).astype(np.float32) * 0.3
COMMON = {"sigma", "the", "and"}


def common(phrase):
    return all(w in COMMON for w in phrase.split())


@pytest.fixture()
def data(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALFLOW_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(paths, "_data_dir", None)
    return tmp_path


def test_takes_become_replacements_and_hints(data):
    d = Dictionary()
    d.add_take("Kubernetes", tone, "Kabir nets.", common)
    d.add_take("Kubernetes", tone, "Cooper Netties", common)
    d.add_take("Kubernetes", tone, "Kubernetes", common)  # heard right: no rule needed
    assert d.replacements() == {"kabir nets": "Kubernetes", "cooper netties": "Kubernetes"}
    assert d.vocabulary() == ["Kubernetes"]
    folder = d.entries["kubernetes"]["_dir"]
    assert sorted(os.listdir(folder)) == ["entry.json", "take-1.wav", "take-2.wav", "take-3.wav"]


def test_common_word_hearing_starts_off_and_can_be_enabled(data):
    d = Dictionary()
    d.add_take("Figma", tone, "Sigma", common)
    assert d.replacements() == {} and d.vocabulary() == ["Figma"]  # hint only
    d.set_variant("Figma", "Sigma", enabled=True)
    assert d.replacements() == {"sigma": "Figma"}


def test_recheck_rederives_from_recordings_and_state_persists(data):
    d = Dictionary()
    d.add_take("Priya", tone, "Pria", common)
    d.recheck("Priya", lambda samples: "Preeya" if len(samples) == SR else "?")
    again = Dictionary()  # reload from disk
    assert again.replacements() == {"preeya": "Priya"}
    again.delete_take("Priya", 1)
    assert Dictionary().replacements() == {}
    Dictionary().delete("Priya")
    assert Dictionary().entries == {}


def test_apply_to_user_entries_win(data):
    d = Dictionary()
    d.add_take("Kubernetes", tone, "Kabir nets", common)
    merged = d.apply_to({"vocabulary": ["LocalFlow"], "replacements": {"kabir nets": "K8s"}})
    assert merged["vocabulary"] == ["LocalFlow", "Kubernetes"]
    assert merged["replacements"]["kabir nets"] == "K8s"


def test_normalize():
    assert normalize("  Kabir-nets, ") == "kabir-nets"
    assert normalize("O'Brien!") == "o'brien"


def test_backup_round_trip(data, tmp_path):
    d = Dictionary()
    d.add_take("Kubernetes", tone, "Kabir nets", common)
    (data / "config.json").write_text(json.dumps({"hotkey": "ctrl+alt"}))
    (data / "history.jsonl").write_text('{"text": "hi"}\n')
    os.makedirs(data / "models" / "big")
    (data / "models" / "big" / "model.bin").write_text("x" * 1000)
    zip_path = tmp_path.parent / "backup.zip"
    assert backup.export(str(zip_path)) == 4  # config, history, entry.json, take-1.wav
    with zipfile.ZipFile(zip_path) as z:
        assert not any(n.startswith("models") for n in z.namelist())  # models re-download
    # wipe, then restore
    d.delete("Kubernetes")
    (data / "config.json").write_text("{}")
    restored = backup.restore(str(zip_path))
    assert "dictionary/" in restored and "config.json" in restored
    assert json.loads((data / "config.json").read_text())["hotkey"] == "ctrl+alt"
    assert Dictionary().replacements() == {"kabir nets": "Kubernetes"}


def test_restore_rejects_foreign_or_unsafe_zips(data, tmp_path):
    plain = tmp_path.parent / "plain.zip"
    with zipfile.ZipFile(plain, "w") as z:
        z.writestr("config.json", "{}")
    with pytest.raises(ValueError, match="isn't a LocalFlow backup"):
        backup.restore(str(plain))
    evil = tmp_path.parent / "evil.zip"
    with zipfile.ZipFile(evil, "w") as z:
        z.writestr(backup.MANIFEST, "{}")
        z.writestr("../../outside.txt", "gotcha")
    with pytest.raises(ValueError, match="Unsafe path"):
        backup.restore(str(evil))


def test_take_inside_a_sentence_only_yields_the_word_part():
    from localflow.dictionary import variant_for

    assert variant_for("We run it on Kubernetes.", "Kubernetes") is None  # heard right
    assert variant_for("We run it on Cabernets today.", "Kubernetes") == "cabernets"
    assert variant_for("Kabir nets", "Kubernetes") == "kabir nets"
    assert variant_for("Deploy the Kabir nets cluster now", "Kubernetes") == "kabir nets"
    assert variant_for("completely unrelated words here", "Kubernetes") is None


def test_apostrophe_hearing_covers_plain_form(data):
    d = Dictionary()
    d.add_take("Kubernetes", tone, "Cabernet's", common)
    assert d.replacements() == {"cabernet's": "Kubernetes", "cabernets": "Kubernetes"}
