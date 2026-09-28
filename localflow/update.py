"""Update LocalFlow from its GitHub releases, from inside the app.

1. Ask GitHub for the latest release (api.github.com/repos/<repo>/releases/latest).
2. Pick the file matching how this copy was installed:
     Windows installer copy  -> LocalFlow-Setup-x64.exe (upgrades in place, settings kept)
     Windows portable zip    -> LocalFlow-windows-x64.zip (files replaced, data/ kept)
     macOS (dmg or zip)      -> LocalFlow-macos-<arm64|intel>.zip (the .app is replaced)
3. Download it and check its SHA-256 against the digest GitHub publishes for the file.
4. Hand over to the installer / a small helper script that waits for LocalFlow to quit,
   swaps the files in, and starts the new version.
Your data folder is never touched by an update.
"""
import hashlib
import json
import logging
import os
import platform
import subprocess
import sys
import tempfile
import time
import urllib.request
import zipfile

from . import __version__, paths

log = logging.getLogger(__name__)
REPO = "npezarro/localflow"
API = "https://api.github.com/repos/%s/releases/latest" % REPO
RELEASES_PAGE = "https://github.com/%s/releases/latest" % REPO
IS_WIN, IS_MAC = sys.platform == "win32", sys.platform == "darwin"


def current_version():
    return os.environ.get("LOCALFLOW_PRETEND_VERSION") or __version__  # test hook


def parse(v):
    out = []
    for part in v.strip().lstrip("vV").split("."):
        digits = "".join(ch for ch in part if ch.isdigit())
        out.append(int(digits) if digits else 0)
    return tuple(out + [0] * (3 - len(out)))


def is_newer(latest, current):
    return parse(latest) > parse(current)


def install_kind():
    """'installer', 'portable', 'mac', or 'source' (running from source: open the page)."""
    if not paths.is_frozen():
        return "source"
    if IS_MAC:
        return "mac"
    if IS_WIN:
        return "installer" if os.path.exists(os.path.join(paths.install_root(), "unins000.exe")) else "portable"
    return "source"


def asset_name(kind):
    if kind == "installer":
        return "LocalFlow-Setup-x64.exe"
    if kind == "portable":
        return "LocalFlow-windows-x64.zip"
    if kind == "mac":
        return "LocalFlow-macos-%s.zip" % ("arm64" if platform.machine() == "arm64" else "intel")
    return None


def latest_release(timeout=20):
    req = urllib.request.Request(API, headers={"Accept": "application/vnd.github+json", "User-Agent": "LocalFlow"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.load(resp)
    return {"version": data["tag_name"].lstrip("vV"), "notes": (data.get("body") or "").strip(),
            "page": data.get("html_url") or RELEASES_PAGE,
            "assets": {a["name"]: a for a in data.get("assets", [])}}


def check():
    """-> dict(available, current, latest, notes, page, kind, asset) ; raises on network errors."""
    rel = latest_release()
    kind = install_kind()
    name = asset_name(kind)
    return {"available": is_newer(rel["version"], current_version()), "current": current_version(),
            "latest": rel["version"], "notes": rel["notes"], "page": rel["page"], "kind": kind,
            "asset": rel["assets"].get(name) if name else None}


def download(asset, dest_dir, progress=lambda frac: None):
    """Download a release asset and verify it against GitHub's published SHA-256."""
    path = os.path.join(dest_dir, asset["name"])
    sha = hashlib.sha256()
    req = urllib.request.Request(asset["browser_download_url"], headers={"User-Agent": "LocalFlow"})
    done, total = 0, asset.get("size") or 0
    with urllib.request.urlopen(req, timeout=120) as resp, open(path, "wb") as out:
        while True:
            block = resp.read(1 << 20)
            if not block:
                break
            out.write(block)
            sha.update(block)
            done += len(block)
            if total:
                progress(done / total)
    if total and done != total:
        raise RuntimeError("download incomplete (%d of %d bytes)" % (done, total))
    digest = asset.get("digest") or ""
    if digest.startswith("sha256:") and digest[7:] != sha.hexdigest():
        os.remove(path)
        raise RuntimeError("download failed its checksum; nothing was installed")
    return path


def _spawn_detached(cmd):
    kwargs = {"close_fds": True}
    if IS_WIN:
        kwargs["creationflags"] = 0x00000008 | 0x00000200 | 0x08000000  # DETACHED | NEW_GROUP | NO_WINDOW
    else:
        kwargs["start_new_session"] = True
    subprocess.Popen(cmd, **kwargs)


def portable_script(new_root, app_root, pid, exe):
    """Windows helper: wait for LocalFlow to exit, copy the new files over (never data\\), restart."""
    return "\r\n".join([
        "@echo off",
        ":wait",
        'tasklist /FI "PID eq %d" 2>NUL | find "%d" >NUL && (timeout /t 1 /nobreak >NUL & goto wait)' % (pid, pid),
        'robocopy "%s" "%s" /E /XD data /R:3 /W:1 /NFL /NDL /NJH /NJS >NUL' % (new_root, app_root),
        'start "" "%s"' % exe,
        'rmdir /S /Q "%s" 2>NUL' % os.path.dirname(new_root),
        "",
    ])


def mac_script(new_app, app_path, pid):
    """macOS helper: wait for LocalFlow to exit, replace the .app, restart."""
    return "\n".join([
        "#!/bin/sh",
        "while kill -0 %d 2>/dev/null; do sleep 1; done" % pid,
        'rm -rf "%s.old" && mv "%s" "%s.old" && mv "%s" "%s" && rm -rf "%s.old"' % (
            app_path, app_path, app_path, new_app, app_path, app_path),
        'open "%s"' % app_path,
        "",
    ])


def apply(info, progress=lambda msg, frac: None):
    """Download and start installing. Returns True when LocalFlow should now quit."""
    kind, asset = info["kind"], info["asset"]
    if kind == "source" or not asset:
        raise RuntimeError("automatic update isn't available for this copy; download it from %s" % info["page"])
    work = tempfile.mkdtemp(prefix="localflow-update-")
    path = download(asset, work, lambda f: progress("Downloading LocalFlow %s" % info["latest"], f))
    progress("Installing…", 1.0)
    pid = os.getpid()
    if kind == "installer":
        # Inno Setup: upgrade in place (same AppId), keep previous tasks, relaunch afterwards.
        _spawn_detached([path, "/SILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/CLOSEAPPLICATIONS", "/RELAUNCH=1"])
        return True
    if kind == "portable":
        extracted = os.path.join(work, "new")
        with zipfile.ZipFile(path) as z:
            z.extractall(extracted)
        new_root = os.path.join(extracted, "LocalFlow")
        app_root = paths.install_root()
        script = os.path.join(work, "apply-update.cmd")
        with open(script, "w") as f:
            f.write(portable_script(new_root, app_root, pid, os.path.join(app_root, "LocalFlow.exe")))
        _spawn_detached(["cmd.exe", "/c", script])
        return True
    if kind == "mac":
        extracted = os.path.join(work, "new")
        subprocess.run(["ditto", "-x", "-k", path, extracted], check=True)  # keeps symlinks + signature
        new_app = os.path.join(extracted, "LocalFlow", "LocalFlow.app")
        app_path = os.path.abspath(os.path.join(os.path.dirname(sys.executable), "..", ".."))
        script = os.path.join(work, "apply-update.sh")
        with open(script, "w") as f:
            f.write(mac_script(new_app, app_path, pid))
        os.chmod(script, 0o755)
        _spawn_detached(["/bin/sh", script])
        return True
    return False


def run_cli(out=None):
    """``--update-now``: check, and if newer, download and hand over (used by tests/CI)."""
    result = {"ok": False}
    try:
        info = check()
        result.update({k: info[k] for k in ("current", "latest", "kind", "available")})
        if info["available"]:
            result["handed_over"] = apply(info)
        result["ok"] = True
    except Exception as exc:
        result["error"] = str(exc)
    if out:
        with open(out, "w") as f:
            json.dump(result, f)
    time.sleep(0.5)
    return 0 if result["ok"] else 1
