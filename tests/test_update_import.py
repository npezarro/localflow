import hashlib
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from localflow import backup, paths, update

PAYLOAD = b"x" * 300000


class H(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Length", str(len(PAYLOAD)))
        self.end_headers()
        self.wfile.write(PAYLOAD)

    def log_message(self, *a):
        pass


@pytest.fixture()
def server():
    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield "http://127.0.0.1:%d/LocalFlow-Setup-x64.exe" % srv.server_address[1]
    srv.shutdown()


def test_versions():
    assert update.is_newer("0.10.0", "0.9.1") and update.is_newer("v1.0", "0.99.9")
    assert not update.is_newer("0.9.1", "0.9.1") and not update.is_newer("0.9.0", "0.9.1")
    assert update.parse("v0.9.1-beta") == (0, 9, 1)


def test_download_verifies_github_checksum(server, tmp_path):
    good = {"name": "LocalFlow-Setup-x64.exe", "browser_download_url": server, "size": len(PAYLOAD),
            "digest": "sha256:" + hashlib.sha256(PAYLOAD).hexdigest()}
    path = update.download(good, str(tmp_path))
    assert open(path, "rb").read() == PAYLOAD
    bad = dict(good, digest="sha256:" + "0" * 64)
    with pytest.raises(RuntimeError, match="checksum"):
        update.download(bad, str(tmp_path))
    assert not os.path.exists(path)  # a tampered download is deleted, never run


def test_portable_script_never_touches_data():
    script = update.portable_script(r"C:\t\new\LocalFlow", r"D:\Apps\LocalFlow", 1234, r"D:\Apps\LocalFlow\LocalFlow.exe")
    assert "/XD data" in script and 'PID eq 1234' in script and "start" in script
    mac = update.mac_script("/tmp/x/LocalFlow.app", "/Applications/LocalFlow.app", 99)
    assert "kill -0 99" in mac and 'open "/Applications/LocalFlow.app"' in mac


def test_asset_for_each_install_kind():
    assert update.asset_name("installer") == "LocalFlow-Setup-x64.exe"
    assert update.asset_name("portable") == "LocalFlow-windows-x64.zip"
    assert update.asset_name("mac").startswith("LocalFlow-macos-") and update.asset_name("source") is None


@pytest.fixture()
def data(tmp_path, monkeypatch):
    d = tmp_path / "current"
    monkeypatch.setenv("LOCALFLOW_DATA_DIR", str(d))
    monkeypatch.setattr(paths, "_data_dir", None)
    paths.data_dir()
    return d


def test_import_folder_from_parent_with_models_merge(data, tmp_path):
    old = tmp_path / "OldLocalFlow" / "data"
    (old / "dictionary" / "kubernetes").mkdir(parents=True)
    (old / "dictionary" / "kubernetes" / "entry.json").write_text('{"word": "Kubernetes", "takes": [], "disabled": []}')
    (old / "config.json").write_text(json.dumps({"model": "small.en"}))
    (old / "history.jsonl").write_text('{"text": "hi"}\n')
    blob_a = old / "models" / "blobs" / "aaa"
    blob_a.parent.mkdir(parents=True)
    blob_a.write_text("small-model-weights")
    snap = old / "models" / "models--Systran--faster-whisper-small.en" / "snapshots" / "rev"
    snap.mkdir(parents=True)
    (snap / "model.bin").write_text("small-model-weights")
    # the current install already has a different model sharing the blobs/ folder
    (data / "models" / "blobs").mkdir(parents=True)
    (data / "models" / "blobs" / "bbb").write_text("base-weights")
    (data / "config.json").write_text(json.dumps({"model": "base.en"}))
    imported = backup.import_folder(str(tmp_path / "OldLocalFlow"))  # the parent folder works too
    assert "config.json" in imported and "dictionary/" in imported
    assert json.loads((data / "config.json").read_text())["model"] == "small.en"
    assert (data / "models" / "blobs" / "aaa").read_text() == "small-model-weights"  # merged in
    assert (data / "models" / "blobs" / "bbb").read_text() == "base-weights"  # kept
    assert (data / "models" / "models--Systran--faster-whisper-small.en" / "snapshots" / "rev" / "model.bin").exists()
    with pytest.raises(ValueError, match="already using"):
        backup.import_folder(str(data))
    with pytest.raises(ValueError, match="No LocalFlow data"):
        backup.import_folder(str(tmp_path / "OldLocalFlow" / "data" / "dictionary" / "kubernetes"))
