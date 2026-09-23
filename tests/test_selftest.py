import json
import os

from localflow import selftest

ROOT = os.path.dirname(os.path.dirname(__file__))


def test_transcribes_sample(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALFLOW_DATA_DIR", os.path.join(ROOT, "data"))
    out = tmp_path / "r.json"
    code = selftest.run(["--selftest", os.path.join(ROOT, "samples", "jfk.wav"), "--out", str(out),
                         "--model", os.environ.get("LOCALFLOW_TEST_MODEL", "tiny.en")])
    result = json.loads(out.read_text())
    assert code == 0, result
    assert "ask not what your country" in result["text"].lower()
