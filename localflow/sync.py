"""Account sync: your learnings and pronunciation dictionary follow you to every device you
sign in on; settings are kept per device, and a new device can copy another's.

In the account (Drive app-data folder):
    learned.json               merged learnings (newest change per entry wins, deletions kept)
    dictionary.json            {word: entry} with each take's content hash
    take-<hash>.wav            voice recordings, stored once by content
    device-<id>.json           each device's name, platform and settings
Transcripts never leave the device.
"""
import json
import logging
import os
import platform
import sys
import threading
import time
import uuid

from . import __version__, paths

log = logging.getLogger(__name__)

# Settings that describe this machine rather than how you like to dictate: never copied.
DEVICE_ONLY = {"input_device", "device", "claude_path", "codex_path", "last_update_check", "setup_seen",
               "polish_verified", "account_email", "device_name", "last_sync", "auto_update_check",
               "copied_settings_once", "gpu_offer_declined"}
# Hotkeys use different keys on Windows and macOS: only copied between the same kind of computer.
HOTKEYS = {"hotkey", "paste_last_hotkey", "feedback_hotkey", "live_hotkey", "live_pause_hotkey", "lock_key"}


def device_id():
    """A random id for this installation (kept out of backups/imports so copies differ)."""
    path = os.path.join(paths.data_dir(), "device-id")
    try:
        with open(path, encoding="utf-8") as f:
            value = f.read().strip()
        if value:
            return value
    except OSError:
        pass
    value = uuid.uuid4().hex[:12]
    with open(path, "w", encoding="utf-8") as f:
        f.write(value)
    return value


def platform_name():
    return "mac" if sys.platform == "darwin" else "windows" if sys.platform == "win32" else sys.platform


def default_device_name():
    node = platform.node().split(".")[0] or "This computer"
    return "%s (%s)" % (node, {"mac": "Mac", "windows": "Windows"}.get(platform_name(), platform_name()))


def settings_to_copy(theirs, their_platform, mine):
    """Their settings applied over mine, minus machine-specific ones. -> (new cfg, skipped keys)."""
    new, skipped = dict(mine), []
    for key, value in theirs.items():
        if key not in mine or key in DEVICE_ONLY:
            continue
        if key in HOTKEYS and their_platform != platform_name():
            skipped.append(key)
            continue
        new[key] = value
    return new, skipped


def merge_dictionary(local, local_deleted, remote):
    """-> (merged {key: entry}, merged deletions, {key: "pull"|"delete"} actions for this device)."""
    remote_entries = remote.get("entries") or {}
    deleted = dict(remote.get("deleted") or {})
    for k, t in local_deleted.items():
        deleted[k] = max(deleted.get(k, 0), t)
    merged, actions = {}, {}
    for key in set(local) | set(remote_entries):
        mine, theirs = local.get(key), remote_entries.get(key)
        newest = mine if theirs is None or (mine is not None and mine.get("t", 0) >= theirs.get("t", 0)) else theirs
        if key in deleted and deleted[key] >= newest.get("t", 0):
            if mine is not None:
                actions[key] = "delete"
            continue
        deleted.pop(key, None)
        merged[key] = newest
        if newest is theirs and theirs is not mine:
            actions[key] = "pull"
    return merged, deleted, actions


class Sync:
    """Runs sync in the background; everything touching Drive happens on its own thread."""

    def __init__(self, app, folder_factory):
        self.app = app
        self.folder_factory = folder_factory  # () -> drive.AppFolder
        self._lock = threading.Lock()
        self._dirty = threading.Event()
        self.last_error = None
        self.last_sync = 0.0
        self.remote_devices = []

    def mark_dirty(self):
        self._dirty.set()

    def start(self, period=600, debounce=20):
        def loop():
            next_full = time.monotonic() + 5
            while True:
                self._dirty.wait(timeout=max(1.0, next_full - time.monotonic()))
                if self._dirty.is_set():
                    time.sleep(debounce)  # let a burst of changes settle
                    self._dirty.clear()
                if self.app.account_ready():
                    self.sync_now()
                next_full = time.monotonic() + period

        threading.Thread(target=loop, daemon=True, name="account-sync").start()

    # ------------------------------------------------------------------ one sync
    def sync_now(self):
        """Pull, merge, push. Returns an error message or None."""
        with self._lock:
            try:
                folder = self.folder_factory()
                files = folder.list()
                self._sync_learned(folder, files)
                self._sync_dictionary(folder, files)
                self._sync_device(folder, files)
                self.last_sync, self.last_error = time.time(), None
                log.info("account sync done (%d devices)", len(self.remote_devices))
            except Exception as exc:
                self.last_error = str(exc)
                log.warning("account sync failed: %s", exc)
            return self.last_error

    def _json(self, folder, files, name, default):
        f = files.get(name)
        return json.loads(folder.download(f["id"])) if f else default

    def _put_json(self, folder, files, name, data):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        f = files.get(name)
        files[name] = {"id": folder.upload(name, body, f["id"] if f else None, "application/json")}

    def _sync_learned(self, folder, files):
        remote = self._json(folder, files, "learned.json", {})
        before = json.dumps(self.app.learner.data, sort_keys=True)
        merged = self.app.learner.merge_from(remote)
        if json.dumps(merged, sort_keys=True) != before:
            self.app.ui_q.put(("call", self.app.settings.refresh_learned))
        if merged != remote:
            self._put_json(folder, files, "learned.json", merged)

    def _sync_dictionary(self, folder, files):
        dic = self.app.dictionary
        remote = self._json(folder, files, "dictionary.json", {})
        local = dic.export_state()
        merged, deleted, actions = merge_dictionary(local, dic.deleted(), remote)

        def fetch(h):
            name = "take-%s.wav" % h
            if name not in files:
                raise RuntimeError("recording %s missing from the account" % h)
            return folder.download(files[name]["id"])

        for key, action in actions.items():
            if action == "pull":
                dic.apply_remote(key, merged[key], fetch)
            else:
                dic.remove_synced(key, deleted.get(key))
        for key, entry in merged.items():  # upload recordings the account doesn't have yet
            for take in entry.get("takes", []):
                name = "take-%s.wav" % take["h"]
                if name not in files:
                    path = dic.take_file(key, take["h"])
                    if path and os.path.exists(path):
                        with open(path, "rb") as f:
                            files[name] = {"id": folder.upload(name, f.read(), None, "audio/wav")}
        new_remote = {"entries": merged, "deleted": deleted}
        if new_remote != {"entries": remote.get("entries") or {}, "deleted": remote.get("deleted") or {}}:
            self._put_json(folder, files, "dictionary.json", new_remote)
        if actions:
            self.app.ui_q.put(("dictionary_changed", None, ""))

    def _sync_device(self, folder, files):
        cfg = self.app.cfg
        mine = {"id": device_id(), "name": cfg.get("device_name") or default_device_name(),
                "platform": platform_name(), "version": __version__, "updated": time.time(),
                "settings": {k: v for k, v in cfg.items() if k not in DEVICE_ONLY}}
        self._put_json(folder, files, "device-%s.json" % mine["id"], mine)
        devices = []
        for name, f in files.items():
            if name.startswith("device-") and name.endswith(".json") and name != "device-%s.json" % mine["id"]:
                try:
                    devices.append(json.loads(folder.download(f["id"])))
                except Exception:
                    log.warning("couldn't read %s", name)
        self.remote_devices = sorted(devices, key=lambda d: -d.get("updated", 0))
