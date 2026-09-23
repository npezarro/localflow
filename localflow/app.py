import logging
import queue
import socket
import sys
import threading
import time
import tkinter as tk
from datetime import datetime
from tkinter import messagebox, ttk

from . import __version__, audio, config, hotkey, output, paths, platform_fix, textproc
from .history import History
from .transcriber import Transcriber, is_local

log = logging.getLogger(__name__)
IS_MAC = sys.platform == "darwin"
IS_WIN = sys.platform == "win32"
SINGLE_INSTANCE_PORT = 47219

BG, FG, MUTED, ACCENT, RED = "#16171b", "#f2f2f2", "#9a9ca5", "#7c8cff", "#ff5a5f"


class App:
    def __init__(self):
        self.cfg = config.load()
        self.history = History(self.cfg["history_limit"])
        self.transcriber = Transcriber()
        self.recorder = audio.Recorder()
        self.ui_q = queue.Queue()
        self.ctl_q = queue.Queue()
        self.work_q = queue.Queue()
        self.record_started = 0.0
        self.overlay_mode = None

        platform_fix.windows_dpi_aware()
        platform_fix.pin_macos_keyboard_layout()
        self.root = tk.Tk()
        self.root.title("LocalFlow")
        self.root.geometry("720x560")
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
        self._build_history(hist)
        self._build_settings(sett)
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

    def _build_settings(self, parent):
        canvas_row = 0
        self.vars = {}
        grid = ttk.Frame(parent)
        grid.pack(fill="both", expand=True)
        grid.columnconfigure(1, weight=1)

        def row(label, widget, note=None):
            nonlocal canvas_row
            ttk.Label(grid, text=label).grid(row=canvas_row, column=0, sticky="w", pady=3, padx=(0, 10))
            widget.grid(row=canvas_row, column=1, sticky="ew", pady=3)
            if note:
                ttk.Label(grid, text=note, foreground="#777").grid(row=canvas_row, column=2, sticky="w",
                                                                   padx=6)
            canvas_row += 1

        def hotkey_field(key):
            frame = ttk.Frame(grid)
            var = tk.StringVar(value=self.cfg[key])
            self.vars[key] = var
            ttk.Entry(frame, textvariable=var, width=22).pack(side="left", fill="x", expand=True)
            ttk.Button(frame, text="Record…", command=lambda: self.capture_hotkey(var)).pack(
                side="left", padx=4)
            return frame

        row("Dictation hotkey", hotkey_field("hotkey"), "hold to talk")
        self.vars["mode"] = tk.StringVar(value=self.cfg["mode"])
        row("Mode", ttk.Combobox(grid, textvariable=self.vars["mode"], values=["hold", "toggle"],
                                 state="readonly", width=12),
            "hold: tap Space while holding for hands-free")
        row("Paste last transcript", hotkey_field("paste_last_hotkey"))
        self.vars["model"] = tk.StringVar(value=self.cfg["model"])
        row("Model", ttk.Combobox(grid, textvariable=self.vars["model"], values=config.MODEL_CHOICES,
                                  width=20), "downloads once into ./data/models")
        self.vars["language"] = tk.StringVar(value=self.cfg["language"])
        row("Language", ttk.Combobox(grid, textvariable=self.vars["language"],
                                     values=["en", "auto", "es", "fr", "de", "it", "pt", "nl", "ja",
                                             "zh", "ko", "ru", "hi"], width=8),
            ".en models are English-only")
        self.devices = [(None, "System default")]
        try:
            self.devices += audio.input_devices()
        except Exception:
            log.exception("listing input devices")
        names = [n for _i, n in self.devices]
        current = next((n for i, n in self.devices if i == self.cfg["input_device"]), names[0])
        self.vars["input_device"] = tk.StringVar(value=current)
        row("Microphone", ttk.Combobox(grid, textvariable=self.vars["input_device"], values=names,
                                       state="readonly"))
        for key, label in [("auto_paste", "Paste into the focused app"),
                           ("restore_clipboard", "Restore previous clipboard after pasting"),
                           ("remove_fillers", "Remove filler words (um, uh)"),
                           ("trailing_space", "Add a trailing space"),
                           ("sounds", "Start/stop sounds")]:
            self.vars[key] = tk.BooleanVar(value=bool(self.cfg[key]))
            row("", ttk.Checkbutton(grid, text=label, variable=self.vars[key]))

        ttk.Label(grid, text="Vocabulary (one per line)").grid(row=canvas_row, column=0, sticky="nw",
                                                              pady=3)
        self.vocab_text = tk.Text(grid, height=3, width=30)
        self.vocab_text.insert("1.0", "\n".join(self.cfg["vocabulary"]))
        self.vocab_text.grid(row=canvas_row, column=1, sticky="ew", pady=3)
        canvas_row += 1
        ttk.Label(grid, text="Replacements\n(spoken => written)").grid(row=canvas_row, column=0,
                                                                     sticky="nw", pady=3)
        self.repl_text = tk.Text(grid, height=3, width=30)
        self.repl_text.insert("1.0", "\n".join("%s => %s" % (k, v.replace("\n", "\\n"))
                                               for k, v in self.cfg["replacements"].items()))
        self.repl_text.grid(row=canvas_row, column=1, sticky="ew", pady=3)
        canvas_row += 1

        btns = ttk.Frame(parent)
        btns.pack(fill="x", pady=(8, 0))
        ttk.Button(btns, text="Save", command=self.save_settings).pack(side="left")
        ttk.Label(btns, text="Data folder: " + paths.data_dir(), foreground="#777").pack(side="left",
                                                                                        padx=10)
        ttk.Button(btns, text="Quit", command=self.quit).pack(side="right")

    def _build_overlay(self):
        ov = tk.Toplevel(self.root)
        ov.overrideredirect(True)
        ov.configure(bg=BG)
        ov.attributes("-topmost", True)
        try:
            ov.attributes("-alpha", 0.0)
        except tk.TclError:
            pass
        self.ov_w, self.ov_h = 250, 46
        self.ov_canvas = tk.Canvas(ov, width=self.ov_w, height=self.ov_h, bg=BG, highlightthickness=0)
        self.ov_canvas.pack()
        self.ov_dot = self.ov_canvas.create_oval(14, 17, 26, 29, fill=RED, outline="")
        self.ov_text = self.ov_canvas.create_text(36, 23, anchor="w", fill=FG, text="",
                                                  font=("TkDefaultFont", 11))
        self.ov_bars = [self.ov_canvas.create_rectangle(0, 0, 0, 0, fill=ACCENT, outline="")
                        for _ in range(12)]
        self.levels = [0.0] * 12
        self.overlay = ov
        ov.geometry("%dx%d+-10000+-10000" % (self.ov_w, self.ov_h))
        ov.update_idletasks()
        if IS_WIN:
            platform_fix.windows_no_activate(int(ov.wm_frame(), 16))

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

    def show_overlay(self, mode, text):
        self.overlay_mode = mode
        self.ov_canvas.itemconfigure(self.ov_text, text=text)
        self.ov_canvas.itemconfigure(self.ov_dot, fill=RED if mode in ("rec", "locked") else ACCENT)
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        x, y = (sw - self.ov_w) // 2, sh - self.ov_h - (110 if IS_WIN else 90)
        self.overlay.geometry("%dx%d+%d+%d" % (self.ov_w, self.ov_h, x, y))
        try:
            self.overlay.attributes("-alpha", 0.92)
        except tk.TclError:
            pass
        self.overlay.lift()

    def hide_overlay(self):
        self.overlay_mode = None
        try:
            self.overlay.attributes("-alpha", 0.0)
        except tk.TclError:
            pass
        self.overlay.geometry("+-10000+-10000")

    def _draw_levels(self):
        self.levels = self.levels[1:] + [self.recorder.level if self.overlay_mode in ("rec", "locked")
                                         else 0.08]
        x0 = self.ov_w - 12 * 7 - 12
        for i, (bar, lv) in enumerate(zip(self.ov_bars, self.levels)):
            h = 3 + lv * 26
            x = x0 + i * 7
            self.ov_canvas.coords(bar, x, 23 - h / 2, x + 4, 23 + h / 2)

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
                    self.recorder.start(self.cfg["input_device"])
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
                    self.recorder.stop()
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
                if not self.transcriber.ready.wait(timeout=600):
                    raise RuntimeError("model is still loading")
                t0 = time.time()
                raw = self.transcriber.transcribe(samples, self.cfg["language"], self.cfg["vocabulary"],
                                                  self.cfg["beam_size"])
                text = textproc.clean(raw, self.cfg)
                log.info("transcribed %.1fs audio in %.2fs", seconds, time.time() - t0)
                if not text.strip():
                    self.ui_q.put(("idle",))
                    continue
                item = self.history.add(text, seconds, self.transcriber.model_name)
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
        if self.overlay_mode:
            self._draw_levels()
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
            self.hide_overlay()
            self.set_status(ev[1], warn=True)
        elif kind == "model_ready":
            self.set_status("Ready · %s" % ev[1])
            if IS_MAC and self.listener and not self.listener.is_trusted:
                self.set_status("Hotkeys blocked: allow LocalFlow in Privacy & Security > "
                                "Accessibility and Input Monitoring, then restart", warn=True)
        elif kind == "model_failed":
            self.set_status("Could not load %s: %s" % (ev[1], ev[2][:120]), color=RED)
            if ev[1] != self.transcriber.model_name and self.transcriber.model is not None:
                self.transcriber.ready.set()  # keep using the previous model
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
    def save_settings(self):
        new = dict(self.cfg)
        try:
            for key in ("hotkey", "paste_last_hotkey"):
                hotkey.parse_combo(self.vars[key].get())
                new[key] = self.vars[key].get().strip().lower()
        except ValueError:
            messagebox.showerror("LocalFlow", "Hotkeys can't be empty.")
            return
        for key in ("mode", "model", "language"):
            new[key] = self.vars[key].get().strip()
        for key in ("auto_paste", "restore_clipboard", "remove_fillers", "trailing_space", "sounds"):
            new[key] = bool(self.vars[key].get())
        dev_name = self.vars["input_device"].get()
        new["input_device"] = next((i for i, n in self.devices if n == dev_name), None)
        new["vocabulary"] = [w.strip() for w in self.vocab_text.get("1.0", "end").splitlines() if w.strip()]
        repl = {}
        for line in self.repl_text.get("1.0", "end").splitlines():
            if "=>" in line:
                spoken, written = line.split("=>", 1)
                if spoken.strip():
                    repl[spoken.strip()] = written.strip().replace("\\n", "\n")
        new["replacements"] = repl
        model_changed = new["model"] != self.cfg["model"]
        self.cfg.clear()
        self.cfg.update(new)
        config.save(self.cfg)
        self.start_listener()
        self._update_hint()
        if model_changed:
            self.load_model(self.cfg["model"])
        else:
            self.set_status("Settings saved · %s" % self.cfg["model"])

    def run(self):
        self.root.mainloop()


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
    App().run()
    guard.close()
    return 0
