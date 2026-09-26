"""Drives the real app's AI clean-up setup assistant (Windows/macOS with a display):
    LOCALFLOW_DATA_DIR=<fresh tmp> python tests/setup_smoke.py <out-dir>
Never writes to the real keychain (keystore.set is stubbed)."""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tkinter import messagebox  # noqa: E402

from localflow import app as appmod, config, keystore  # noqa: E402

out_dir = sys.argv[1]
results = {}
stored = {}
keystore.set = lambda name, value: stored.__setitem__(name, bool(value)) or "stub"
errors = []
messagebox.showerror = lambda title, msg, **kw: errors.append(msg)
messagebox.askyesnocancel = lambda *a, **k: True


def shot(widget, name):
    widget.update()
    time.sleep(0.2)
    widget.update()
    try:
        import ctypes
        from ctypes import wintypes

        from PIL import Image

        hwnd = int(widget.wm_frame(), 16)
        rect = wintypes.RECT()
        ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect))
        w, h = rect.right - rect.left, rect.bottom - rect.top
        hdc = ctypes.windll.user32.GetWindowDC(hwnd)
        mdc = ctypes.windll.gdi32.CreateCompatibleDC(hdc)
        bmp = ctypes.windll.gdi32.CreateCompatibleBitmap(hdc, w, h)
        ctypes.windll.gdi32.SelectObject(mdc, bmp)
        ctypes.windll.user32.PrintWindow(hwnd, mdc, 2)
        buf = ctypes.create_string_buffer(w * h * 4)
        ctypes.windll.gdi32.GetBitmapBits(bmp, len(buf), buf)
        Image.frombuffer("RGBA", (w, h), buf, "raw", "BGRA", 0, 1).save(os.path.join(out_dir, name))
    except Exception as exc:
        results["shot_" + name] = str(exc)


def wait_until(a, cond, timeout):
    end = time.time() + timeout
    while time.time() < end and not cond():
        a.root.update()
        time.sleep(0.05)
    return cond()


def saved():
    with open(config.config_path(), encoding="utf-8") as f:
        return json.load(f)


def run(a):
    # 1. first launch opens the assistant
    results["opened_on_first_launch"] = wait_until(a, lambda: a.setup_dialog is not None, 5)
    d = a.setup_dialog
    shot(d.win, "setup-first-launch.png")

    # 2. Claude: check every install, expect a working one
    d.choice.set("claude"); d._choice_changed()
    d.check()
    wait_until(a, lambda: str(d.check_btn.cget("state")) == "normal" and d.result is not None
               or "isn't installed" in d.status.get("1.0", "end"), 150)
    results["claude_report"] = d.status.get("1.0", "end").strip().splitlines()
    results["claude_use_enabled"] = str(d.use_btn.cget("state")) == "normal"
    shot(d.win, "setup-claude-checked.png")
    d.use()
    c = saved()
    results["after_use"] = {k: c[k] for k in ("polish", "polish_verified", "claude_path", "setup_seen")}

    # 3. Groq with an obviously bad key -> clear failure, Use stays disabled
    a.open_setup(preselect="groq")
    d = a.setup_dialog
    d.key_var.set("not-a-real-key")
    d.check()
    wait_until(a, lambda: str(d.check_btn.cget("state")) == "normal", 30)
    results["groq_bad_key_report"] = d.status.get("1.0", "end").strip()[:200]
    results["groq_bad_key_use_enabled"] = str(d.use_btn.cget("state")) == "normal"
    shot(d.win, "setup-groq-bad-key.png")
    d.close()

    # 4. two runtime failures -> paused + assistant pending, opens when window is shown
    a._polish_failed("claude did not answer within 12s")
    results["paused_after_1"] = a.polish_paused
    a._polish_failed("claude did not answer within 12s")
    results["paused_after_2"] = a.polish_paused
    results["verified_cleared"] = saved()["polish_verified"]
    results["status_after_failures"] = a.status_var.get()
    a.show_window()
    results["assistant_opened_on_show"] = a.setup_dialog is not None and a.setup_dialog.win.winfo_exists()
    shot(a.setup_dialog.win, "setup-after-failure.png")
    a.setup_dialog.close()

    # 5. release tail / pre-roll settings
    s = a.settings
    a.nb.select(a.settings_tab); a.root.update()
    s.vars["release_tail"].set("1.2"); s.vars["preroll"].set("0.8")
    s.save_btn.invoke(); a.root.update()
    c = saved()
    results["tail_saved"] = c["release_tail"]; results["preroll_saved"] = c["preroll"]
    wait_until(a, lambda: a.recorder.tail == 1.2, 5)
    results["recorder_tail"] = a.recorder.tail; results["recorder_preroll"] = a.recorder.preroll
    s.vars["release_tail"].set("abc"); s.save_btn.invoke(); a.root.update()
    results["bad_number_error"] = errors[-1] if errors else None
    s.revert()
    shot(a.root, "settings-dictation.png")
    results["keychain_writes"] = stored
    with open(os.path.join(out_dir, "setup_results.json"), "w") as f:
        json.dump(results, f, indent=2)
    a.quit()


if __name__ == "__main__":
    a = appmod.App()
    a.root.after(300, lambda: run(a))
    a.run()
