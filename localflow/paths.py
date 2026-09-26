"""Where LocalFlow keeps its files (config, history, downloaded models, log).

Portable copy (the zip): it ships with a ``data`` folder next to the app, and that
folder's presence is what makes it portable: everything stays inside the unzipped
folder. Installed copy (Windows installer, .dmg dragged to Applications): there is no
``data`` folder, so files go to the per-user folder (%LOCALAPPDATA%/LocalFlow on Windows,
~/Library/Application Support/LocalFlow). A portable folder that isn't writable
(read-only media, macOS App Translocation) also falls back to the per-user folder.
"""
import os
import sys

APP_NAME = "LocalFlow"


def is_frozen():
    return getattr(sys, "frozen", False)


def install_root():
    """The folder the user unzipped into."""
    if is_frozen():
        exe_dir = os.path.dirname(os.path.abspath(sys.executable))
        if sys.platform == "darwin" and ".app/Contents/MacOS" in exe_dir:
            # LocalFlow/LocalFlow.app/Contents/MacOS/LocalFlow -> LocalFlow/
            return os.path.abspath(os.path.join(exe_dir, "..", "..", ".."))
        return exe_dir
    return os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def bundle_dir():
    """Where read-only bundled resources (the default model) live."""
    if is_frozen():
        return getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    return install_root()


def is_portable():
    return data_dir() == os.path.join(install_root(), "data")


def is_translocated():
    return sys.platform == "darwin" and "/AppTranslocation/" in install_root()


def _writable(path):
    try:
        os.makedirs(path, exist_ok=True)
        probe = os.path.join(path, ".write-test")
        with open(probe, "w") as f:
            f.write("ok")
        os.remove(probe)
        return True
    except OSError:
        return False


def _user_data_dir():
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    elif sys.platform == "darwin":
        base = os.path.expanduser("~/Library/Application Support")
    else:
        base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    return os.path.join(base, APP_NAME)


_data_dir = None


def data_dir():
    global _data_dir
    if _data_dir is None:
        override = os.environ.get("LOCALFLOW_DATA_DIR")
        portable = os.path.join(install_root(), "data")
        candidate = override or portable
        if not override and (not os.path.isdir(portable) or is_translocated() or not _writable(portable)):
            candidate = _user_data_dir()
        os.makedirs(candidate, exist_ok=True)
        _data_dir = candidate
    return _data_dir


def models_dir():
    path = os.path.join(data_dir(), "models")
    os.makedirs(path, exist_ok=True)
    return path


def bundled_models_dir():
    return os.path.join(bundle_dir(), "models")
