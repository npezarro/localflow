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

from . import __version__, apps, audio, config, drive, edits, feedback, google_auth, sync, hotkey, keystore, output, paths, pipeline, platform_fix, polish, typer
from .history import History
from .dictionary import Dictionary
from .dictionary_ui import DictionaryTab
from .indicator import Indicator
from .learn import Learner
from .live import LiveSession
from .settings_ui import SettingsPanel
from . import quick_menu
from .setup_ui import SetupDialog
from .chunked import ChunkedTranscription
from .transcriber import Transcriber, is_local, speech_in, speech_regions
from . import ui
from .ui import px

log = logging.getLogger(__name__)
IS_MAC = sys.platform == "darwin"
IS_WIN = sys.platform == "win32"
SINGLE_INSTANCE_PORT = 47219

RED = "#ff5a5f"
ROW_COLOURS = {"unchanged": "#dcf3df", "corrected": "#fff1b8", "changed": "#ffdcc7", "wrong": "#ffd2d2"}
# Your own flag (right-click a transcript) wins over what the edit watch decided.
VERDICT_TAG = {"right": "unchanged", "wrong": "wrong", "fixed": "corrected", "confirmed": "corrected"}
IN_APP_TEXT = {"watching": "watching for edits…", "unreadable": "(this app doesn't expose its text)",
               "removed": "(deleted or moved)", "stale": "(not checked)"}


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
        self.chunker = None  # (ChunkedTranscription, stop Event, thread) during a normal dictation
        self.continuous = False  # always-on live listening (pause/resume hotkey)
        self.correcting = None  # {"item", "started"} while recording a spoken correction
        self.last_paste = None  # where the last transcript was pasted, for an in-place correction
        self.edit_watch = edits.EditWatcher(self._edit_watch_result)
        self.account_sync = sync.Sync(self, lambda: drive.AppFolder(google_auth.Session.shared().token))
        self._hook_sync()
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
        self.hotkey_reconnects = 0
        threading.Thread(target=self._hotkey_watchdog, daemon=True, name="hotkey-watchdog").start()
        self._open_mic()
        self.root.after(40, self._poll)
        self.root.after(1200, self._maybe_setup)
        self.root.after(4000, self._startup_update_check)
        self.account_sync.start()
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
        quick = ttk.Menubutton(top, text="Quick settings")
        quick_menu_ = tk.Menu(quick, tearoff=False, postcommand=lambda: quick_menu.build(self, quick_menu_))
        quick.configure(menu=quick_menu_)
        quick.pack(side="right")
        self.quick_menu = quick_menu_
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

        key = ttk.Frame(parent)
        key.pack(fill="x", pady=(6, 0))
        for status, label in (("unchanged", "right as dictated"), ("corrected", "corrected (learned)"),
                              ("changed", "rewritten (not learned)"), ("wrong", "you flagged it wrong")):
            tk.Label(key, text=" %s " % label, background=ROW_COLOURS[status], foreground="#1d1f23",
                     font=("TkDefaultFont", 8)).pack(side="left", padx=(0, 6))
        ttk.Label(key, text="Right-click a transcript to flag it.", foreground="#888",
                  font=("TkDefaultFont", 8)).pack(side="left", padx=(6, 0))
        pane = ttk.PanedWindow(parent, orient="vertical")
        pane.pack(fill="both", expand=True, pady=(6, 0))
        frame = ttk.Frame(pane)
        self.tree = ttk.Treeview(frame, columns=("when", "text", "in_app"), show="headings", selectmode="browse")
        self.tree.heading("when", text="When")
        self.tree.heading("text", text="Transcript (double-click to copy)")
        self.tree.heading("in_app", text="In the app afterwards")
        self.tree.column("when", width=px(110), stretch=False)
        self.tree.column("text", width=px(330))
        self.tree.column("in_app", width=px(330))
        # Row colour = what happened to the transcript in the app you pasted it into.
        for tag, colour in ROW_COLOURS.items():
            self.tree.tag_configure(tag, background=colour, foreground="#1d1f23")
        sb = ttk.Scrollbar(frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.tree.bind("<<TreeviewSelect>>", lambda _e: self._show_selected())
        self.tree.bind("<Double-1>", lambda _e: self.copy_selected())
        for seq in ("<Button-3>",) + (("<Button-2>", "<Control-Button-1>") if IS_MAC else ()):
            self.tree.bind(seq, self._history_menu)
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
            pystray.MenuItem("Restart hotkeys & microphone",
                             lambda: self.ui_q.put(("call", lambda: self.restart_hotkeys(True)))),
            pystray.MenuItem("Restart LocalFlow", lambda: self.ui_q.put(("call", self.restart_app))),
            pystray.MenuItem("Check for updates…",
                             lambda: self.ui_q.put(("call", lambda: self.check_for_updates(True, self.set_status)))),
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
        if self.root.state() == "iconic":
            self.root.state("normal")
        self.root.lift()
        # Briefly topmost so Windows brings it over whatever is in front, then back to normal.
        self.root.attributes("-topmost", True)
        self.root.after(300, lambda: self.root.attributes("-topmost", False))
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
        text += "  Correct by voice: %s." % hotkey.format_combo(self.cfg["feedback_hotkey"])
        text += "  Live typing is %s (%s toggles)." % ("ON" if self.cfg["live_typing"] else "off",
                                                      hotkey.format_combo(self.cfg["live_hotkey"]))
        self.hint_var.set(text)

    def show_overlay(self, mode, text=""):
        self.indicator.live = bool(self.cfg["live_typing"])
        kind = {"rec": "listening", "locked": "locked", "message": "message", "continuous": "continuous",
                "correcting": "correcting"}.get(mode)
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
        try:
            oneshots[hotkey.parse_combo(self.cfg["feedback_hotkey"])] = lambda: self.ctl_q.put(("feedback",))
        except ValueError:
            pass
        self.listener = hotkey.HotkeyListener(machine, oneshots)
        self.listener.start()

    def _hotkey_watchdog(self, tick=2.0, probe_every=20):
        """Keep the hotkeys alive without a Settings save: forget keys whose release we never
        saw (stuck keys stop the hotkey matching), and reconnect if the OS stopped sending us
        keys at all."""
        since_probe = 0.0
        while True:
            time.sleep(tick)
            since_probe += tick
            lst = self.listener
            if not lst or lst._capture:
                continue
            try:
                stuck = lst.clear_stale()
            except Exception:
                log.exception("stuck-key check failed")
                stuck = []
            if stuck:
                log.warning("cleared stuck key(s) %s: their release was never seen (lock screen, "
                            "Ctrl+Alt+Del or an admin prompt?)", ", ".join(stuck))
            if since_probe < probe_every or lst.machine.state != hotkey.IDLE or lst.machine.pressed:
                continue  # probe only when idle and no keys are held
            since_probe = 0.0
            try:
                # A busy moment can delay the answer; only a second, longer miss means dead.
                ok = lst.healthy() or lst.healthy(wait=4)
            except Exception:
                log.exception("hotkey health check failed")
                continue
            if not ok and lst is self.listener:
                self.hotkey_reconnects += 1
                log.warning("hotkeys stopped responding; reconnecting (#%d this session)", self.hotkey_reconnects)
                self.ui_q.put(("call", self.restart_hotkeys))

    def restart_hotkeys(self, announce=False):
        """Recreate the keyboard listener and reopen the microphone (what saving Settings did)."""
        self.start_listener()
        self._open_mic()
        if announce:
            self.set_status("Hotkeys and microphone restarted")
            self.show_overlay("message", "Hotkeys and microphone restarted")

    def restart_app(self):
        """Start a fresh copy of LocalFlow, then quit this one."""
        if not self.settings.confirm_leave():
            return
        from . import update

        cmd = [sys.executable] if paths.is_frozen() else [sys.executable, os.path.abspath(sys.argv[0])]
        env_key = "LOCALFLOW_RESTART_FROM"
        os.environ[env_key] = str(os.getpid())  # the new copy waits for this one to exit
        try:
            update._spawn_detached(cmd)
        finally:
            os.environ.pop(env_key, None)
        self.quit()

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
                if cmd == "feedback":
                    self._toggle_feedback()
                    continue
                if self.correcting and cmd in ("start", "stop", "cancel"):
                    if cmd == "cancel":
                        self.correcting = None
                        self.recorder.stop(keep_tail=False)
                        self.ui_q.put(("idle",))
                    continue  # the dictation hotkey waits until the correction is finished
                if self.continuous and cmd in ("start", "stop", "cancel"):
                    continue  # the dictation hotkey is ignored while continuous live mode runs
                if cmd == "continuous":
                    self._toggle_continuous()
                    continue
                if cmd == "start":
                    if self.recorder.active:
                        continue
                    self.edit_watch.stop()  # a new dictation: finish learning from the last one
                    apps.remember(apps.foreground())
                    if IS_WIN and "cmd" in hotkey.parse_combo(self.cfg["hotkey"]):
                        platform_fix.mask_windows_key()
                    self.recorder.start()
                    self.record_started = time.monotonic()
                    if self.cfg["live_typing"]:
                        self.live_active = True
                        self.live_q.put(("begin",))
                    elif self.cfg["background_transcribe"] and self.cfg["engine"] == "local":
                        self._start_chunker()
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
                    chunker, self.chunker = self.chunker, None
                    if chunker:
                        chunker[1].set()  # stop cutting; the work loop finishes it
                    if len(samples) < audio.SAMPLE_RATE * 0.25:
                        self.ui_q.put(("idle",))
                        continue
                    self.ui_q.put(("transcribing",))
                    self.work_q.put((samples, seconds, chunker))
                elif cmd == "cancel":
                    self.recorder.stop(keep_tail=False)
                    if self.chunker:
                        self.chunker[1].set()
                        self.chunker = None
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

    def _start_chunker(self):
        """Transcribe finished stretches in the background while the user is still talking."""
        cfg = self._run_cfg()
        chunker = ChunkedTranscription(
            lambda x, ctx: self.transcriber.transcribe(audio.normalize(x), cfg["language"], cfg["vocabulary"],
                                                       cfg["beam_size"], context=ctx),
            speech_regions)
        stop = threading.Event()

        def run():
            while not stop.is_set():
                if not self.transcriber.ready.is_set():
                    stop.wait(0.3)
                    continue
                try:
                    samples, offset = self.recorder.peek()
                    if not chunker.update(samples, offset):
                        stop.wait(0.4)
                except Exception:
                    log.exception("background transcription failed")
                    return

        thread = threading.Thread(target=run, daemon=True, name="chunker")
        thread.start()
        self.chunker = (chunker, stop, thread)

    def _work_loop(self):
        while True:
            job = self.work_q.get()
            if isinstance(job[0], str) and job[0] == "feedback":
                try:
                    self._apply_feedback(job[1], job[2])
                except Exception as exc:
                    log.exception("correction failed")
                    self.ui_q.put(("error", "Correction failed: %s" % exc))
                continue
            samples, seconds, chunker = job
            try:
                st = audio.stats(samples)
                if self.cfg["save_last_recording"]:
                    audio.save_wav(os.path.join(paths.data_dir(), "last-recording.wav"), samples)
                cfg = self._run_cfg()
                raw = None
                if chunker:
                    chunker[2].join()  # let an in-flight chunk finish
                    if self.transcriber.ready.is_set():
                        raw = chunker[0].finish(audio.normalize(samples))
                        log.info("while-talking chunks: %d", chunker[0].chunks_done)
                result = pipeline.process(samples, cfg, self.transcriber, raw=raw,
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
                    if self.cfg["auto_paste"]:
                        self._note_paste(item["id"], text)
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

    # ------------------------------------------------------------------ spoken corrections
    def _note_paste(self, item_id, text=None):
        app = apps.foreground()
        self.last_paste = {"id": item_id, "app": app, "t": time.monotonic(),
                           "keys": self.listener.user_keys if self.listener else -1}
        if text:
            self._watch_edits(item_id, text, app)

    def _watch_edits(self, item_id, text, app=None):
        """Keep an eye on the field we just wrote into, to learn from the user's fixes."""
        app = app or apps.foreground()
        if self.cfg.get("learn") and self.cfg.get("learn_from_edits") and not apps.is_terminal(app):
            self.history.update(item_id, in_app={"status": "watching", "app": apps.label(app), "at": time.time()})
            self.edit_watch.watch(item_id, text, apps.label(app))

    def _edit_watch_result(self, item_id, old, status, final, app_name):
        """Runs on the watcher thread when a watch ends: keep what the transcript looked like in
        the app (so every learned fix can be checked in Transcripts), and learn real fixes."""
        item = next((i for i in self.history.items if i["id"] == item_id), None)
        if not item:
            return
        in_app = {"status": status, "final": final, "app": app_name or "", "at": time.time()}
        if status != "corrected":
            self.history.update(item_id, in_app=in_app)
            self.ui_q.put(("call", self.refresh_history))
            return
        new = final
        new_text = new + (" " if item["text"].endswith(" ") else "")
        self.history.update(item_id, text=new_text, original=item.get("original", item["text"]),
                            edited_in_app=True, in_app=in_app)
        # An edit may be a change of mind, not a mishearing: it becomes a rule on the 2nd time.
        learned = self.learner.learn_correction(old, new, self.transcriber.is_uncommon, confirm_after=2) \
            if self.cfg.get("learn") else []
        pairs = learned or edits.words_changed(old, new)
        shown = "; ".join("“%s” → “%s”" % p for p in pairs[:3])
        msg = ("Learned from your edit: " + shown) if learned else \
            ("Noted your edit (%s); it's learned if you make the same fix again" % shown)
        self.ui_q.put(("edit_learned", msg, True))

    def _can_fix_in_place(self, item):
        """Undo + paste is only safe right after our own paste, in the same app, with no typing since."""
        lp = self.last_paste
        if not (self.cfg["feedback_fix_in_place"] and lp and lp["id"] == item["id"] and self.listener):
            return False
        now = apps.foreground()
        if not now or not lp["app"] or now.get("id") != lp["app"].get("id") or \
                now.get("title") != lp["app"].get("title"):
            return False
        if apps.is_terminal(now):  # Ctrl+Z suspends there instead of undoing
            return False
        return lp["keys"] == self.listener.user_keys and time.monotonic() - lp["t"] < 180

    def _toggle_feedback(self):
        """Runs on the control thread: start or finish recording a spoken correction."""
        if self.correcting:
            info, self.correcting = self.correcting, None
            samples = self.recorder.stop()
            if self.cfg["sounds"]:
                audio.play(audio.tone(660))
            if len(samples) < audio.SAMPLE_RATE * 0.3:
                self.ui_q.put(("idle",))
                return
            self.ui_q.put(("overlay", "busy", "Correcting…"))
            self.work_q.put(("feedback", samples, info))
            return
        if self.recorder.active or self.continuous:
            self.ui_q.put(("overlay", "message", "Finish dictating first, then correct"))
            return
        item = self.history.last()
        if not item:
            self.ui_q.put(("overlay", "message", "Nothing to correct yet"))
            return
        self.recorder.start()
        self.correcting = {"item": item, "started": time.monotonic()}
        if self.cfg["sounds"]:
            audio.play(audio.tone(990))
        self.ui_q.put(("correcting",))

        def time_limit(info=self.correcting):
            if self.correcting is info:
                self.ctl_q.put(("feedback",))  # 30 s is plenty for a correction
        timer = threading.Timer(30, time_limit)
        timer.daemon = True
        timer.start()

    def _apply_feedback(self, samples, info):
        """Runs on the work thread: hear the correction, apply it, save / learn / paste."""
        if not self.transcriber.ready.wait(timeout=600):
            raise RuntimeError("model is still loading")
        item = next((i for i in self.history.items if i["id"] == info["item"]["id"]), info["item"])
        old = item["text"]
        cfg = dict(self._run_cfg(), replacements={}, polish="off")
        spoken, _engine, _note = pipeline.transcribe(samples, cfg, self.transcriber)
        spoken = spoken.strip()
        log.info("spoken correction: %r", spoken)
        if not spoken:
            self.ui_q.put(("error", "Didn't catch the correction"))
            return
        parsed = feedback.parse(spoken)
        explicit = parsed and (parsed[0] or feedback.spelled_out(spoken))
        ai = self.cfg["polish"] != "off" and not self.polish_paused
        new, how = (None, "")
        if explicit or not ai:
            new, how = feedback.correct(old, spoken)
        if new is None and ai:
            self.ui_q.put(("overlay", "polish", "Cleaning up: applying your correction…"))
            try:
                new = polish.correct_with_ai(old, spoken, self._run_cfg(), keystore.get)
                how = "“%s”" % spoken
            except Exception as exc:
                log.warning("AI correction failed: %s", exc)
                new, how = None, "AI couldn't apply “%s”" % spoken
        if new is None or new.strip() == old.strip():
            reason = how or "no change for “%s”" % spoken
            self.ui_q.put(("error", reason[0].upper() + reason[1:]))
            return
        if old.endswith(" ") and not new.endswith((" ", "\n")):
            new += " "
        self.history.update(item["id"], text=new, original=item.get("original", old), verdict="fixed")
        learned = []
        if self.cfg.get("learn"):
            learned = self.learner.learn_correction(old, new, self.transcriber.is_uncommon)
        self._wait_for_keys_up()
        if self._can_fix_in_place(item) and self._may_type(notify=False):
            self.paster.undo_and_paste(new)
            self._note_paste(item["id"], new)
            where = "Fixed"
        else:
            output.set_clipboard(new)
            where = "Corrected, copied"
        log.info("correction applied (%s): %s", where, how)
        self.ui_q.put(("corrected", "%s: %s" % (where, how), bool(learned)))

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

    # ------------------------------------------------------------------ account
    def _hook_sync(self):
        self.learner.on_change = self.account_sync.mark_dirty
        self.dictionary.on_change = self.account_sync.mark_dirty

    def account_ready(self):
        return bool(self.cfg.get("sync_enabled", True)) and google_auth.available() and google_auth.signed_in()

    def sign_in_google(self):
        self.set_status("Finish signing in with Google in your browser…", warn=True)

        def run():
            try:
                email = google_auth.sign_in()
            except Exception as exc:
                msg = "Google sign-in failed: %s" % exc
                self.ui_q.put(("call", lambda: (self.set_status(msg, warn=True), self.settings.refresh_account())))
                return
            self.cfg["account_email"] = email
            config.save(self.cfg)
            error = self.account_sync.sync_now()
            self.ui_q.put(("call", lambda: self._after_sign_in(email, error)))

        threading.Thread(target=run, daemon=True, name="google-sign-in").start()

    def _after_sign_in(self, email, error):
        self.show_window()
        self.settings.refresh_account()
        self.dictionary_tab.refresh()
        self.set_status("Signed in as %s%s" % (email, " · sync problem: %s" % error if error else " · synced"),
                        warn=bool(error))
        if not error and self.account_sync.remote_devices and not self.cfg.get("copied_settings_once"):
            self.cfg["copied_settings_once"] = True
            config.save(self.cfg)
            if messagebox.askyesno("LocalFlow", "Your account has settings from %d other device(s). "
                                   "Copy one's settings to this computer?" % len(self.account_sync.remote_devices),
                                   parent=self.root):
                self.copy_settings_dialog()

    def sign_out_google(self):
        if not messagebox.askyesno("LocalFlow", "Sign out? Your learnings and dictionary stay on this computer "
                                   "and in your account; they just stop syncing here.", parent=self.root):
            return
        google_auth.sign_out()
        self.cfg["account_email"] = ""
        config.save(self.cfg)
        self.settings.refresh_account()
        self.set_status("Signed out of Google")

    def sync_now_ui(self):
        self.set_status("Syncing…")

        def run():
            error = self.account_sync.sync_now()
            self.ui_q.put(("call", lambda: (self.settings.refresh_account(), self.dictionary_tab.refresh(),
                                            self.set_status("Sync problem: %s" % error if error else "Synced",
                                                            warn=bool(error)))))

        threading.Thread(target=run, daemon=True, name="sync-now").start()

    def copy_settings_dialog(self):
        devices = self.account_sync.remote_devices
        if not devices:
            messagebox.showinfo("LocalFlow", "No other devices have synced to your account yet. Sign in on "
                                "another computer first.", parent=self.root)
            return
        win = tk.Toplevel(self.root)
        win.title("Copy settings from another device")
        win.transient(self.root)
        ttk.Label(win, padding=(12, 10, 12, 4), wraplength=px(420), justify="left",
                  text="Replace this computer's settings with another device's. Its microphone, processor "
                       "and tool paths aren't copied; hotkeys are only copied between the same kind of "
                       "computer (Windows or Mac). Your API keys stay where they are.").pack(fill="x")
        choice = tk.IntVar(value=0)
        for i, d in enumerate(devices):
            when = time.strftime("%b %d %H:%M", time.localtime(d.get("updated", 0)))
            ttk.Radiobutton(win, variable=choice, value=i,
                            text="%s · %s · LocalFlow %s · last seen %s" % (d.get("name", "?"), d.get("platform", "?"),
                                                                          d.get("version", "?"), when)
                            ).pack(anchor="w", padx=16, pady=2)

        def copy():
            d = devices[choice.get()]
            new, skipped = sync.settings_to_copy(d.get("settings") or {}, d.get("platform"), self.cfg)
            win.destroy()
            self.apply_settings(new)
            self.settings.load(self.cfg)
            note = " (hotkeys kept: that's a %s)" % d.get("platform") if skipped else ""
            self.set_status("Copied settings from %s%s" % (d.get("name", "that device"), note))

        row = ttk.Frame(win, padding=12)
        row.pack(fill="x")
        ttk.Button(row, text="Copy settings", command=copy).pack(side="right")
        ttk.Button(row, text="Cancel", command=win.destroy).pack(side="right", padx=6)

    # ------------------------------------------------------------------ backup
    def reload_data(self):
        """After a restore: re-read everything from the data folder."""
        self.cfg.clear()
        self.cfg.update(config.load())
        self.history = History(self.cfg["history_limit"])
        self.learner = Learner()
        self.dictionary.reload()
        self._hook_sync()
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
        self._watch_edits(item["id"], text)
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
                self.transcriber.load(name, device=self.cfg.get("device", "auto"))
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
        elif kind == "correcting":
            self.show_overlay("correcting", "")
            self.set_status("Say the correction, then press %s again"
                            % hotkey.format_combo(self.cfg["feedback_hotkey"]))
        elif kind == "edit_learned":  # quiet: just the status line, no pill minutes after pasting
            self.set_status(ev[1])
            self.refresh_history()
            if ev[2]:
                self.settings.refresh_learned()
        elif kind == "corrected":
            self.show_overlay("message", ev[1][:70])
            self.set_status(ev[1])
            self.refresh_history()
            if ev[2]:
                self.settings.refresh_learned()
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
            self.settings.refresh_gpu()
            self.settings.refresh_apple()
            self.root.after(300, self._check_gpu)
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
            ia = item.get("in_app") or {}
            status = ia.get("status", "")
            if status == "watching" and time.time() - ia.get("at", 0) > edits.WATCH_SECONDS + 60:
                status = "stale"  # LocalFlow closed while it was watching
            # Left: what LocalFlow wrote. Right: what it looked like in the app at the end.
            wrote = item.get("original") if item.get("edited_in_app") else item["text"]
            if status in ("unchanged", "corrected", "changed", "not_a_correction"):
                after = (ia.get("final") or "") + ("   (you said: not a correction)" if status == "not_a_correction" else "")
            else:
                after = IN_APP_TEXT.get(status, "")
            tag = VERDICT_TAG.get(item.get("verdict")) or (status if status in ROW_COLOURS else None)
            if q and q not in (wrote + " " + (after or "")).lower():
                continue
            when = datetime.fromtimestamp(item["ts"]).strftime("%b %d %H:%M")
            self.tree.insert("", "end", iid=item["id"], tags=(tag,) if tag else (),
                             values=(when, wrote.replace("\n", " ⏎ "), (after or "").replace("\n", " ⏎ ")))

    # ------------------------------------------------------------------ flagging transcripts
    def _history_menu(self, event):
        iid = self.tree.identify_row(event.y)
        if not iid:
            return
        self.tree.selection_set(iid)
        self.tree.focus(iid)
        item = self._selected()
        if not item:
            return
        m = tk.Menu(self.root, tearoff=False)
        if item.get("edited_in_app"):
            m.add_command(label="Not a correction: undo what LocalFlow learned from it",
                          command=lambda: self.dismiss_edit(iid))
            if self.learner.is_pending(item.get("original") or "", item["text"]):
                m.add_command(label="Yes, a real correction: learn it now", command=lambda: self.confirm_edit(iid))
            m.add_separator()
        m.add_command(label="Transcript was right", command=lambda: self.set_verdict(iid, "right"))
        m.add_command(label="Transcript was wrong: fix it…", command=lambda: self.flag_wrong(iid))
        if item.get("verdict"):
            m.add_command(label="Clear my flag", command=lambda: self.set_verdict(iid, None))
        m.add_separator()
        m.add_command(label="Copy", command=self.copy_selected)
        m.add_command(label="Delete", command=self.delete_selected)
        try:
            m.tk_popup(event.x_root, event.y_root)
        finally:
            m.grab_release()

    def _item(self, iid):
        return next((i for i in self.history.items if i["id"] == iid), None)

    def _reselect(self, iid, status):
        self.refresh_history()
        if self.tree.exists(iid):
            self.tree.selection_set(iid)
        self.set_status(status)

    def set_verdict(self, iid, verdict):
        self.history.update(iid, verdict=verdict)
        self._reselect(iid, {"right": "Marked right.", "wrong": "Marked wrong."}.get(verdict, "Flag cleared."))

    def dismiss_edit(self, iid):
        """The edit watch called something a correction that wasn't: restore and un-learn."""
        item = self._item(iid)
        if not item or not item.get("edited_in_app"):
            return
        original, edited = item.get("original") or item["text"], item["text"]
        undone = self.learner.unlearn_correction(original.strip(), edited.strip())
        in_app = dict(item.get("in_app") or {}, status="not_a_correction")
        self.history.update(iid, text=original, original=None, edited_in_app=False, in_app=in_app, verdict="right")
        self.settings.refresh_learned()
        self._reselect(iid, "Not a correction: " + ("undid " + "; ".join("“%s” → “%s”" % p for p in undone)
                                                     if undone else "nothing had been learned from it") + ".")

    def confirm_edit(self, iid):
        item = self._item(iid)
        if not item or not item.get("original"):
            return
        learned = self.learner.learn_correction(item["original"].strip(), item["text"].strip(),
                                                self.transcriber.is_uncommon, confirm_after=1)
        self.history.update(iid, verdict="confirmed")
        self.settings.refresh_learned()
        self._reselect(iid, "Learned: " + "; ".join("“%s” → “%s”" % p for p in learned))

    def flag_wrong(self, iid):
        self.history.update(iid, verdict="wrong")
        self._reselect(iid, "Marked wrong: fix the text below and press Save correction to teach LocalFlow.")
        self.detail.focus_set()
        self.detail.mark_set("insert", "1.0")

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
                how = " (you edited it in %s)" % (item.get("in_app") or {}).get("app", "the app") \
                    if item.get("edited_in_app") else ""
                meta += "  Corrected%s from: %s" % (how, item["original"].strip())
            elif item.get("raw"):
                meta += "  Before clean-up: " + item["raw"].strip()
            ia = item.get("in_app")
            if ia and not item.get("edited_in_app"):
                where = ia.get("app") or "the app"
                meta += "  " + {
                    "unchanged": "Left as is in %s." % where,
                    "changed": "Rewritten in %s (not learned): %s" % (where, (ia.get("final") or "").strip()),
                    "removed": "Deleted or moved in %s." % where,
                    "unreadable": "%s doesn't expose its text, so edits there can't be learned." % where,
                }.get(ia["status"], "")
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
        self.history.update(item["id"], text=new, original=item.get("original", old), verdict="fixed")
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
    def quick_set(self, key, value):
        """A change from the quick menu or tray: save now, keep the Settings form in step."""
        if self.cfg.get(key) == value:
            return
        self.apply_settings(dict(self.cfg, **{key: value}))
        self.settings.reflect(key)

    def apply_settings(self, new):
        model_changed = new["model"] != self.cfg["model"] or new.get("device") != self.cfg.get("device")
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
            label = "%s · %s" % (self.cfg["model"], {"cuda": "NVIDIA GPU", "apple": "Apple GPU"}.get(
                self.transcriber.device, "CPU"))
        if self.cfg["polish"] != "off":
            label += " + %s clean-up" % self.cfg["polish"]
        return label

    # ------------------------------------------------------------------ speed
    def speed_test(self):
        """Time the current engine on the bundled 11-second sample."""
        def run():
            try:
                if not self.transcriber.ready.wait(timeout=600):
                    raise RuntimeError("model is still loading")
                sample = audio.load_wav(os.path.join(paths.bundle_dir(), "samples", "jfk.wav"))
                cfg = self._run_cfg()
                self.transcriber.transcribe(sample[:16000 * 2], cfg["language"], None, cfg["beam_size"])  # warm up
                t0 = time.time()
                text = self.transcriber.transcribe(sample, cfg["language"], None, cfg["beam_size"])
                took = time.time() - t0
                msg = ("%s on the %s, %s search: %.1f s for 11 s of speech.\n\n\"%s\"\n\n"
                       "That is roughly your wait after a short dictation. With 'Transcribe while I talk', "
                       "long dictations wait only for the part since your last pause."
                       % (self.transcriber.model_name, "GPU" if self.transcriber.device == "cuda" else "CPU",
                          "fast" if cfg["beam_size"] == 1 else "thorough", took, text.strip()))
                if self.transcriber.device_error:
                    msg += "\n\nGPU not used: " + self.transcriber.device_error
            except Exception as exc:
                msg = "Speed test failed: %s" % exc
            self.ui_q.put(("dialog", "Speed test", msg))

        threading.Thread(target=run, daemon=True, name="speed-test").start()

    def _gpu_situation(self):
        """-> (kind, problem). kind: "nvidia"/"apple" if this computer has a GPU LocalFlow could
        use; problem: None if it's in use, "missing" if its support isn't downloaded, else why not."""
        from . import apple_gpu, gpu

        t = self.transcriber
        if apple_gpu.IS_APPLE_SILICON:
            if t.device == "apple":
                return "apple", None
            if not apple_gpu.installed():
                return "apple", "missing"
            if not apple_gpu.supports(self.cfg["model"]):
                return "apple", "%s has no Apple GPU version" % self.cfg["model"]
            return "apple", t.device_error or "it didn't load"
        if IS_WIN and gpu.nvidia_present():
            if t.device == "cuda":
                return "nvidia", None
            if not gpu.libraries_present() and (not t.device_error or "not found" in t.device_error
                                                or "cannot be loaded" in t.device_error):
                return "nvidia", "missing"
            return "nvidia", t.device_error or "it didn't load"
        return None, "no compatible GPU was found on this computer"

    def _check_gpu(self):
        """After a model loads: offer GPU support, or say plainly why a chosen GPU isn't used."""
        want = self.cfg.get("device", "auto")
        if want == "cpu" or self.transcriber.device in ("cuda", "apple"):
            return
        kind, problem = self._gpu_situation()
        name = {"nvidia": "NVIDIA GPU", "apple": "Apple GPU"}.get(kind, "GPU")
        size = {"nvidia": "about 1 GB", "apple": "about 45 MB"}.get(kind, "")
        if problem == "missing":
            if want == "auto" and self.cfg.get("gpu_offer_declined"):
                return
            if messagebox.askyesno("LocalFlow", "This computer has an %s, but LocalFlow is transcribing on the "
                                   "CPU because GPU support isn't downloaded yet.\n\nDownload it now (%s, once)? "
                                   "Larger models like large-v3-turbo get several times faster." % (name, size),
                                   parent=self.root):
                status = lambda msg: self.set_status(msg)  # noqa: E731
                (self.download_apple_gpu if kind == "apple" else self.download_gpu)(status)
            elif want == "auto":
                self.cfg["gpu_offer_declined"] = True
                config.save(self.cfg)
            else:
                self.set_status("%s chosen, but its support isn't downloaded: transcribing on the CPU "
                                "(Settings → Transcription)." % name, warn=True)
            return
        if want in ("cuda", "apple"):  # chosen explicitly but not working: say so
            msg = "%s isn't being used: %s. Transcribing on the CPU for now." % (name, problem)
            self.set_status(msg, warn=True)
            self.show_overlay("message", "GPU not in use: transcribing on the CPU")
            messagebox.showwarning("LocalFlow", msg, parent=self.root)

    def download_apple_gpu(self, progress):
        """Fetch MLX (Apple Silicon GPU), then reload the model on the GPU."""
        from . import apple_gpu

        def run():
            try:
                mb = apple_gpu.download(lambda msg, frac: self.ui_q.put(
                    ("call", lambda: progress("%s… %d%%" % (msg, frac * 100)))))
                self.ui_q.put(("call", lambda: progress("Installed (%d MB). Loading the model on the GPU "
                                                         "(it downloads once in MLX format)…" % mb)))
                self.ui_q.put(("call", lambda: self.load_model(self.cfg["model"])))
            except Exception as exc:
                err = "Download failed: %s" % exc
                self.ui_q.put(("call", lambda: progress(err)))

        threading.Thread(target=run, daemon=True, name="apple-gpu-download").start()

    def download_gpu(self, progress):
        """Fetch GPU support, then reload the model on the GPU. Runs off the UI thread."""
        from . import gpu

        def run():
            try:
                mb = gpu.download(lambda msg, frac: self.ui_q.put(("call", lambda: progress("%s… %d%%" % (msg, frac * 100)))))
                self.ui_q.put(("call", lambda: progress("Installed (%d MB). Loading the model on the GPU…" % mb)))
                self.ui_q.put(("call", lambda: self.load_model(self.cfg["model"])))
            except Exception as exc:
                err = "Download failed: %s" % exc
                self.ui_q.put(("call", lambda: progress(err)))

        threading.Thread(target=run, daemon=True, name="gpu-download").start()

    # ------------------------------------------------------------------ updates
    def _startup_update_check(self):
        if not self.cfg.get("auto_update_check") or time.time() - float(self.cfg.get("last_update_check") or 0) < 86400:
            return
        self.check_for_updates(interactive=False)

    def check_for_updates(self, interactive=True, on_status=None):
        from . import update

        say = on_status or (lambda msg: None)
        say("Checking GitHub for a newer version…")

        def run():
            try:
                info = update.check()
                self.cfg["last_update_check"] = time.time()
                config.save(self.cfg)
            except Exception as exc:
                err = "Couldn't check for updates: %s" % str(exc)[:120]
                self.ui_q.put(("call", lambda: say(err)))
                if interactive:
                    self.ui_q.put(("dialog", "Updates", err))
                return
            if info["available"]:
                self.ui_q.put(("call", lambda: (say("LocalFlow %s is available (you have %s)." % (info["latest"], info["current"])),
                                                self._offer_update(info))))
            else:
                msg = "You're up to date (LocalFlow %s)." % info["current"]
                self.ui_q.put(("call", lambda: say(msg)))
                if interactive:
                    self.ui_q.put(("dialog", "Updates", msg))

        threading.Thread(target=run, daemon=True, name="update-check").start()

    def _offer_update(self, info):
        from . import update

        if info["kind"] == "source" or not info["asset"]:
            if messagebox.askyesno("LocalFlow", "LocalFlow %s is available (you have %s).\n\nOpen the download page?"
                                   % (info["latest"], info["current"]), parent=self.root):
                import webbrowser

                webbrowser.open(info["page"])
            return
        notes = info["notes"].splitlines()
        summary = "\n".join(line for line in notes[:8] if line.strip())[:600]
        mac_note = ("\n\nmacOS may ask again for Accessibility and Input Monitoring after the update "
                    "(until LocalFlow is signed by Apple)." if IS_MAC else "")
        if not messagebox.askyesno(
                "Update LocalFlow",
                "LocalFlow %s is available (you have %s). %.0f MB download.%s\n\n%s\n\nUpdate now? LocalFlow "
                "restarts when it's done; your settings, history and dictionary are kept."
                % (info["latest"], info["current"], (info["asset"].get("size") or 0) / 1e6, mac_note, summary),
                parent=self.root):
            return
        self.set_status("Downloading LocalFlow %s…" % info["latest"], warn=True)

        def run():
            try:
                done = update.apply(info, lambda msg, frac: self.ui_q.put(("status_warn", "%s %d%%" % (msg, frac * 100))))
                if done:
                    self.ui_q.put(("quit",))
            except Exception as exc:
                err = "Update failed: %s" % exc
                self.ui_q.put(("dialog", "Update LocalFlow", err))
                self.ui_q.put(("status_warn", err))

        threading.Thread(target=run, daemon=True, name="update-apply").start()

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
        sock.listen(4)
        return sock
    except OSError:
        return None


def _serve_show_requests(guard, app):
    """The running copy: a second launch asks it to come to the front instead of starting."""
    def run():
        while True:
            try:
                conn, _addr = guard.accept()
            except OSError:
                return  # closed on exit
            try:
                conn.settimeout(2)
                if conn.recv(16).strip() == b"show":
                    app.ui_q.put(("show",))
                    conn.sendall(b"ok\n")
            except OSError:
                pass
            finally:
                conn.close()

    threading.Thread(target=run, daemon=True, name="single-instance").start()


def _ask_running_copy_to_show():
    """-> True if an already running LocalFlow answered and is bringing its window up."""
    if IS_WIN:
        try:  # we were just launched by the user, so we may hand them the right to take focus
            import ctypes

            ctypes.windll.user32.AllowSetForegroundWindow(-1)  # ASFW_ANY
        except Exception:
            pass
    try:
        with socket.create_connection(("127.0.0.1", SINGLE_INSTANCE_PORT), timeout=3) as conn:
            conn.sendall(b"show\n")
            conn.settimeout(3)
            return conn.recv(8).startswith(b"ok")
    except OSError:
        return False


def main():
    guard = _single_instance()
    if guard is None and os.environ.get("LOCALFLOW_RESTART_FROM"):
        deadline = time.monotonic() + 20  # restarting: wait for the old copy to let go
        while guard is None and time.monotonic() < deadline:
            time.sleep(0.25)
            guard = _single_instance()
    os.environ.pop("LOCALFLOW_RESTART_FROM", None)
    if guard is None:
        if _ask_running_copy_to_show():
            return 0
        root = tk.Tk()
        root.withdraw()
        messagebox.showinfo("LocalFlow", "LocalFlow is already running.")
        return 1
    log.info("LocalFlow %s starting; data dir %s", __version__, paths.data_dir())
    app = App()
    _serve_show_requests(guard, app)
    if "--smoke-ui" in sys.argv:
        _smoke(app)
    app.run()
    guard.close()
    return 0
