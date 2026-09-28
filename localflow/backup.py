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


def find_data_folder(folder):
    """The LocalFlow data folder at or inside ``folder`` (accepts the data folder itself or
    the LocalFlow folder that contains it), or None."""
    markers = FILES + FOLDERS + ["models"]
    for candidate in (folder, os.path.join(folder, "data")):
        if os.path.isdir(candidate) and any(os.path.exists(os.path.join(candidate, m)) for m in markers):
            return candidate
    return None


def import_folder(folder):
    """Bring in settings, history, learned words, dictionary and downloaded models from
    another LocalFlow data folder. Settings/history/learned/dictionary replace the current
    ones (like Restore); models are added if missing. Returns the list of imported items."""
    src = find_data_folder(folder)
    if not src:
        raise ValueError("No LocalFlow data found in that folder. Pick a folder that contains "
                         "config.json, history.jsonl or a dictionary folder (or the LocalFlow "
                         "folder that holds a data folder).")
    root = paths.data_dir()
    if os.path.realpath(src) == os.path.realpath(root):
        raise ValueError("That's the folder LocalFlow is already using.")
    imported = []
    for name in FILES:
        path = os.path.join(src, name)
        if os.path.isfile(path):
            shutil.copy2(path, os.path.join(root, name))
            imported.append(name)
    for name in FOLDERS:
        path = os.path.join(src, name)
        if os.path.isdir(path):
            dest = os.path.join(root, name)
            shutil.rmtree(dest, ignore_errors=True)
            shutil.copytree(path, dest)
            imported.append(name + "/")
    models = os.path.join(src, "models")
    if os.path.isdir(models):
        dest_models = os.path.join(root, "models")
        os.makedirs(dest_models, exist_ok=True)
        # Downloaded models use a shared-blob layout (models--*/snapshots -> blobs/), so merge
        # file by file (keeping links) instead of skipping folders that already exist.
        added = []

        def copy_if_missing(a, b):
            if not os.path.exists(b):
                shutil.copy2(a, b)
                added.append(b)
            return b

        for entry in os.listdir(models):
            source, target = os.path.join(models, entry), os.path.join(dest_models, entry)
            if os.path.isdir(source):
                shutil.copytree(source, target, symlinks=True, dirs_exist_ok=True, copy_function=copy_if_missing)
            else:
                copy_if_missing(source, target)
        if added:
            imported.append("model files (%d)" % len(added))
    return imported
