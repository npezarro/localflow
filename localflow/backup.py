"""Back up / restore everything you've taught LocalFlow as one zip, to move it between
installs (portable <-> installed, one computer to another).

Included: config.json, history.jsonl, learned.json, seen_apps.json, dictionary/ (with
your recordings). Not included: downloaded models (they re-download) and API keys (they
live in the system keychain; re-enter them after restoring on a new computer).
"""
import json
import os
import shutil
import tempfile
import time
import zipfile

from . import __version__, paths

FILES = ["config.json", "history.jsonl", "learned.json", "seen_apps.json"]
FOLDERS = ["dictionary"]
MANIFEST = "localflow-backup.json"


def export(dest):
    root = paths.data_dir()
    count = 0
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(MANIFEST, json.dumps({"app": "LocalFlow", "version": __version__, "created": time.time()}))
        for name in FILES:
            path = os.path.join(root, name)
            if os.path.isfile(path):
                z.write(path, name)
                count += 1
        for folder in FOLDERS:
            base = os.path.join(root, folder)
            for dirpath, _dirs, files in os.walk(base):
                for fname in files:
                    full = os.path.join(dirpath, fname)
                    z.write(full, os.path.relpath(full, root).replace(os.sep, "/"))
                    count += 1
    return count


def restore(src):
    """Replace the current data with the backup's. Returns the list of restored paths."""
    root = paths.data_dir()
    with zipfile.ZipFile(src) as z:
        names = z.namelist()
        if MANIFEST not in names:
            raise ValueError("This isn't a LocalFlow backup (no %s inside)." % MANIFEST)
        for name in names:
            norm = os.path.normpath(name)
            if norm.startswith("..") or os.path.isabs(norm):
                raise ValueError("Unsafe path in backup: %s" % name)
        with tempfile.TemporaryDirectory() as tmp:
            z.extractall(tmp)
            restored = []
            for name in FILES:
                src_path = os.path.join(tmp, name)
                if os.path.isfile(src_path):
                    shutil.copy2(src_path, os.path.join(root, name))
                    restored.append(name)
            for folder in FOLDERS:
                src_dir = os.path.join(tmp, folder)
                if os.path.isdir(src_dir):
                    dest = os.path.join(root, folder)
                    shutil.rmtree(dest, ignore_errors=True)
                    shutil.copytree(src_dir, dest)
                    restored.append(folder + "/")
    return restored
