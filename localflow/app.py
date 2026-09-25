import logging
import os
import queue
import socket
import sys
import threading
import time
import tkinter as tk
from datetime import datetime
from tkinter import messagebox, ttk

from . import __version__, audio, config, hotkey, keystore, output, paths, pipeline, platform_fix, polish
from .history import History
from .indicator import Indicator
from .settings_ui import SettingsPanel
from .transcriber import Transcriber, is_local

log = logging.getLogger(__name__)
IS_MAC = sys.platform == "darwin"
IS_WIN = sys.platform == "win32"
SINGLE_INSTANCE_PORT = 47219

RED = "#ff5a5f"


class App:
    def __init__(self):
        self.cfg = config.load()
        self.history = History(self.cfg["history_limit"])
        self.transcriber = Transcriber()
        self.recorder = audio.Recorder(warm=self.cfg["warm_mic"])
        self.devices = [(None, "System default")]
        try:
            self.devices += audio.input_devices()
        except Exception:
            log.exception("listing input devices")
        self.ui_q = queue.Queue()
        self.ctl_q = queue.Queue()
        self.work_q = queue.Queue()
        self.record_started = 0.0

        platform_fix.windows_dpi_aware()
        platform_fix.pin_macos_keyboard_layout()
        self.root = tk.Tk()
        self.root.title("LocalFlow")
        self.root.geometry("780x640")
        self.root.minsize(560, 420)
        self.paster = output.Paster()  # after Tk: both must be created on the main thread

        self._build_ui()
        self._build_overlay()
        self._install_close_behaviour()

        threading.Thread(target=self._control_loop, daemon=True, name="control").start()
        threading.Thread(target=self._work_loop, daemon=True, name="transcribe").start()
        self.load_model(self.cfg["model"])
        self.listener = None
        self.start_listener()
        self._open_mic()
        self.root.after(40, self._poll)
        if IS_MAC and not platform_fix.macos_accessibility_trusted(prompt=True):
            self.set_status("Grant Accessibility + Input Monitoring in System Settings, then restart",
                            warn=True)

    # ------------------------------------------------------------------ UI
    def _build_ui(self):
        root = self.root
        style = ttk.Style(root)
        if "clam" in style.theme_names() and not IS_MAC:
            style.theme_use("clam")
        top = ttk.Frame(root, padding=(14, 12, 14, 6))
        top.pack(fill="x")
        self.status_dot = tk.Canvas(top, width=14, height=14, highlightthickness=0)
        self.status_dot.pack(side="left")
        self._dot = self.status_dot.create_oval(2, 2, 12, 12, fill="#bbb", outline="")
        self.status_var = tk.StringVar(value="Starting…")
        ttk.Label(top, textvariable=self.status_var, font=("TkDefaultFont", 11, "bold")).pack(
            side="left", padx=8)
        self.hint_var = tk.StringVar()
        ttk.Label(root, textvariable=self.hint_var, foreground="#666", padding=(36, 0, 14, 6),
                  wraplength=640).pack(fill="x")

        nb = ttk.Notebook(root)
        nb.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        hist = ttk.Frame(nb, padding=8)
        sett = ttk.Frame(nb, padding=8)
        nb.add(hist, text="Transcripts")
        nb.add(sett, text="Settings")
        self.nb, self.settings_tab = nb, sett
        self._build_history(hist)
        self.settings = SettingsPanel(sett, self)
        self._current_tab = str(hist)
        self._reverting_tab = False
        nb.bind("<<NotebookTabChanged>>", self._tab_changed)
        self._update_hint()

    def _build_history(self, parent):
        bar = ttk.Frame(parent)
        bar.pack(fill="x")
        ttk.Label(bar, text="Search").pack(side="left")
        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", lambda *_: self.refresh_history())
        ttk.Entry(bar, textvariable=self.search_var).pack(side="left", fill="x", expand=True, padx=6)
        ttk.Button(bar, text="Copy", command=self.copy_selected).pack(side="left")
        ttk.Button(bar, text="Delete", command=self.delete_selected).pack(side="left", padx=4)
        ttk.Button(bar, text="Clear all", command=self.clear_history).pack(side="left")

        pane = ttk.PanedWindow(parent, orient="vertical")
        pane.pack(fill="both", expand=True, pady=(8, 0))
        frame = ttk.Frame(pane)
        self.tree = ttk.Treeview(frame, columns=("when", "text"), show="headings", selectmode="browse")
        self.tree.heading("when", text="When")
        self.tree.heading("text", text="Transcript (double-click to copy)")
        self.tree.column("when", width=120, stretch=False)
        self.tree.column("text", width=500)
        sb = ttk.Scrollbar(frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.tree.bind("<<TreeviewSelect>>", lambda _e: self._show_selected())
        self.tree.bind("<Double-1>", lambda _e: self.copy_selected())
        pane.add(frame, weight=3)
        self.detail = tk.Text(pane, height=5, wrap="word", relief="flat", padx=8, pady=6)
        self.detail.configure(state="disabled")
        pane.add(self.detail, weight=1)
        self.refresh_history()

    def _tab_changed(self, _event):
        new = self.nb.select()
        if self._reverting_tab:
            self._reverting_tab = False
            self._current_tab = new
            return
        if self._current_tab == str(self.settings_tab) and new != self._current_tab:
            if not self.settings.confirm_leave():
                self._reverting_tab = True
                self.nb.select(self.settings_tab)
                return
        self._current_tab = new

    def _build_overlay(self):
        self.indicator = Indicator(self.root)

    def _install_close_behaviour(self):
        self.root.protocol("WM_DELETE_WINDOW", self.hide_window)
        if IS_MAC:
            self.root.createcommand("tk::mac::ReopenApplication", self.show_window)
            self.root.createcommand("tk::mac::Quit", self.quit)
        if IS_WIN:
            self._start_tray()

    def _start_tray(self):
        try:
            import pystray
            from PIL import Image, ImageDraw
        except Exception:
            log.info("pystray unavailable; closing the window will minimise instead")
            self.tray = None
            return
        img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        d.ellipse((4, 4, 60, 60), fill=(124, 140, 255, 255))
        d.rounded_rectangle((24, 14, 40, 40), radius=8, fill="white")
        d.rectangle((30, 42, 34, 50), fill="white")
        menu = pystray.Menu(
            pystray.MenuItem("Show LocalFlow", lambda: self.ui_q.put(("show",)), default=True),
            pystray.MenuItem("Paste last transcript", lambda: self.ctl_q.put(("paste_last",))),
            pystray.MenuItem("Quit", lambda: self.ui_q.put(("quit",))))
        self.tray = pystray.Icon("LocalFlow", img, "LocalFlow", menu)
        self.tray.run_detached()

    def hide_window(self):
        if not self.settings.confirm_leave():
            return
        if IS_WIN and not getattr(self, "tray", None):
            self.root.iconify()
        else:
            self.root.withdraw()

    def show_window(self):
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()

    def quit(self):
        try:
            if self.listener:
                self.listener.stop()
            if getattr(self, "tray", None):
                self.tray.stop()
        finally:
            self.root.destroy()

    # ------------------------------------------------------------------ status + overlay
    def set_status(self, text, color=None, warn=False):
        self.status_var.set(text)
        self.status_dot.itemconfigure(self._dot, fill=color or ("#e0a030" if warn else "#3cb371"))

    def _update_hint(self):
        hk = hotkey.format_combo(self.cfg["hotkey"])
        if self.cfg["mode"] == "hold":
            text = ("Hold %s and speak; release to paste. Tap %s while holding for hands-free, "
                    "press %s again to finish. Esc cancels." % (hk, self.cfg["lock_key"].title(), hk))
        else:
            text = "Press %s to start dictating, press it again to paste. Esc cancels." % hk
        text += "  Paste last: %s." % hotkey.format_combo(self.cfg["paste_last_hotkey"])
        self.hint_var.set(text)

    def show_overlay(self, mode, text=""):
        kind = {"rec": "listening", "locked": "locked", "message": "message"}.get(mode)
        if kind is None:
            kind = "polish" if text.startswith("Cleaning") else "busy"
        self.indicator.show(kind, text)

    def hide_overlay(self):
        self.indicator.hide()

    # ------------------------------------------------------------------ hotkeys
    def start_listener(self):
        if self.listener:
            self.listener.stop()
        machine = hotkey.HotkeyMachine(
            self.cfg["hotkey"], mode=self.cfg["mode"], lock_key=self.cfg["lock_key"],
            min_hold=self.cfg["min_hold_seconds"],
            on_start=lambda: self.ctl_q.put(("start",)),
            on_stop=lambda: self.ctl_q.put(("stop",)),
            on_cancel=lambda: self.ctl_q.put(("cancel",)),
            on_lock=lambda: self.ui_q.put(("locked",)))
        oneshots = {}
        try:
            oneshots[hotkey.parse_combo(self.cfg["paste_last_hotkey"])] = \
                lambda: self.ctl_q.put(("paste_last",))
        except ValueError:
            pass
        self.listener = hotkey.HotkeyListener(machine, oneshots)
        self.listener.start()

    def capture_hotkey(self, var):
        var.set("press keys…")

        def done(combo):
            self.ui_q.put(("captured", var, combo))

        self.listener.capture_next(done)

    # ------------------------------------------------------------------ worker threads
    def _control_loop(self):
        while True:
            cmd = self.ctl_q.get()[0]
            try:
                if cmd == "start":
                    if self.recorder.active:
                        continue
                    if IS_WIN and "cmd" in hotkey.parse_combo(self.cfg["hotkey"]):
                        platform_fix.mask_windows_key()
                    self.recorder.start()
                    self.record_started = time.monotonic()
                    if self.cfg["sounds"]:
                        audio.play(audio.tone(880))
                    self.ui_q.put(("recording",))
                elif cmd == "stop":
                    samples = self.recorder.stop()
                    seconds = time.monotonic() - self.record_started
                    if self.cfg["sounds"]:
                        audio.play(audio.tone(660))
                    if len(samples) < audio.SAMPLE_RATE * 0.25:
                        self.ui_q.put(("idle",))
                        continue
                    self.ui_q.put(("transcribing",))
                    self.work_q.put((samples, seconds))
                elif cmd == "cancel":
                    self.recorder.stop(keep_tail=False)
                    self.ui_q.put(("idle",))
                elif cmd == "paste_last":
                    last = self.history.last()
                    if last:
                        self._wait_for_keys_up()
                        self.paster.deliver(last["text"], True, self.cfg["restore_clipboard"])
            except Exception as exc:
                log.exception("control %s", cmd)
                self.ui_q.put(("error", "Microphone error: %s" % exc))

    def _wait_for_keys_up(self, timeout=2.5):
        # Pasting while the user still holds Ctrl/Win/Alt would send the wrong shortcut.
        deadline = time.monotonic() + timeout
        while self.listener and self.listener.machine.pressed and time.monotonic() < deadline:
            time.sleep(0.02)

    def _work_loop(self):
        while True:
            samples, seconds = self.work_q.get()
            try:
                st = audio.stats(samples)
                if self.cfg["save_last_recording"]:
                    audio.save_wav(os.path.join(paths.data_dir(), "last-recording.wav"), samples)
                result = pipeline.process(samples, self.cfg, self.transcriber,
                                          on_stage=lambda _s: self.ui_q.put(("overlay", "busy", "Cleaning up…")))
                log.info("dictation: audio %.1fs peak %.3f rms %.4f | %s %.2fs%s | %d chars%s",
                         st["seconds"], st["peak"], st["rms"], result["engine"], result["stt_s"],
                         " + clean-up %.2fs" % result["polish_s"] if result["polished"] else "",
                         len(result["text"]), " | " + result["note"] if result["note"] else "")
                if result["note"]:
                    self.ui_q.put(("status_warn", result["note"]))
                text = result["text"]
                if not text.strip():
                    msg = ("Didn't catch anything. Microphone level was very low: check the input "
                           "device in Settings." if st["peak"] < 0.02 else "Didn't catch any words.")
                    self.ui_q.put(("error", msg))
                    continue
                item = self.history.add(text, seconds, result["engine"],
                                        raw=result["raw"] if result["polished"] else None)
                self._wait_for_keys_up()
                self.paster.deliver(text, self.cfg["auto_paste"], self.cfg["restore_clipboard"])
                self.ui_q.put(("transcript", item))
            except Exception as exc:
                log.exception("transcription failed")
                self.ui_q.put(("error", "Transcription failed: %s" % exc))

    def load_model(self, name):
        self.transcriber.ready.clear()
        verb = "Loading" if is_local(name) else "Downloading"
        self.set_status("%s model %s…" % (verb, name), warn=True)

        def run():
            try:
                self.transcriber.load(name)
                self.ui_q.put(("model_ready", name))
            except Exception as exc:
                log.exception("model load failed")
                self.ui_q.put(("model_failed", name, str(exc)))

        threading.Thread(target=run, daemon=True, name="model-load").start()

    # ------------------------------------------------------------------ UI event pump
    def _poll(self):
        try:
            while True:
                self._handle(self.ui_q.get_nowait())
        except queue.Empty:
            pass
        self.indicator.tick(self.recorder.level)
        self.root.after(40, self._poll)

    def _handle(self, ev):
        kind = ev[0]
        if kind == "recording":
            self.show_overlay("rec", "Listening…")
        elif kind == "locked":
            self.show_overlay("locked", "Hands-free · hotkey to finish")
        elif kind == "transcribing":
            self.show_overlay("busy", "Transcribing…")
        elif kind == "idle":
            self.hide_overlay()
        elif kind == "transcript":
            self.hide_overlay()
            self.refresh_history()
        elif kind == "error":
            self.show_overlay("message", ev[1][:70])
            self.set_status(ev[1], warn=True)
        elif kind == "model_ready":
            self.set_status("Ready · %s" % self._engine_label())
            if IS_MAC and self.listener and not self.listener.is_trusted:
                self.set_status("Hotkeys blocked: allow LocalFlow in Privacy & Security > "
                                "Accessibility and Input Monitoring, then restart", warn=True)
        elif kind == "model_failed":
            self.set_status("Could not load %s: %s" % (ev[1], ev[2][:120]), color=RED)
            if ev[1] != self.transcriber.model_name and self.transcriber.model is not None:
                self.transcriber.ready.set()  # keep using the previous model
        elif kind == "overlay":
            self.show_overlay(ev[1], ev[2])
        elif kind == "dialog":
            messagebox.showinfo(ev[1], ev[2], parent=self.root)
        elif kind == "status":
            self.set_status(ev[1])
        elif kind == "setvar":
            self.settings.vars[ev[1]].set(ev[2])
        elif kind == "status_warn":
            self.set_status(ev[1], warn=True)
        elif kind == "captured":
            ev[1].set(ev[2])
        elif kind == "show":
            self.show_window()
        elif kind == "quit":
            self.quit()

    # ------------------------------------------------------------------ history actions
    def refresh_history(self):
        q = self.search_var.get().lower().strip() if hasattr(self, "search_var") else ""
        self.tree.delete(*self.tree.get_children())
        for item in reversed(self.history.items):
            if q and q not in item["text"].lower():
                continue
            when = datetime.fromtimestamp(item["ts"]).strftime("%b %d %H:%M")
            self.tree.insert("", "end", iid=item["id"], values=(when, item["text"].replace("\n", " ⏎ ")))

    def _selected(self):
        sel = self.tree.selection()
        if not sel:
            return None
        return next((i for i in self.history.items if i["id"] == sel[0]), None)

    def _show_selected(self):
        item = self._selected()
        self.detail.configure(state="normal")
        self.detail.delete("1.0", "end")
        if item:
            self.detail.insert("1.0", item["text"])
            meta = "\n\n— %s, %.1fs" % (item.get("model", ""), item.get("seconds", 0))
            if item.get("raw"):
                meta += "\nBefore clean-up: " + item["raw"]
            self.detail.insert("end", meta, "meta")
            self.detail.tag_configure("meta", foreground="#888")
        self.detail.configure(state="disabled")

    def copy_selected(self):
        item = self._selected()
        if item:
            output.set_clipboard(item["text"])
            self.set_status("Copied to clipboard")

    def delete_selected(self):
        item = self._selected()
        if item:
            self.history.delete(item["id"])
            self.refresh_history()

    def clear_history(self):
        if messagebox.askyesno("LocalFlow", "Delete all saved transcripts?"):
            self.history.clear()
            self.refresh_history()

    # ------------------------------------------------------------------ settings
    def apply_settings(self, new):
        model_changed = new["model"] != self.cfg["model"]
        self.cfg.clear()
        self.cfg.update(new)
        config.save(self.cfg)
        self.start_listener()
        self._open_mic()
        self._update_hint()
        if model_changed:
            self.load_model(self.cfg["model"])
        else:
            self.set_status("Settings saved · %s" % self._engine_label())

    def _engine_label(self):
        if self.cfg["engine"] == "cloud":
            base, model, _k = pipeline.cloud_settings(self.cfg)
            label = "%s (%s)" % (model, self.cfg["cloud_provider"])
        else:
            label = self.cfg["model"]
        if self.cfg["polish"] != "off":
            label += " + %s clean-up" % self.cfg["polish"]
        return label

    def _open_mic(self):
        def run():
            try:
                self.recorder.configure(self.cfg["input_device"], self.cfg["warm_mic"])
            except Exception as exc:
                log.exception("opening microphone")
                self.ui_q.put(("error", "Microphone error: %s" % exc))

        threading.Thread(target=run, daemon=True, name="mic-open").start()

    # ------------------------------------------------------------------ diagnostics
    def test_microphone(self):
        if self.settings.dirty and not self.settings.confirm_leave():
            return

        def run():
            try:
                self.ui_q.put(("overlay", "rec", "Test: speak now (4 s)…"))
                self.recorder.start()
                time.sleep(4)
                samples = self.recorder.stop(keep_tail=False)
                st = audio.stats(samples)
                self.ui_q.put(("overlay", "busy", "Transcribing…"))
                result = pipeline.process(samples, self.cfg, self.transcriber)
                if self.cfg["save_last_recording"]:
                    audio.save_wav(os.path.join(paths.data_dir(), "last-recording.wav"), samples)
                verdict = ("Level looks good." if st["peak"] > 0.1 else
                           "Very quiet: check the input device and its volume." if st["peak"] > 0.01 else
                           "No sound: wrong input device, muted, or no microphone permission.")
                msg = ("Heard: %s\n\nRecorded %.1fs, peak %.2f, rms %.3f. %s\nEngine: %s, %.1fs%s%s"
                       % (result["text"].strip() or "(nothing)", st["seconds"], st["peak"], st["rms"], verdict,
                          result["engine"], result["stt_s"],
                          ", clean-up %.1fs" % result["polish_s"] if result["polished"] else "",
                          "\n" + result["note"] if result["note"] else ""))
                self.ui_q.put(("dialog", "Microphone test", msg))
            except Exception as exc:
                log.exception("mic test")
                self.ui_q.put(("dialog", "Microphone test", "Failed: %s" % exc))
            finally:
                self.ui_q.put(("idle",))

        threading.Thread(target=run, daemon=True, name="mic-test").start()

    def test_polish(self):
        try:
            cfg = self.settings.collect()
        except ValueError as exc:
            messagebox.showerror("LocalFlow", str(exc))
            return
        if cfg["polish"] == "off":
            messagebox.showinfo("LocalFlow", "Pick a clean-up option first.")
            return
        sample = ("um so I think we should uh meet on tuesday at 3 pm and then like send the deck "
                  "to sarah, actually no wednesday")
        keys = {k: v.get().strip() for k, v in self.settings.key_vars.items()}
        self.set_status("Testing clean-up with %s…" % cfg["polish"], warn=True)

        def run():
            found = ""
            provider = cfg["polish"]
            if provider in ("claude", "codex") and not cfg[provider + "_path"]:
                self.ui_q.put(("status_warn", "Looking for a working %s install…" % provider))
                label, lines = polish.detect(provider, cfg)
                found = "Checked:\n" + "\n".join(lines) + "\n\n"
                if not label:
                    self.ui_q.put(("dialog", "AI clean-up test", found + "No working install found."))
                    self.ui_q.put(("status", "Ready · %s" % self._engine_label()))
                    return
                cfg[provider + "_path"] = label
                self.ui_q.put(("setvar", provider + "_path", label))
            t0 = time.time()
            try:
                out = polish.polish(sample, cfg, lambda name: keys.get(name) or keystore.get(name))
                msg = found + "In:  %s\n\nOut: %s\n\n%.1f s" % (sample, out, time.time() - t0)
                if found:
                    msg += "\n\nThe working command was filled in; press Save to keep it."
            except Exception as exc:
                msg = found + "Failed after %.1f s:\n%s" % (time.time() - t0, exc)
            self.ui_q.put(("dialog", "AI clean-up test", msg))
            self.ui_q.put(("status", "Ready · %s" % self._engine_label()))

        threading.Thread(target=run, daemon=True, name="polish-test").start()

    def run(self):
        self.root.mainloop()


def _smoke(app):
    """CI: prove the packaged GUI starts (window, pill, settings, hotkey listener, model)."""
    import json

    out = sys.argv[sys.argv.index("--smoke-ui") + 1] if len(sys.argv) > sys.argv.index("--smoke-ui") + 1 else None
    started = time.time()

    def check():
        if not app.transcriber.ready.is_set() and time.time() - started < 120:
            app.root.after(250, check)
            return
        app.indicator.show("listening")
        app.indicator.tick(0.5)
        app.nb.select(app.settings_tab)
        app.root.update()
        result = {"ok": app.transcriber.ready.is_set(), "status": app.status_var.get(),
                  "settings_fields": len(app.settings.vars), "indicator": app.indicator.mode,
                  "listener": app.listener is not None}
        if out:
            with open(out, "w", encoding="utf-8") as f:
                json.dump(result, f, indent=2)
        app.indicator.hide()
        app.quit()

    app.root.after(500, check)


def _single_instance():
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", SINGLE_INSTANCE_PORT))
        sock.listen(1)
        return sock
    except OSError:
        return None


def main():
    guard = _single_instance()
    if guard is None:
        root = tk.Tk()
        root.withdraw()
        messagebox.showinfo("LocalFlow", "LocalFlow is already running.")
        return 1
    log.info("LocalFlow %s starting; data dir %s", __version__, paths.data_dir())
    app = App()
    if "--smoke-ui" in sys.argv:
        _smoke(app)
    app.run()
    guard.close()
    return 0
