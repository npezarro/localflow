import json
import os
import stat
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import numpy as np
import pytest

from localflow import audio, cloud, pipeline, polish


class Handler(BaseHTTPRequestHandler):
    seen = []

    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"]))
        Handler.seen.append((self.path, dict(self.headers), body))
        if self.path.endswith("/audio/transcriptions"):
            out = {"text": " Hello from the cloud. "}
        else:
            out = {"choices": [{"message": {"content": "Cleaned text here."}}]}
        data = json.dumps(out).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass


@pytest.fixture()
def server():
    Handler.seen = []
    srv = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield "http://127.0.0.1:%d/v1" % srv.server_address[1]
    srv.shutdown()


def test_cloud_transcribe_multipart(server):
    samples = np.zeros(16000, dtype=np.float32)
    text = cloud.transcribe(samples, server, "whisper-x", "k123", "en", ["Kubernetes"])
    assert text == "Hello from the cloud."
    path, headers, body = Handler.seen[0]
    assert path == "/v1/audio/transcriptions"
    assert headers["Authorization"] == "Bearer k123"
    assert b'name="model"\r\n\r\nwhisper-x' in body
    assert b'name="language"\r\n\r\nen' in body
    assert b"Vocabulary: Kubernetes." in body
    assert b'filename="speech.wav"' in body and b"RIFF" in body


def test_cloud_chat(server):
    assert cloud.chat("sys", "user text", server, "m", "") == "Cleaned text here."
    payload = json.loads(Handler.seen[0][2])
    assert payload["messages"][1]["content"] == "user text"


def test_cloud_http_error_is_readable():
    with pytest.raises(cloud.CloudError, match="cannot reach"):
        cloud.chat("s", "u", "http://127.0.0.1:9/v1", "m", "", timeout=2)


class FakeTranscriber:
    model_name = "fake"

    def __init__(self):
        self.ready = threading.Event()
        self.ready.set()

    def transcribe(self, audio, language, vocabulary, beam):
        return "um so local text works fine"


def test_cloud_falls_back_to_local():
    cfg = {"engine": "cloud", "cloud_provider": "custom", "cloud_base_url": "http://127.0.0.1:9/v1",
           "cloud_model": "", "cloud_fallback_local": True, "polish": "off", "remove_fillers": True,
           "trailing_space": False, "replacements": {}}
    r = pipeline.process(np.zeros(8000, dtype=np.float32), cfg, FakeTranscriber(), get_key=lambda k: "")
    assert r["engine"] == "local:fake"
    assert "Cloud failed" in r["note"]
    assert r["text"] == "So local text works fine"


def test_cloud_pipeline_uses_api(server):
    cfg = {"engine": "cloud", "cloud_provider": "custom", "cloud_base_url": server, "cloud_model": "w",
           "polish": "off", "remove_fillers": False, "trailing_space": True, "replacements": {}}
    r = pipeline.process(np.zeros(8000, dtype=np.float32), cfg, FakeTranscriber(), get_key=lambda k: "")
    assert r["engine"] == "cloud:w" and r["text"] == "Hello from the cloud. "


@pytest.mark.skipif(sys.platform == "win32", reason="shell-script fake CLI")
def test_claude_polish_via_cli(tmp_path):
    fake = tmp_path / "claude"
    fake.write_text("#!/bin/sh\necho \"$@\" > \"$(dirname \"$0\")/args.txt\"\n"
                    "cat > \"$(dirname \"$0\")/stdin.txt\"\necho 'So local text works fine.'\n")
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    cfg = {"polish": "claude", "claude_path": str(fake), "claude_model": "sonnet", "vocabulary": ["Priya"],
           "polish_min_words": 3, "remove_fillers": True, "trailing_space": True, "replacements": {},
           "engine": "local"}
    polish._found.clear()
    r = pipeline.process(np.zeros(8000, dtype=np.float32), cfg, FakeTranscriber(), get_key=lambda k: "")
    assert r["polished"] and r["text"] == "So local text works fine. "
    assert r["raw"] == "um so local text works fine"
    args = (tmp_path / "args.txt").read_text()
    assert "--setting-sources" in args and "--model sonnet" in args and "Priya" in args
    assert (tmp_path / "stdin.txt").read_text() == "So local text works fine"


def test_polish_rejects_answers():
    assert not polish.plausible("what is the capital of France",
                                "The capital of France is Paris, a city famous for the Eiffel Tower, "
                                "art museums, cafes and a long history as a political center.")
    assert polish.plausible("um so we meet tuesday", "So we meet Tuesday.")


def test_polish_failure_keeps_raw_text(tmp_path):
    cfg = {"polish": "claude", "claude_path": str(tmp_path / "missing-claude"), "polish_min_words": 1,
           "remove_fillers": False, "trailing_space": False, "replacements": {}, "engine": "local"}
    polish._found.clear()
    r = pipeline.process(np.zeros(8000, dtype=np.float32), cfg, FakeTranscriber(), get_key=lambda k: "")
    assert not r["polished"] and r["text"] == "um so local text works fine"
    assert "AI clean-up failed" in r["note"]


def test_recorder_preroll_and_resample():
    rec = audio.Recorder(warm=True, preroll=0.5, tail=0)
    rec._rate = 48000
    rec._fake = None
    rec._stream = type("S", (), {"active": True})()  # pretend the stream is open
    block = np.ones((1440, 1), dtype=np.float32) * 0.1  # 30 ms at 48 kHz
    for _ in range(50):  # 1.5 s of audio before the hotkey
        rec._callback(block, 1440, None, None)
    rec.start()
    for _ in range(10):  # 0.3 s while recording
        rec._callback(block, 1440, None, None)
    out = rec.stop()
    assert abs(len(out) / 16000 - 0.8) < 0.05  # 0.5 s pre-roll + 0.3 s speech, resampled to 16 kHz


def test_normalize_boosts_quiet_audio():
    quiet = np.sin(np.linspace(0, 100, 16000)).astype(np.float32) * 0.02
    assert abs(float(np.max(np.abs(audio.normalize(quiet)))) - 0.24) < 0.01  # capped at 12x
    loud = quiet * 40
    assert np.allclose(audio.normalize(loud), loud)


def test_mac_monitor_coordinates():
    from localflow import monitors

    # Primary 1440x900 at origin; second display to the right and 200 px higher (Cocoa y-up).
    frames = [(0, 0, 1440, 900), (1440, 200, 1920, 1080)]
    assert monitors.pick_screen((2000, 800), frames) == 1
    assert monitors.pick_screen((100, 100), frames) == 0
    assert monitors.pick_screen((-5000, 0), frames) == 0
    # Second screen's visible frame (below its menu bar) -> Tk top-left coords.
    assert monitors.mac_to_tk((1440, 200, 1920, 1055), 900) == (1440, -355, 1920, 1055)
