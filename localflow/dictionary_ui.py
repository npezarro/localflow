"""Dictionary tab: teach LocalFlow words by recording yourself saying them."""
import threading
import time
import tkinter as tk
from tkinter import messagebox, ttk

from .ui import px

from . import audio
from .dictionary import normalize, variant_for

MUTED = "#6b6f78"
TAKE_SECONDS = 3.0


class DictionaryTab:
    def __init__(self, parent, app):
        self.app = app
        ttk.Label(parent, foreground=MUTED, wraplength=px(720), justify="left",
                  text="Teach LocalFlow words it gets wrong by saying them. Each recording is transcribed "
                       "without hints to learn how LocalFlow hears you (e.g. “Kabir nets”), and "
                       "that is then corrected to your spelling. Recordings are kept in the data folder, "
                       "so after changing model or engine, Re-check re-learns from them.").pack(fill="x")
        bar = ttk.Frame(parent)
        bar.pack(fill="x", pady=(8, 6))
        ttk.Button(bar, text="Add word…", command=self.add_word).pack(side="left")
        ttk.Button(bar, text="Record another take", command=self.record_more).pack(side="left", padx=4)
        ttk.Button(bar, text="Re-check all", command=self.recheck_all).pack(side="left")
        ttk.Button(bar, text="Delete word", command=self.delete_word).pack(side="left", padx=4)

        pane = ttk.PanedWindow(parent, orient="vertical")
        pane.pack(fill="both", expand=True)
        top = ttk.Frame(pane)
        self.words = ttk.Treeview(top, columns=("word", "heard", "takes"), show="headings",
                                  selectmode="browse", height=7)
        for col, title, width in (("word", "Word", 170), ("heard", "LocalFlow heard", 420), ("takes", "Takes", 60)):
            self.words.heading(col, text=title)
            self.words.column(col, width=px(width), stretch=(col == "heard"))
        self.words.pack(fill="both", expand=True)
        self.words.bind("<<TreeviewSelect>>", lambda _e: self._show_takes())
        pane.add(top, weight=2)

        bottom = ttk.Frame(pane)
        self.takes = ttk.Treeview(bottom, columns=("take", "heard", "rule"), show="headings",
                                  selectmode="browse", height=5)
        for col, title, width in (("take", "Take", 60), ("heard", "Heard as", 300), ("rule", "Correction", 310)):
            self.takes.heading(col, text=title)
            self.takes.column(col, width=px(width), stretch=(col != "take"))
        self.takes.pack(fill="both", expand=True)
        tbar = ttk.Frame(bottom)
        tbar.pack(fill="x", pady=(6, 0))
        ttk.Button(tbar, text="Play take", command=self.play_take).pack(side="left")
        ttk.Button(tbar, text="Turn correction on/off", command=self.toggle_variant).pack(side="left", padx=4)
        ttk.Button(tbar, text="Delete take", command=self.delete_take).pack(side="left")
        self.status = tk.StringVar()
        ttk.Label(tbar, textvariable=self.status, foreground=MUTED).pack(side="left", padx=10)
        pane.add(bottom, weight=1)
        self.refresh()

    # ------------------------------------------------------------------ views
    def refresh(self, select=None):
        d = self.app.dictionary
        self.words.delete(*self.words.get_children())
        for key, entry in sorted(d.entries.items()):
            parts = []
            for heard, enabled in d.variants(entry).items():
                parts.append("“%s”%s" % (heard, "" if enabled else " (off)"))
            summary = ", ".join(parts) or ("heard correctly already" if entry["takes"] else "no takes yet")
            self.words.insert("", "end", iid=key, values=(entry["word"], summary, len(entry["takes"])))
        if select and self.words.exists(select.lower()):
            self.words.selection_set(select.lower())
        self._show_takes()

    def _entry(self):
        sel = self.words.selection()
        return self.app.dictionary.entries.get(sel[0]) if sel else None

    def _show_takes(self):
        self.takes.delete(*self.takes.get_children())
        entry = self._entry()
        if not entry:
            return
        for take in entry["takes"]:
            h = variant_for(take["heard"], entry["word"])
            if not normalize(take["heard"]):
                rule = "(nothing heard)"
            elif not h:
                rule = "already right (or nothing close to correct)"
            elif h in entry["disabled"]:
                rule = "off: common words, spelling hint only"
            else:
                rule = "→ %s" % entry["word"]
            self.takes.insert("", "end", iid=str(take["n"]), values=(take["n"], take["heard"], rule))

    def _take(self):
        entry, sel = self._entry(), self.takes.selection()
        if not entry or not sel:
            return entry, None
        return entry, next((t for t in entry["takes"] if str(t["n"]) == sel[0]), None)

    # ------------------------------------------------------------------ actions
    def add_word(self):
        RecordDialog(self)

    def record_more(self):
        entry = self._entry()
        if not entry:
            messagebox.showinfo("LocalFlow", "Select a word first, or use Add word.")
            return
        RecordDialog(self, entry["word"])

    def delete_word(self):
        entry = self._entry()
        if entry and messagebox.askyesno("LocalFlow", "Delete “%s” and its recordings?" % entry["word"]):
            self.app.dictionary.delete(entry["word"])
            self.refresh()

    def delete_take(self):
        entry, take = self._take()
        if take:
            self.app.dictionary.delete_take(entry["word"], take["n"])
            self.refresh(entry["word"])

    def toggle_variant(self):
        entry, take = self._take()
        if not take:
            return
        h = variant_for(take["heard"], entry["word"])
        if not h:
            return
        self.app.dictionary.set_variant(entry["word"], h, enabled=h in entry["disabled"])
        self.refresh(entry["word"])

    def play_take(self):
        entry, take = self._take()
        if take:
            path = self.app.dictionary.take_path(entry["word"], take["n"])
            try:
                import sounddevice as sd

                sd.play(audio.load_wav(path), audio.SAMPLE_RATE)
            except Exception as exc:
                self.status.set("Couldn't play: %s" % exc)

    def recheck_all(self):
        self.status.set("Re-checking with the current engine…")

        def run():
            for entry in list(self.app.dictionary.entries.values()):
                self.app.dictionary.recheck(entry["word"], self.app.hear_plain)
            self.app.ui_q.put(("dictionary_changed", None, "Re-checked every take with the current engine."))

        threading.Thread(target=run, daemon=True).start()


class RecordDialog:
    def __init__(self, tab, word=""):
        self.tab, self.app = tab, tab.app
        win = self.win = tk.Toplevel(self.app.root)
        win.title("Teach a word")
        win.transient(self.app.root)
        win.resizable(False, False)
        body = ttk.Frame(win, padding=16)
        body.pack(fill="both", expand=True)
        ttk.Label(body, text="Word or phrase, spelled the way you want it typed").pack(anchor="w")
        self.word = tk.StringVar(value=word)
        entry = ttk.Entry(body, textvariable=self.word, width=40)
        entry.pack(anchor="w", pady=(2, 10))
        ttk.Label(body, foreground=MUTED, wraplength=px(440), justify="left",
                  text="Press Record and say it once, the way you normally would. Three or four takes "
                       "(maybe one inside a short sentence) teach it best.").pack(anchor="w")
        self.record_btn = ttk.Button(body, text="Record (%d s)" % TAKE_SECONDS, command=self.record)
        self.record_btn.pack(anchor="w", pady=(10, 6))
        self.log = tk.Text(body, height=7, width=58, wrap="word", relief="flat", font=("TkDefaultFont", 9),
                           background=win.cget("background"))
        self.log.pack(fill="x")
        self.log.tag_configure("ok", foreground="#2e8b57")
        self.log.tag_configure("bad", foreground="#c0392b")
        ttk.Button(body, text="Done", command=self.close).pack(anchor="e", pady=(10, 0))
        win.protocol("WM_DELETE_WINDOW", self.close)
        entry.focus_set()

    def say(self, text, tag=None):
        self.log.insert("end", text + "\n", tag)
        self.log.see("end")

    def record(self):
        word = self.word.get().strip()
        if not word:
            messagebox.showinfo("LocalFlow", "Type the word first.", parent=self.win)
            return
        if self.app.recorder.active:
            messagebox.showinfo("LocalFlow", "Finish the current dictation first.", parent=self.win)
            return
        self.record_btn.configure(state="disabled")
        threading.Thread(target=self._capture, args=(word,), daemon=True).start()
        self._countdown(time.monotonic() + TAKE_SECONDS)

    def _countdown(self, end):
        left = end - time.monotonic()
        if left > 0 and self.win.winfo_exists():
            self.record_btn.configure(text="Listening… %.0f" % max(1, left + 0.5))
            self.win.after(200, self._countdown, end)

    def _capture(self, word):
        try:
            self.app.ui_q.put(("overlay", "rec", ""))
            self.app.recorder.start()
            time.sleep(TAKE_SECONDS)
            samples = self.app.recorder.stop(keep_tail=False)
            self.app.ui_q.put(("overlay", "busy", ""))
            if audio.stats(samples)["peak"] < 0.01:
                self.app.ui_q.put(("call", lambda: self.say("Didn't hear anything; check the microphone.", "bad")))
                return
            heard, fixed = self.app.teach_word(word, samples)
            if fixed:
                msg, tag = "Heard “%s” → now types “%s” ✓" % (heard, word), "ok"
            else:
                msg, tag = "Heard “%s” → still not right ✗ (try another take)" % heard, "bad"
            self.app.ui_q.put(("call", lambda: self.say(msg, tag)))
            self.app.ui_q.put(("dictionary_changed", word, ""))
        except Exception as exc:
            error = "Failed: %s" % exc  # bind now: `exc` is cleared when the except block ends
            self.app.ui_q.put(("call", lambda: self.say(error, "bad")))
        finally:
            self.app.ui_q.put(("idle",))
            self.app.ui_q.put(("call", self._ready))

    def _ready(self):
        if self.win.winfo_exists():
            self.record_btn.configure(state="normal", text="Record another take (%d s)" % TAKE_SECONDS)

    def close(self):
        self.win.destroy()
