"""Drives the real Tk app (run on Windows/macOS with a display, not in CI):
    LOCALFLOW_DATA_DIR=<tmp> python tests/ui_smoke.py <out-dir>
Checks settings save/revert/prompt-on-leave and captures screenshots."""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tkinter import messagebox  # noqa: E402

from localflow import app as appmod, config, paths  # noqa: E402

out_dir = sys.argv[1]
answers = []
asked = []


def fake_ask(title, msg, **kw):
    asked.append(msg)
    return answers.pop(0)


messagebox.askyesnocancel = fake_ask
results = {}


def shot(a, name):
    a.root.update()
    time.sleep(0.3)
    a.root.update()
    try:
        import ctypes

        from PIL import Image, ImageGrab  # noqa: F401

        hwnd = int(a.root.wm_frame(), 16)
        rect = ctypes.wintypes.RECT()
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
        results["shot_" + name] = "failed: %s" % exc


def saved_cfg():
    with open(config.config_path(), encoding="utf-8") as f:
        return json.load(f)


def run(a):
    s = a.settings
    a.nb.select(a.settings_tab)
    a.root.update()
    shot(a, "settings-top.png")
    s.canvas.yview_moveto(0.55)
    shot(a, "settings-middle.png")
    s.canvas.yview_moveto(1.0)
    shot(a, "settings-bottom.png")
    s.canvas.yview_moveto(0)

    # 1) change mode, leave tab, answer Yes -> saved
    s.vars["mode"].set("toggle")
    a.root.update()
    results["dirty_after_change"] = s.dirty
    results["footer_text"] = s.dirty_var.get()
    shot(a, "settings-dirty.png")
    answers.append(True)
    a.nb.select(0)
    a.root.update()
    results["yes_saved_mode"] = saved_cfg()["mode"]
    results["yes_live_machine_mode"] = a.listener.machine.mode

    # 2) change back, leave, answer No -> reverted, not saved
    a.nb.select(a.settings_tab); a.root.update()
    s.vars["mode"].set("hold"); a.root.update()
    answers.append(False)
    a.nb.select(0); a.root.update()
    results["no_saved_mode"] = saved_cfg()["mode"]
    results["no_form_mode"] = s.vars["mode"].get()

    # 3) change, leave, Cancel -> stays on settings, still dirty
    a.nb.select(a.settings_tab); a.root.update()
    s.vars["language"].set("fr"); a.root.update()
    answers.append(None)
    a.nb.select(0); a.root.update()
    results["cancel_still_on_settings"] = a.nb.select() == str(a.settings_tab)
    results["cancel_still_dirty"] = s.dirty

    # 4) Save button
    s.save_btn.invoke(); a.root.update()
    results["savebutton_language"] = saved_cfg()["language"]
    results["savebutton_clean"] = not s.dirty

    # 5) Text widget edits count as changes
    s.vocab_text.insert("1.0", "Kubernetes\n"); a.root.update()
    results["text_edit_dirty"] = s.dirty
    s.save_btn.invoke(); a.root.update()
    results["vocab_saved"] = saved_cfg()["vocabulary"]

    # 6) engine / polish labels round-trip
    s.vars["polish"].set("Claude: your Claude subscription, via Claude Code")
    s.vars["engine"].set("Online API (most accurate; audio is sent to the provider)")
    s.save_btn.invoke(); a.root.update()
    c = saved_cfg()
    results["engine_polish_saved"] = [c["engine"], c["polish"]]
    results["status"] = a.status_var.get()
    results["asked"] = asked
    a.nb.select(0); a.root.update()
    shot(a, "transcripts.png")
    with open(os.path.join(out_dir, "ui_results.json"), "w") as f:
        json.dump(results, f, indent=2)
    a.quit()


if __name__ == "__main__":
    a = appmod.App()
    a.root.after(2500, lambda: run(a))
    a.run()
