"""Download a faster-whisper model into build/models/<name> so it ships inside the zip."""
import os
import sys

from faster_whisper import download_model

name = sys.argv[1] if len(sys.argv) > 1 else "base.en"
out = os.path.join(os.path.dirname(__file__), "..", "build", "models", name)
download_model(name, output_dir=out)
print("model ready:", os.path.abspath(out), sorted(os.listdir(out)))
