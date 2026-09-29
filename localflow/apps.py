"""Which app has keyboard focus, and is LocalFlow allowed to type into it?

Settings: ``type_into`` = "all" (default) | "only" (allow-list) | "except" (block-list),
``app_list`` = names, one per entry. An entry matches the program file name with or
without extension ("chrome", "chrome.exe"), the app's display name ("Google Chrome"),
or its macOS bundle id ("com.google.Chrome"), ignoring case.
"""
import logging
import os
import sys

log = logging.getLogger(__name__)


def _windows_foreground():
    import ctypes
    from ctypes import wintypes

    user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
    user32.GetForegroundWindow.restype = ctypes.c_void_p
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return None
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(ctypes.c_void_p(hwnd), ctypes.byref(pid))
    title = ctypes.create_unicode_buffer(256)
    user32.GetWindowTextW(ctypes.c_void_p(hwnd), title, 256)
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    kernel32.OpenProcess.restype = ctypes.c_void_p
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
    exe = ""
    if handle:
        buf = ctypes.create_unicode_buffer(1024)
        size = wintypes.DWORD(1024)
        if kernel32.QueryFullProcessImageNameW(ctypes.c_void_p(handle), 0, buf, ctypes.byref(size)):
            exe = os.path.basename(buf.value)
        kernel32.CloseHandle(ctypes.c_void_p(handle))
    cls = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(ctypes.c_void_p(hwnd), cls, 256)
    return {"name": exe or title.value, "exe": exe, "title": title.value, "id": exe.lower(),
            "window_class": cls.value}


# Terminal windows, whatever program runs inside them: Ctrl+Z there suspends or sends EOF
# instead of undoing, so LocalFlow never does an undo + paste into one.
TERMINAL_WINDOW_CLASSES = {"consolewindowclass", "cascadia_hosting_window_class", "mintty",
                           "virtualconsoleclass", "puttyconfigbox", "putty"}
TERMINAL_APPS = {"windowsterminal", "cmd", "powershell", "pwsh", "conhost", "wsl", "mintty", "alacritty",
                 "wezterm", "wezterm-gui", "kitty", "putty", "termius", "tabby", "hyper", "warp",
                 "com.apple.terminal", "com.googlecode.iterm2", "net.kovidgoyal.kitty",
                 "org.alacritty", "com.github.wez.wezterm", "dev.warp.warp-stable", "co.zeit.hyper",
                 "com.termius-dmg.mac"}


def is_terminal(app):
    if not app:
        return False
    if (app.get("window_class") or "").lower() in TERMINAL_WINDOW_CLASSES:
        return True
    ids = {os.path.splitext((app.get(k) or "").lower())[0] for k in ("id", "exe")}
    ids |= {(app.get("id") or "").lower()}
    return bool(ids & TERMINAL_APPS)


def _mac_foreground():
    from AppKit import NSWorkspace

    app = NSWorkspace.sharedWorkspace().frontmostApplication()
    if app is None:
        return None
    name = str(app.localizedName() or "")
    bundle = str(app.bundleIdentifier() or "")
    exe = os.path.basename(str(app.executableURL().path())) if app.executableURL() else ""
    return {"name": name or exe, "exe": exe, "title": name, "id": bundle}


def foreground():
    """{"name", "exe", "title", "id"} for the focused app, or None if unknown."""
    try:
        if sys.platform == "win32":
            return _windows_foreground()
        if sys.platform == "darwin":
            return _mac_foreground()
    except Exception:
        log.debug("foreground app lookup failed", exc_info=True)
    return None


def _keys(app):
    keys = set()
    for value in (app.get("name"), app.get("exe"), app.get("id")):
        if value:
            v = value.strip().lower()
            keys.add(v)
            keys.add(os.path.splitext(v)[0])
    return keys


def matches(entry, app):
    e = entry.strip().lower()
    return bool(e) and (e in _keys(app) or os.path.splitext(e)[0] in _keys(app))


def allowed(app, cfg):
    """Can we type/paste into ``app``? Unknown app -> follow the mode's default."""
    mode = cfg.get("type_into", "all")
    if mode == "all":
        return True
    if app is None:
        return mode == "except"
    hit = any(matches(e, app) for e in cfg.get("app_list") or [])
    return hit if mode == "only" else not hit


def label(app):
    return (app or {}).get("name") or "this app"


def _seen_path():
    from . import paths

    return os.path.join(paths.data_dir(), "seen_apps.json")


def remember(app):
    """Record apps you dictate into, so Settings can offer them instead of guessing names."""
    if not app or not app.get("name"):
        return
    import json
    import time

    try:
        with open(_seen_path(), encoding="utf-8") as f:
            seen = json.load(f)
    except (OSError, ValueError):
        seen = {}
    key = app.get("exe") or app["name"]
    seen[key] = {"name": app["name"], "exe": app.get("exe", ""), "id": app.get("id", ""), "last": time.time()}
    seen = dict(sorted(seen.items(), key=lambda kv: -kv[1]["last"])[:40])
    try:
        with open(_seen_path(), "w", encoding="utf-8") as f:
            json.dump(seen, f)
    except OSError:
        pass


def seen_apps():
    """Most recent first: list of display strings like 'chrome.exe' or 'Slack'."""
    import json

    try:
        with open(_seen_path(), encoding="utf-8") as f:
            seen = json.load(f)
    except (OSError, ValueError):
        return []
    return [key for key, _v in sorted(seen.items(), key=lambda kv: -kv[1]["last"])]
