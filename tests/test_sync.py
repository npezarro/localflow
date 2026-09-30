"""Two devices syncing through an in-memory stand-in for the Drive app-data folder."""
import json
import queue

import numpy as np
import pytest

from localflow import audio, learn, paths, sync
from localflow.dictionary import Dictionary


class FakeFolder:
    def __init__(self):
        self.files = {}  # name -> (id, bytes)

    def list(self):
        return {name: {"id": fid} for name, (fid, _b) in self.files.items()}

    def download(self, fid):
        return next(b for i, b in self.files.values() if i == fid)

    def upload(self, name, content, file_id=None, mime=None):
        fid = file_id or "id-%d" % (len(self.files) + 1)
        self.files[name] = (fid, bytes(content))
        return fid


class Device:
    def __init__(self, root, folder, name, monkeypatch):
        self.root, self.monkeypatch = str(root), monkeypatch
        root.mkdir()
        self.use()
        self.learner = learn.Learner()
        self.dictionary = Dictionary()
        self.cfg = {"device_name": name, "hotkey": "ctrl+alt", "beam_size": 1, "input_device": 3, "sounds": True}
        self.ui_q = queue.Queue()
        self.settings = type("S", (), {"refresh_learned": lambda self: None})()
        self.sync = sync.Sync(self, lambda: folder)

    def use(self):
        self.monkeypatch.setattr(paths, "_data_dir", self.root)

    def account_ready(self):
        return True

    def run(self):
        self.use()
        assert self.sync.sync_now() is None


@pytest.fixture
def two(tmp_path, monkeypatch):
    folder = FakeFolder()
    a = Device(tmp_path / "a", folder, "Desk", monkeypatch)
    b = Device(tmp_path / "b", folder, "Laptop", monkeypatch)
    return a, b, folder


def test_learnings_follow_you_and_forgetting_sticks(two):
    a, b, _ = two
    a.use()
    a.learner.learn_correction("the sigma file", "the Figma file")
    a.run()
    b.run()
    assert b.learner.replacements() == {"sigma": "Figma"}
    b.use()
    b.learner.clear()  # forget everything on the laptop
    b.run()
    a.run()
    assert a.learner.replacements() == {}  # didn't come back from the desk copy
    a.use()
    a.learner.learn_correction("the sigma file", "the Figma file")  # learned again later: wins
    a.run()
    b.run()
    assert b.learner.replacements() == {"sigma": "Figma"}


def test_dictionary_words_and_recordings_sync(two):
    a, b, folder = two
    a.use()
    a.dictionary.add_take("Kubernetes", np.zeros(1600, dtype=np.float32) + 0.01, "Kabir nets")
    a.run()
    assert sum(n.startswith("take-") for n in folder.files) == 1
    b.run()
    b.use()
    entry = b.dictionary.entries["kubernetes"]
    assert len(entry["takes"]) == 1 and entry["takes"][0]["heard"] == "Kabir nets"
    wav = b.dictionary.take_file("kubernetes", entry["takes"][0]["h"])
    assert len(audio.load_wav(wav)) == 1600
    b.dictionary.delete("Kubernetes")
    b.run()
    a.run()
    assert "kubernetes" not in a.dictionary.entries
    a.run()  # stable: nothing reappears
    assert "kubernetes" not in a.dictionary.entries


def test_settings_stay_per_device_and_can_be_copied(two):
    a, b, folder = two
    a.run()
    b.run()
    assert [d["name"] for d in b.sync.remote_devices] == ["Desk"]
    assert b.cfg["device_name"] == "Laptop"  # nothing copied on its own
    desk = b.sync.remote_devices[0]
    desk["settings"]["beam_size"] = 5
    new, skipped = sync.settings_to_copy(desk["settings"], desk["platform"], b.cfg)
    assert new["beam_size"] == 5 and new["input_device"] == 3 and new["device_name"] == "Laptop"
    other_os, skipped = sync.settings_to_copy(desk["settings"], "somethingelse", b.cfg)
    assert "hotkey" in skipped
    assert json.loads(folder.files["device-%s.json" % sync.device_id()][1])["name"] == "Laptop"


def test_sign_in_flow_keeps_the_refresh_token_in_the_credential_store(monkeypatch):
    import base64
    import threading
    import urllib.parse
    import urllib.request

    from localflow import google_auth, keystore

    store = {}
    monkeypatch.setattr(keystore, "set", lambda k, v: store.__setitem__(k, v))
    monkeypatch.setattr(keystore, "get", lambda k: store.get(k, ""))
    monkeypatch.setattr(google_auth, "client", lambda: ("cid", "csecret"))
    posted = {}
    claims = base64.urlsafe_b64encode(json.dumps({"email": "me@example.com"}).encode()).decode().rstrip("=")

    def fake_post(url, fields, timeout=30):
        posted.update(fields)
        return {"access_token": "at", "expires_in": 3600, "refresh_token": "rt", "id_token": "h." + claims + ".s"}

    monkeypatch.setattr(google_auth, "_post", fake_post)

    def browser(url):  # the user approves: Google redirects back to our one-off local address
        q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        assert q["scope"][0] == google_auth.SCOPES and q["code_challenge_method"][0] == "S256"
        back = q["redirect_uri"][0] + "?" + urllib.parse.urlencode({"code": "c0de", "state": q["state"][0]})
        threading.Thread(target=lambda: urllib.request.urlopen(back, timeout=5).read()).start()

    assert google_auth.sign_in(open_browser=browser, timeout=10) == "me@example.com"
    assert store[google_auth.REFRESH_KEY] == "rt"
    assert posted["code"] == "c0de" and posted["code_verifier"] and posted["grant_type"] == "authorization_code"
    assert google_auth.Session.shared().token() == "at"
