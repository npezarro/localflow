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

from . import __version__, apps, audio, config, hotkey, keystore, output, paths, pipeline, platform_fix, polish, typer
from .history import History
from .dictionary import Dictionary
from .dictionary_ui import DictionaryTab
from .indicator import Indicator
from .learn import Learner
from .live import LiveSession
from .settings_ui import SettingsPanel
from .setup_ui import SetupDialog
from .transcriber import Transcriber, is_local, speech_in
from . import ui
from .ui import px

log = logging.getLogger(__name__)
IS_MAC = sys.platform == "darwin"
IS_WIN = sys.platform == "win32"
SINGLE_INSTANCE_PORT = 47219

RED = "#ff5a5f"


class App:
    def __init__(self):
        self.cfg = config.load()
        self.history = History(self.cfg["history_limit"])
        self.learner = Learner()
        self.dictionary = Dictionary()
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
        self.live_q = queue.Queue()
        self.live_active = False
        self.continuous = False  # always-on live listening (pause/resume hotkey)
        self._blocked_notice = None
        self.record_started = 0.0
        self.polish_failures = 0
        self.polish_paused = False
        self.pending_setup = None  # reason text; the setup assistant opens when the window is next shown
        self.setup_dialog = None

        platform_fix.windows_dpi_aware()
        platform_fix.pin_macos_keyboard_layout()
        self.root = tk.Tk()
        self.root.title("LocalFlow")
        ui.init(self.root)
        self.root.geometry("%dx%d" % (px(800), px(660)))
        self.root.minsize(px(560), px(420))
        self.paster = output.Paster()  # after Tk: both must be created on the main thread
        icon_path = os.path.join(paths.bundle_dir(), "assets", "icon.png")
        if os.path.exists(icon_path) and not IS_MAC:  # macOS uses the .app bundle's icon
            try:
                self._icon = tk.PhotoImage(file=icon_path).subsample(16)
                self.root.iconphoto(True, self._icon)
            except tk.TclError:
                pass

        self._build_ui()
        self._build_overlay()
        self._install_close_behaviour()

        threading.Thread(target=self._control_loop, daemon=True, name="control").start()
        threading.Thread(target=self._work_loop, daemon=True, name="transcribe").start()
        threading.Thread(target=self._live_loop, daemon=True, name="live").start()
        self.load_model(self.cfg["model"])
        self.listener = None
        self.start_listener()
        self._open_mic()
        self.root.after(40, self._poll)
        self.root.after(1200, self._maybe_setup)
        if self.cfg["live_autostart"]:
            self.root.after(1500, lambda: self.ctl_q.put(("continuous",)))
        if IS_MAC and not platform_fix.macos_accessibility_trusted(prompt=True):
            self.set_status("Grant Accessibility + Input Monitoring in System Settings, then restart",
                            warn=True)

    # ------------------------------------------------------------------ UI
    def _build_ui(self):
        root = self.root
        style = ttk.Style(root)
        if "clam" in style.theme_names() and not IS_MAC:
            style.theme_use("clam")
        style.configure("Treeview", rowheight=px(22))
        # Theme elements drawn at fixed pixel sizes: scale them with the display.
        for name in ("TCheckbutton", "TRadiobutton"):
            style.configure(name, indicatorsize=px(12), indicatormargin=(px(2), px(2), px(6), px(2)))
        style.configure("Vertical.TScrollbar", arrowsize=px(14))
        style.configure("Horizontal.TScrollbar", arrowsize=px(14))
        top = ttk.Frame(root, padding=(px(14), px(12), px(14), px(6)))
        top.pack(fill="x")
        self.status_dot = tk.Canvas(top, width=px(14), height=px(14), highlightthickness=0)
        self.status_dot.pack(side="left")
        self._dot = self.status_dot.create_oval(px(2), px(2), px(12), px(12), fill="#bbb", outline="")
        self.status_var = tk.StringVar(value="Starting…")
        ttk.Label(top, textvariable=self.status_var, font=("TkDefaultFont", 11, "bold")).pack(
            side="left", padx=8)
        self.hint_var = tk.StringVar()
        ttk.Label(root, textvariable=self.hint_var, foreground="#666", padding=(px(36), 0, px(14), px(6)),
                  wraplength=px(640)).pack(fill="x")

        nb = ttk.Notebook(root)
        nb.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        hist = ttk.Frame(nb, padding=8)
        sett = ttk.Frame(nb, padding=8)
        dic = ttk.Frame(nb, padding=8)
        nb.add(hist, text="Transcripts")
        nb.add(dic, text="Dictionary")
        nb.add(sett, text="Settings")
        self.nb, self.settings_tab = nb, sett
        self._build_history(hist)
        self.dictionary_tab = DictionaryTab(dic, self)
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
        self.correct_btn = ttk.Button(bar, text="Save correction", command=self.save_correction,
                                      state="disabled")
        self.correct_btn.pack(side="left", padx=(4, 0))
        ttk.Button(bar, text="Delete", command=self.delete_selected).pack(side="left", padx=4)
        ttk.Button(bar, text="Clear all", command=self.clear_history).pack(side="left")

        pane = ttk.PanedWindow(parent, orient="vertical")
        pane.pack(fill="both", expand=True, pady=(8, 0))
        frame = ttk.Frame(pane)
        self.tree = ttk.Treeview(frame, columns=("when", "text"), show="headings", selectmode="browse")
        self.tree.heading("when", text="When")
        self.tree.heading("text", text="Transcript (double-click to copy)")
        self.tree.column("when", width=px(120), stretch=False)
        self.tree.column("text", width=px(500))
        sb = ttk.Scrollbar(frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.tree.bind("<<TreeviewSelect>>", lambda _e: self._show_selected())
        self.tree.bind("<Double-1>", lambda _e: self.copy_selected())
        pane.add(frame, weight=3)
        lower = ttk.Frame(pane)
        self.detail = tk.Text(lower, height=5, wrap="word", relief="flat", padx=8, pady=6, undo=True)
        self.detail.pack(fill="both", expand=True)
        self.detail.bind("<<Modified>>", self._detail_modified)
        self.detail_meta = tk.StringVar(value="Select a transcript. Fix any mistakes here and press "
                                              "Save correction: LocalFlow learns from it.")
        ttk.Label(lower, textvariable=self.detail_meta, foreground="#888", wraplength=px(700),
                  justify="left").pack(fill="x", pady=(4, 0))
        pane.add(lower, weight=1)
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
        icon_path = os.path.join(paths.bundle_dir(), "assets", "icon.png")
        if os.path.exists(icon_path):
            img = Image.open(icon_path).resize((64, 64))
        else:
            img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
            ImageDraw.Draw(img).ellipse((4, 4, 60, 60), fill=(124, 140, 255, 255))
        menu = pystray.Menu(
            pystray.MenuItem("Show LocalFlow", lambda: self.ui_q.put(("show",)), default=True),
            pystray.MenuItem("Paste last transcript", lambda: self.ctl_q.put(("paste_last",))),
            pystray.MenuItem("Live listening (always on)", lambda: self.ui_q.put(("continuous",)),
                             checked=lambda _item: self.continuous),
            pystray.MenuItem("Live typing", lambda: self.ui_q.put(("toggle_live",)),
                             checked=lambda _item: bool(self.cfg["live_typing"])),
            pystray.MenuItem("Set up AI clean-up…", lambda: self.ui_q.put(("setup",))),
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
        if self.pending_setup:
            self.open_setup(reason=self.pending_setup)

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
        text += "  Live typing is %s (%s toggles)." % ("ON" if self.cfg["live_typing"] else "off",
                                                      hotkey.format_combo(self.cfg["live_hotkey"]))
        self.hint_var.set(text)

    def show_overlay(self, mode, text=""):
        self.indicator.live = bool(self.cfg["live_typing"])
        kind = {"rec": "listening", "locked": "locked", "message": "message", "continuous": "continuous"}.get(mode)
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
            oneshots[hotkey.parse_combo(self.cfg["live_pause_hotkey"])] = lambda: self.ui_q.put(("continuous",))
        except ValueError:
            pass
        try:
            oneshots[hotkey.parse_combo(self.cfg["live_hotkey"])] = lambda: self.ui_q.put(("toggle_live",))
        except ValueError:
            pass
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
                if self.continuous and cmd in ("start", "stop", "cancel"):
                    continue  # the dictation hotkey is ignored while continuous live mode runs
                if cmd == "continuous":
                    self._toggle_continuous()
                    continue
                if cmd == "start":
                    if self.recorder.active:
                        continue
                    apps.remember(apps.foreground())
                    if IS_WIN and "cmd" in hotkey.parse_combo(self.cfg["hotkey"]):
                        platform_fix.mask_windows_key()
                    self.recorder.start()
                    self.record_started = time.monotonic()
                    if self.cfg["live_typing"]:
                        self.live_active = True
                        self.live_q.put(("begin",))
                    if self.cfg["sounds"]:
                        audio.play(audio.tone(880))
                    self.ui_q.put(("recording",))
                elif cmd == "stop":
                    samples = self.recorder.stop()
                    seconds = time.monotonic() - self.record_started
                    if self.cfg["sounds"]:
                        audio.play(audio.tone(660))
                    if self.live_active:
                        self.live_active = False
                        self.ui_q.put(("transcribing",))
                        self.live_q.put(("finish", samples, seconds, self.recorder.offset))
                        continue
                    if len(samples) < audio.SAMPLE_RATE * 0.25:
                        self.ui_q.put(("idle",))
                        continue
                    self.ui_q.put(("transcribing",))
                    self.work_q.put((samples, seconds))
                elif cmd == "cancel":
                    self.recorder.stop(keep_tail=False)
                    if self.live_active:
                        self.live_active = False
                        self.live_q.put(("cancel",))
                    self.ui_q.put(("idle",))
                elif cmd == "paste_last":
                    last = self.history.last()
                    if last:
                        self._wait_for_keys_up()
                        if self._may_type():
                            self.paster.deliver(last["text"], True, self.cfg["restore_clipboard"])
                        else:
                            output.set_clipboard(last["text"])
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
                cfg = self._run_cfg()
                result = pipeline.process(samples, cfg, self.transcriber,
                                          on_stage=lambda _s: self.ui_q.put(("overlay", "busy", "Cleaning up…")))
                log.info("dictation: audio %.1fs peak %.3f rms %.4f | %s %.2fs%s | %d chars%s",
                         st["seconds"], st["peak"], st["rms"], result["engine"], result["stt_s"],
                         " + clean-up %.2fs" % result["polish_s"] if result["polished"] else "",
                         len(result["text"]), " | " + result["note"] if result["note"] else "")
                polish_error = next((n for n in result["note"].split("; ") if n.startswith("AI clean-up failed")), "")
                if result["note"] and not polish_error:
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
                if self._may_type():
                    self.paster.deliver(text, self.cfg["auto_paste"], self.cfg["restore_clipboard"])
                else:
                    output.set_clipboard(text)  # still on the clipboard and in history
                self.ui_q.put(("transcript", item))
                self._learn_from(text)
                if polish_error:
                    self.ui_q.put(("polish_failed", polish_error[len("AI clean-up failed ("):-1]))
                elif result["polished"]:
                    self.polish_failures = 0
            except Exception as exc:
                log.exception("transcription failed")
                self.ui_q.put(("error", "Transcription failed: %s" % exc))

    def _run_cfg(self):
        """Settings for one dictation: yours plus what LocalFlow has learned."""
        cfg = dict(self.cfg)
        if self.polish_paused:
            cfg["polish"] = "off"
        cfg = self.dictionary.apply_to(cfg)  # words you taught by voice come first
        if cfg.get("learn"):
            cfg = self.learner.apply_to(cfg)
        return cfg

    # ------------------------------------------------------------------ pronunciation dictionary
    def hear_plain(self, samples):
        """What the current engine hears with NO hints, vocabulary or replacements."""
        cfg = dict(self.cfg, vocabulary=[], replacements={}, polish="off")
        raw, _engine, _note = pipeline.transcribe(samples, cfg, self.transcriber)
        return raw

    def teach_word(self, word, samples):
        """Store one recording of ``word``; returns (what was heard, is it right now?)."""
        heard = self.hear_plain(samples)
        common = lambda phrase: all(not self.transcriber.is_uncommon(w) for w in phrase.split())  # noqa: E731
        self.dictionary.add_take(word, samples, heard, is_common=common)
        cfg = dict(self._run_cfg(), polish="off")
        result = pipeline.process(samples, cfg, self.transcriber)
        return heard, word.lower() in result["text"].lower()

    # ------------------------------------------------------------------ backup
    def reload_data(self):
        """After a restore: re-read everything from the data folder."""
        self.cfg.clear()
        self.cfg.update(config.load())
        self.history = History(self.cfg["history_limit"])
        self.learner = Learner()
        self.dictionary.reload()
        self.settings.load(self.cfg)
        self.refresh_history()
        self.dictionary_tab.refresh()
        self.start_listener()
        self._open_mic()
        self._update_hint()

    def _learn_from(self, text):
        if not self.cfg.get("learn"):
            return
        try:
            promoted = self.learner.observe(text, self.transcriber.is_uncommon)
        except Exception:
            log.exception("learning failed")
            return
        if promoted:
            self.ui_q.put(("learned", "Learned new word%s: %s" % ("s" if len(promoted) > 1 else "",
                                                                  ", ".join(promoted))))

    def _may_type(self, notify=True):
        """Is the focused app one LocalFlow may type/paste into (Settings → Apps)?"""
        app = apps.foreground()
        if apps.allowed(app, self.cfg):
            self._blocked_notice = None
            return True
        name = apps.label(app)
        if notify and self._blocked_notice != name:
            self._blocked_notice = name
            self.ui_q.put(("blocked", name))
        return False

    def _toggle_continuous(self, reason=""):
        """Runs on the control thread: start or pause always-on live listening."""
        if self.continuous:
            self.continuous = False
            samples = self.recorder.stop()
            seconds = time.monotonic() - self.record_started
            if self.cfg["sounds"]:
                audio.play(audio.tone(660))
            self.live_q.put(("finish", samples, seconds, self.recorder.offset))
            self.ui_q.put(("continuous_off", reason))
            return
        if self.recorder.active:
            return  # a hotkey dictation is in progress
        apps.remember(apps.foreground())
        self.recorder.start()
        self.record_started = time.monotonic()
        self.continuous = True
        self.live_q.put(("begin", True))
        if self.cfg["sounds"]:
            audio.play(audio.tone(880))
        self.ui_q.put(("continuous_on",))

    def _live_loop(self):
        """Live typing: while recording, commit and type words about once a second."""
        while True:
            msg = self.live_q.get()
            if msg[0] != "begin":
                continue
            continuous = len(msg) > 1 and msg[1]
            cfg = self._run_cfg()
            session = LiveSession(lambda a, lang, prompt: self.transcriber.transcribe_words(a, lang, prompt),
                                  cfg, speech_in=speech_in)
            last_pass = time.monotonic()
            auto_paused = False
            self._blocked_notice = None
            try:
                while True:
                    try:
                        msg = self.live_q.get(timeout=0.2)
                    except queue.Empty:
                        msg = None
                    if msg and msg[0] == "cancel":
                        break  # words already typed stay; the rest is discarded
                    if msg and msg[0] == "finish":
                        self._live_finish(session, msg[1], msg[2], msg[3])
                        break
                    if time.monotonic() - last_pass < 0.8 or not self.transcriber.ready.is_set():
                        continue
                    last_pass = time.monotonic()
                    samples, offset = self.recorder.peek()
                    piece = session.update(samples, offset)
                    if continuous:
                        self.recorder.trim_before(session.buffer_start - 0.5)
                        limit = float(self.cfg.get("live_auto_pause_min") or 0) * 60
                        heard = offset + len(samples) / audio.SAMPLE_RATE
                        if limit and not auto_paused and heard - session.last_speech > limit:
                            auto_paused = True
                            self.ctl_q.put(("continuous",))
                            self.ui_q.put(("status_warn", "Live listening paused after %g min of silence."
                                           % self.cfg["live_auto_pause_min"]))
                    if piece and self._may_type():
                        typer.type_text(piece, held=tuple(self.listener.machine.pressed) if self.listener else ())
            except Exception as exc:
                log.exception("live typing failed")
                self.ui_q.put(("error", "Live typing failed: %s" % exc))

    def _live_finish(self, session, samples, seconds, offset=0.0):
        if not self.transcriber.ready.wait(timeout=600):
            raise RuntimeError("model is still loading")
        if self.cfg["save_last_recording"]:
            audio.save_wav(os.path.join(paths.data_dir(), "last-recording.wav"), samples)
        rest = session.finish(samples, offset)
        self._wait_for_keys_up(timeout=1.0)
        if rest and self._may_type():
            typer.type_text(rest, held=tuple(self.listener.machine.pressed) if self.listener else ())
        text = session.typed
        log.info("live dictation: audio %.1fs | %d chars", seconds, len(text))
        if not text.strip():
            self.ui_q.put(("idle",))
            return
        item = self.history.add(text, seconds, "live:%s" % self.transcriber.model_name)
        self.ui_q.put(("transcript", item))
        self._learn_from(text)

    def toggle_live(self):
        new = dict(self.cfg, live_typing=not self.cfg["live_typing"])
        self.apply_settings(new)
        self.settings.set_quietly("live_typing", self.cfg["live_typing"])  # keep other unsaved edits
        state = "on" if self.cfg["live_typing"] else "off"
        self.set_status("Live typing %s" % state)
        self.show_overlay("message", "Live typing %s" % state)

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
        if self.continuous and self.indicator.mode is None:
            self.show_overlay("continuous")  # a temporary message just expired
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
            if self.continuous:
                self.show_overlay("continuous")
            else:
                self.hide_overlay()
        elif kind == "transcript":
            if self.indicator.mode != "message":  # keep e.g. "Live listening paused" visible
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
        elif kind == "polish_failed":
            self._polish_failed(ev[1])
        elif kind == "call":
            ev[1]()
        elif kind == "dictionary_changed":
            self.dictionary_tab.refresh(ev[1])
            if ev[2]:
                self.set_status(ev[2])
        elif kind == "setvar":
            self.settings.vars[ev[1]].set(ev[2])
        elif kind == "status_warn":
            self.set_status(ev[1], warn=True)
        elif kind == "captured":
            ev[1].set(ev[2])
        elif kind == "show":
            self.show_window()
        elif kind == "setup":
            self.open_setup()
        elif kind == "learned":
            self.set_status(ev[1])
            self.settings.refresh_learned()
        elif kind == "continuous_on":
            self.show_overlay("continuous")
            self.set_status("Live listening · %s pauses" % hotkey.format_combo(self.cfg["live_pause_hotkey"]))
        elif kind == "continuous_off":
            self.show_overlay("message", "Live listening paused")
            self.set_status(ev[1] or "Live listening paused · %s resumes"
                            % hotkey.format_combo(self.cfg["live_pause_hotkey"]))
        elif kind == "blocked":
            if self.indicator.mode in (None, "message", "busy", "polish"):
                self.show_overlay("message", "Not typing into %s · copied instead" % ev[1][:30])
            self.set_status("%s isn't in your allowed apps (Settings → Apps); text copied instead." % ev[1],
                            warn=True)
        elif kind == "continuous":
            self.ctl_q.put(("continuous",))
        elif kind == "toggle_live":
            self.toggle_live()
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
        self.detail.delete("1.0", "end")
        if item:
            self.detail.insert("1.0", item["text"].rstrip())
            meta = "%s, %.1fs." % (item.get("model", ""), item.get("seconds", 0))
            if item.get("original"):
                meta += "  Corrected from: " + item["original"].strip()
            elif item.get("raw"):
                meta += "  Before clean-up: " + item["raw"].strip()
            self.detail_meta.set(meta + "  Edit above and press Save correction to teach LocalFlow.")
        self.detail.edit_modified(False)
        self.correct_btn.configure(state="disabled")

    def _detail_modified(self, _event):
        if self.detail.edit_modified():
            self.correct_btn.configure(state="normal" if self._selected() else "disabled")

    def save_correction(self):
        item = self._selected()
        if not item:
            return
        old = item["text"]
        new = self.detail.get("1.0", "end-1c").strip()
        if not new or new == old.strip():
            return
        if old.endswith(" ") and not new.endswith("\n"):
            new += " "
        self.history.update(item["id"], text=new, original=item.get("original", old))
        learned = []
        if self.cfg.get("learn"):
            learned = self.learner.learn_correction(old, new, self.transcriber.is_uncommon)
            self.settings.refresh_learned()
        output.set_clipboard(new)
        self.refresh_history()
        if self.tree.exists(item["id"]):
            self.tree.selection_set(item["id"])
        if learned:
            self.set_status("Learned: " + "; ".join("“%s” → “%s”" % pair for pair in learned[:3])
                            + ". Corrected text copied.")
        else:
            self.set_status("Correction saved and copied to the clipboard.")

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
        polish_changed = new["polish"] != self.cfg["polish"]
        if polish_changed:
            self.polish_paused = False
            self.polish_failures = 0
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
        if polish_changed and self.cfg["polish"] != "off" and self.cfg["polish_verified"] != self.cfg["polish"]:
            self.root.after(100, lambda: self.open_setup(reason="Let's check that clean-up works before using it."))

    # ------------------------------------------------------------------ AI clean-up setup
    def _maybe_setup(self):
        if not self.cfg["setup_seen"]:
            self.open_setup()
        elif self.cfg["polish"] != "off" and self.cfg["polish_verified"] != self.cfg["polish"]:
            self.open_setup(reason="AI clean-up is turned on but hasn't been checked on this computer.")

    def open_setup(self, preselect=None, reason=""):
        self.pending_setup = None
        if self.setup_dialog is not None and self.setup_dialog.win.winfo_exists():
            self.setup_dialog.win.lift()
            return
        self.root.deiconify()
        self.setup_dialog = SetupDialog(self, preselect=preselect, reason=reason)

    def _polish_failed(self, reason):
        self.polish_failures += 1
        if self.cfg["polish_verified"]:
            self.cfg["polish_verified"] = ""
            config.save(self.cfg)
        if self.polish_failures >= 2:
            self.polish_paused = True
            msg = "AI clean-up paused after repeated failures (%s). Open LocalFlow to fix it." % reason
        else:
            msg = "AI clean-up failed (%s); pasted the plain transcript." % reason
        self.set_status(msg, warn=True)
        self.show_overlay("message", "Clean-up failed · plain text pasted")
        self.pending_setup = "AI clean-up just failed: %s" % reason

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
                self.recorder.preroll = float(self.cfg["preroll"])
                self.recorder.tail = float(self.cfg["release_tail"])
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
                result = pipeline.process(samples, self._run_cfg(), self.transcriber)
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
