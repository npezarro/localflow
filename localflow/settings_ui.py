"""Settings tab: scrollable form, always-visible Save/Revert footer, unsaved-change
tracking (the app asks before you leave the tab or close the window)."""
import os
import subprocess
import sys
import tkinter as tk
import webbrowser
from tkinter import messagebox, ttk

from . import cloud, config, hotkey, keystore, paths

IS_MAC = sys.platform == "darwin"
MUTED = "#6b6f78"

ENGINES = [("local", "On this computer (private, offline)"),
           ("cloud", "Online API (most accurate; audio is sent to the provider)")]
POLISH = [("off", "Off"),
          ("claude", "Claude: your Claude subscription, via Claude Code"),
          ("codex", "ChatGPT: your ChatGPT subscription, via Codex CLI"),
          ("api", "API key: Groq / OpenAI / custom server")]
KEY_FIELDS = [("groq_api_key", "Groq API key", cloud.STT_PRESETS["groq"]["key_url"]),
              ("openai_api_key", "OpenAI API key", cloud.STT_PRESETS["openai"]["key_url"]),
              ("custom_api_key", "Custom server key", "")]
BOOLS = [("auto_paste", "Paste into the focused app"),
         ("restore_clipboard", "Restore the previous clipboard after pasting"),
         ("remove_fillers", "Remove filler words (um, uh)"),
         ("trailing_space", "Add a trailing space"),
         ("sounds", "Start/stop sounds"),
         ("save_last_recording", "Keep the last recording (data/last-recording.wav) for troubleshooting")]


FLOATS = {"release_tail": "Keep listening after release", "preroll": "Keep before press"}


TYPE_INTO = [("all", "All apps"), ("only", "Only the apps listed below"),
             ("except", "All apps except those listed below")]
INTS = {"live_auto_pause_min": ("Auto-pause after silence", 0, 120)}


def _label_for(pairs, key):
    return next((label for k, label in pairs if k == key), pairs[0][1])


def _key_for(pairs, label):
    return next((k for k, lbl in pairs if lbl == label), pairs[0][0])


class SettingsPanel:
    def __init__(self, parent, app):
        self.app = app
        self.dirty = False
        self._loading = False
        self.vars = {}
        self.key_vars = {}

        outer = ttk.Frame(parent)
        outer.pack(fill="both", expand=True)
        canvas = tk.Canvas(outer, highlightthickness=0, borderwidth=0)
        sb = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        self.body = ttk.Frame(canvas, padding=(4, 4, 12, 4))
        self.body.bind("<Configure>", lambda _e: canvas.configure(scrollregion=canvas.bbox("all")))
        win = canvas.create_window((0, 0), window=self.body, anchor="nw")
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure(win, width=e.width))
        canvas.configure(yscrollcommand=sb.set)
        canvas.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.canvas = canvas
        for seq in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            canvas.bind_all(seq, self._wheel, add="+")

        footer = ttk.Frame(parent, padding=(0, 8, 0, 0))
        footer.pack(fill="x", side="bottom")
        self.save_btn = ttk.Button(footer, text="Save", command=self.save)
        self.save_btn.pack(side="left")
        ttk.Button(footer, text="Revert", command=self.revert).pack(side="left", padx=6)
        self.dirty_var = tk.StringVar(value="All changes saved")
        self.dirty_label = ttk.Label(footer, textvariable=self.dirty_var, foreground=MUTED)
        self.dirty_label.pack(side="left", padx=8)
        ttk.Button(footer, text="Quit LocalFlow", command=app.quit).pack(side="right")
        ttk.Button(footer, text="Open data folder", command=self.open_data).pack(side="right", padx=6)

        self._build()
        self.load(app.cfg)

    # ------------------------------------------------------------------ layout helpers
    def _wheel(self, event):
        widget = event.widget
        while widget is not None:
            if widget is self.canvas:
                break
            widget = getattr(widget, "master", None)
        if widget is None or isinstance(event.widget, tk.Text):
            return
        delta = -1 if (getattr(event, "num", 0) == 4 or event.delta > 0) else 1
        if IS_MAC:
            delta = -event.delta if event.delta else delta
        self.canvas.yview_scroll(int(delta), "units")

    def _section(self, title):
        frame = ttk.LabelFrame(self.body, text=title, padding=(10, 6))
        frame.pack(fill="x", pady=(0, 10))
        frame.columnconfigure(1, weight=1)
        frame._row = 0
        return frame

    def _row(self, frame, label, widget, note=None):
        ttk.Label(frame, text=label).grid(row=frame._row, column=0, sticky="nw", pady=3, padx=(0, 10))
        widget.grid(row=frame._row, column=1, sticky="ew", pady=3)
        if note:
            frame._row += 1
            ttk.Label(frame, text=note, foreground=MUTED, wraplength=520, justify="left").grid(
                row=frame._row, column=1, sticky="w")
        frame._row += 1

    def _var(self, key, kind=tk.StringVar):
        var = kind()
        var.trace_add("write", lambda *_: self._changed())
        self.vars[key] = var
        return var

    def _combo(self, frame, key, values, width=18, readonly=True):
        return ttk.Combobox(frame, textvariable=self._var(key), values=values, width=width,
                            state="readonly" if readonly else "normal")

    def _text(self, frame, height=3):
        widget = tk.Text(frame, height=height, width=40, wrap="word", undo=True)
        widget.bind("<<Modified>>", self._text_modified)
        return widget

    def _text_modified(self, event):
        if event.widget.edit_modified():
            event.widget.edit_modified(False)
            self._changed()

    # ------------------------------------------------------------------ form
    def _build(self):
        s = self._section("Dictation")
        f = ttk.Frame(s)
        ttk.Entry(f, textvariable=self._var("hotkey"), width=22).pack(side="left", fill="x", expand=True)
        ttk.Button(f, text="Record…", command=lambda: self.app.capture_hotkey(self.vars["hotkey"])).pack(
            side="left", padx=4)
        self._row(s, "Hotkey", f)
        self._row(s, "Mode", self._combo(s, "mode", ["hold", "toggle"], 10),
                  "hold: talk while holding, release to paste (tap Space while holding for "
                  "hands-free). toggle: press once to start, again to finish.")
        f = ttk.Frame(s)
        ttk.Entry(f, textvariable=self._var("paste_last_hotkey"), width=22).pack(side="left", fill="x",
                                                                                expand=True)
        ttk.Button(f, text="Record…",
                   command=lambda: self.app.capture_hotkey(self.vars["paste_last_hotkey"])).pack(
            side="left", padx=4)
        self._row(s, "Paste last transcript", f)
        ttk.Checkbutton(s, text="Live typing: type words into the focused app while you speak",
                        variable=self._var("live_typing", tk.BooleanVar)).grid(row=s._row, column=1, sticky="w")
        s._row += 1
        ttk.Label(s, foreground=MUTED, wraplength=520, justify="left",
                  text="Words appear about 1-2 s behind your voice and are typed as keystrokes (the "
                       "clipboard is left alone). Uses the local model; AI clean-up doesn't apply, "
                       "because typed text isn't rewritten afterwards.").grid(row=s._row, column=1, sticky="w")
        s._row += 1
        f = ttk.Frame(s)
        ttk.Entry(f, textvariable=self._var("live_hotkey"), width=22).pack(side="left", fill="x", expand=True)
        ttk.Button(f, text="Record…", command=lambda: self.app.capture_hotkey(self.vars["live_hotkey"])).pack(
            side="left", padx=4)
        self._row(s, "Toggle live typing", f)
        f = ttk.Frame(s)
        ttk.Entry(f, textvariable=self._var("live_pause_hotkey"), width=22).pack(side="left", fill="x", expand=True)
        ttk.Button(f, text="Record…",
                   command=lambda: self.app.capture_hotkey(self.vars["live_pause_hotkey"])).pack(side="left", padx=4)
        self._row(s, "Always-on live listening", f,
                  "Press to start listening; words keep flowing into whatever field is focused "
                  "(even as you switch apps) until you press it again to pause. No need to hold anything.")
        self._row(s, "Auto-pause after silence",
                  ttk.Spinbox(s, from_=0, to=120, increment=1, width=6, textvariable=self._var("live_auto_pause_min")),
                  "Minutes of silence before always-on listening pauses itself (0 = never).")
        self._row(s, "Keep listening after release",
                  ttk.Spinbox(s, from_=0.0, to=3.0, increment=0.1, width=6, format="%.1f",
                              textvariable=self._var("release_tail")),
                  "Seconds the mic stays open after you let go of the hotkey (the pill keeps "
                  "listening). Raise it if your last word gets cut off; 0 stops instantly.")

        s = self._section("Microphone")
        self.device_names = [n for _i, n in self.app.devices]
        self._row(s, "Input", self._combo(s, "input_device", self.device_names, 40))
        ttk.Checkbutton(s, text="Keep the microphone ready (stops the first word getting cut off)",
                        variable=self._var("warm_mic", tk.BooleanVar)).grid(row=s._row, column=1, sticky="w")
        s._row += 1
        self._row(s, "Keep before press",
                  ttk.Spinbox(s, from_=0.0, to=2.0, increment=0.1, width=6, format="%.1f",
                              textvariable=self._var("preroll")),
                  "Seconds of audio kept from just before you press the hotkey (needs the "
                  "option above).")
        f = ttk.Frame(s)
        ttk.Button(f, text="Test microphone (speak for 4 s)", command=self.app.test_microphone).pack(side="left")
        self._row(s, "", f, "Records 4 seconds, then shows the level and what was heard "
                            "(uses your saved settings).")

        s = self._section("Transcription")
        self._row(s, "Engine", self._combo(s, "engine", [lbl for _k, lbl in ENGINES], 52))
        self._row(s, "Local model", self._combo(s, "model", config.MODEL_CHOICES, 20, readonly=False),
                  "base.en is bundled. small.en is more accurate (about 2x slower); large-v3-turbo "
                  "is best but needs a fast CPU or Apple Silicon. Others download once.")
        self._row(s, "Language", self._combo(s, "language", ["en", "auto", "es", "fr", "de", "it", "pt",
                                                              "nl", "ja", "zh", "ko", "ru", "hi"], 8,
                                              readonly=False),
                  ".en models are English-only; pick a model without .en for other languages.")
        self._row(s, "Online provider", self._combo(s, "cloud_provider", list(cloud.STT_PRESETS), 12),
                  "; ".join("%s = %s" % (k, v["label"]) for k, v in cloud.STT_PRESETS.items()))
        self._row(s, "Online model", ttk.Entry(s, textvariable=self._var("cloud_model")),
                  "Blank = provider default.")
        self._row(s, "Custom server URL", ttk.Entry(s, textvariable=self._var("cloud_base_url")),
                  "Only for the custom provider, e.g. http://localhost:8000/v1")
        ttk.Checkbutton(s, text="If the online service fails, use the local model",
                        variable=self._var("cloud_fallback_local", tk.BooleanVar)).grid(
            row=s._row, column=1, sticky="w")
        s._row += 1

        s = self._section("AI clean-up (optional)")
        self._row(s, "Clean up with", self._combo(s, "polish", [lbl for _k, lbl in POLISH], 52),
                  "Fixes punctuation, removes false starts and applies self-corrections "
                  "(\"Tuesday, actually Wednesday\"). Adds about 2-3 s. Claude and ChatGPT use the "
                  "Claude Code / Codex apps already signed in on this computer.")
        self._row(s, "Claude model", ttk.Entry(s, textvariable=self._var("claude_model")),
                  "sonnet (fast) or opus.")
        self._row(s, "Codex model", ttk.Entry(s, textvariable=self._var("codex_model")),
                  "Blank = your Codex default.")
        self._row(s, "Claude command", ttk.Entry(s, textvariable=self._var("claude_path")),
                  "Blank = find it automatically. Test clean-up fills in the install that works "
                  "(on Windows this can be Claude Code inside WSL).")
        self._row(s, "Codex command", ttk.Entry(s, textvariable=self._var("codex_path")))
        self._row(s, "API provider", self._combo(s, "polish_api_provider", list(cloud.CHAT_PRESETS), 12))
        self._row(s, "API model", ttk.Entry(s, textvariable=self._var("polish_api_model")),
                  "Blank = %s." % ", ".join("%s: %s" % (k, v["model"]) for k, v in cloud.CHAT_PRESETS.items()))
        self._row(s, "Custom API URL", ttk.Entry(s, textvariable=self._var("polish_api_base_url")),
                  "e.g. a local Ollama server: http://localhost:11434/v1")
        self.prompt_text = self._text(s, 3)
        self._row(s, "Custom instructions", self.prompt_text, "Blank = built-in instructions.")
        f = ttk.Frame(s)
        ttk.Button(f, text="Set up / check…",
                   command=lambda: self.app.open_setup(preselect=_key_for(POLISH, self.vars["polish"].get()))
                   ).pack(side="left")
        ttk.Button(f, text="Test clean-up", command=self.app.test_polish).pack(side="left", padx=6)
        self._row(s, "", f, "Set up walks you through installing / signing in and checks it works.")

        s = self._section("API keys")
        store = keystore.backend_name()
        for key, label, url in KEY_FIELDS:
            f = ttk.Frame(s)
            var = tk.StringVar()
            var.trace_add("write", lambda *_: self._changed())
            self.key_vars[key] = var
            ttk.Entry(f, textvariable=var, show="•", width=40).pack(side="left", fill="x", expand=True)
            if url:
                ttk.Button(f, text="Get a key", command=lambda u=url: webbrowser.open(u)).pack(side="left", padx=4)
            self._row(s, label, f)
        ttk.Label(s, text=("Stored in the system keychain." if store else
                           "No system keychain found: keys are saved in data/secrets.json."),
                  foreground=MUTED).grid(row=s._row, column=1, sticky="w")
        s._row += 1

        s = self._section("Apps")
        self._row(s, "Type into", self._combo(s, "type_into", [lbl for _k, lbl in TYPE_INTO], 34),
                  "Applies to pasting and live typing. When the focused app isn't allowed, the text is "
                  "copied to the clipboard and saved in Transcripts instead.")
        self.apps_text = self._text(s, 4)
        self._row(s, "Apps", self.apps_text,
                  "One per line: the program name (chrome, slack, code, notepad), or on a Mac the app "
                  "name (Google Chrome, Slack) or bundle id.")
        f = ttk.Frame(s)
        self.seen_var = tk.StringVar()
        self.seen_combo = ttk.Combobox(f, textvariable=self.seen_var, width=30, state="readonly",
                                       postcommand=self._refresh_seen)
        self.seen_combo.pack(side="left")
        ttk.Button(f, text="Add", command=self._add_seen).pack(side="left", padx=4)
        self._row(s, "Apps you've dictated into", f)

        s = self._section("Output")
        for key, label in BOOLS:
            ttk.Checkbutton(s, text=label, variable=self._var(key, tk.BooleanVar)).grid(
                row=s._row, column=0, columnspan=2, sticky="w")
            s._row += 1

        s = self._section("Words")
        self.vocab_text = self._text(s, 3)
        self._row(s, "Vocabulary", self.vocab_text, "One per line: names and jargon to spell your way.")
        self.repl_text = self._text(s, 3)
        self._row(s, "Replacements", self.repl_text,
                  "One per line, spoken => written. e.g. new line => \\n")
        ttk.Checkbutton(s, text="Learn from my dictations and corrections (stays on this computer)",
                        variable=self._var("learn", tk.BooleanVar)).grid(row=s._row, column=1, sticky="w")
        s._row += 1
        f = ttk.Frame(s)
        self.learned_list = tk.Listbox(f, height=5, activestyle="none")
        self.learned_list.pack(side="left", fill="both", expand=True)
        lsb = ttk.Scrollbar(f, orient="vertical", command=self.learned_list.yview)
        lsb.pack(side="left", fill="y")
        self.learned_list.configure(yscrollcommand=lsb.set)
        self._row(s, "Learned", f,
                  "Names and jargon you repeat (after 3 dictations) and fixes from Transcripts → "
                  "Save correction. They're used as spelling hints and replacements.")
        f = ttk.Frame(s)
        ttk.Button(f, text="Forget selected", command=self.forget_learned).pack(side="left")
        ttk.Button(f, text="Forget all", command=self.forget_all_learned).pack(side="left", padx=6)
        self._row(s, "", f)
        self._learned_rows = []

    # ------------------------------------------------------------------ state
    def _changed(self):
        if self._loading:
            return
        self.dirty = True
        self.dirty_var.set("● Unsaved changes")
        self.dirty_label.configure(foreground="#c77700")

    def _refresh_seen(self):
        from . import apps

        self.seen_combo.configure(values=apps.seen_apps() or ["(dictate into an app first)"])

    def _add_seen(self):
        name = self.seen_var.get()
        if not name or name.startswith("("):
            return
        current = [a.strip().lower() for a in self.apps_text.get("1.0", "end").splitlines()]
        if name.lower() not in current:
            text = self.apps_text.get("1.0", "end").rstrip("\n")
            self.apps_text.delete("1.0", "end")
            self.apps_text.insert("1.0", (text + "\n" if text else "") + name)

    def refresh_learned(self):
        self._learned_rows = self.app.learner.entries()
        self.learned_list.delete(0, "end")
        for _kind, _key, label in self._learned_rows:
            self.learned_list.insert("end", label)
        if not self._learned_rows:
            self.learned_list.insert("end", "Nothing learned yet.")

    def forget_learned(self):
        sel = self.learned_list.curselection()
        if sel and sel[0] < len(self._learned_rows):
            kind, key, _label = self._learned_rows[sel[0]]
            self.app.learner.remove(kind, key)
            self.refresh_learned()

    def forget_all_learned(self):
        if messagebox.askyesno("LocalFlow", "Forget everything LocalFlow has learned?"):
            self.app.learner.clear()
            self.refresh_learned()

    def set_quietly(self, key, value):
        """Reflect a change made outside the form without marking it unsaved."""
        self._loading = True
        try:
            self.vars[key].set(value)
        finally:
            self._loading = False

    def _clean(self, message="All changes saved"):
        self.dirty = False
        self.dirty_var.set(message)
        self.dirty_label.configure(foreground=MUTED)

    def load(self, cfg):
        self._loading = True
        try:
            for key, var in self.vars.items():
                if key == "engine":
                    var.set(_label_for(ENGINES, cfg["engine"]))
                elif key == "polish":
                    var.set(_label_for(POLISH, cfg["polish"]))
                elif key == "type_into":
                    var.set(_label_for(TYPE_INTO, cfg["type_into"]))
                elif key == "input_device":
                    var.set(next((n for i, n in self.app.devices if i == cfg["input_device"]),
                                 self.device_names[0]))
                elif isinstance(var, tk.BooleanVar):
                    var.set(bool(cfg[key]))
                else:
                    var.set("" if cfg[key] is None else str(cfg[key]))
            for key, var in self.key_vars.items():
                var.set(keystore.get(key))
            for widget, value in ((self.vocab_text, "\n".join(cfg["vocabulary"])),
                                  (self.repl_text, "\n".join("%s => %s" % (k, v.replace("\n", "\\n"))
                                                             for k, v in cfg["replacements"].items())),
                                  (self.prompt_text, cfg["polish_prompt"]),
                                  (self.apps_text, "\n".join(cfg["app_list"]))):
                widget.delete("1.0", "end")
                widget.insert("1.0", value)
                widget.edit_modified(False)
        finally:
            self._loading = False
        self.refresh_learned()
        self._clean()

    def collect(self):
        """Form -> new config dict. Raises ValueError with a user-facing message."""
        new = dict(self.app.cfg)
        for key in ("hotkey", "paste_last_hotkey", "live_hotkey", "live_pause_hotkey"):
            value = self.vars[key].get().strip().lower()
            try:
                hotkey.parse_combo(value)
            except ValueError:
                raise ValueError("The %s can't be empty." % key.replace("_", " "))
            new[key] = value
        for key, var in self.vars.items():
            if key in ("hotkey", "paste_last_hotkey", "live_hotkey", "live_pause_hotkey"):
                continue
            if key == "engine":
                new[key] = _key_for(ENGINES, var.get())
            elif key == "polish":
                new[key] = _key_for(POLISH, var.get())
            elif key == "type_into":
                new[key] = _key_for(TYPE_INTO, var.get())
            elif key in INTS:
                label, lo, hi = INTS[key]
                try:
                    new[key] = max(lo, min(hi, int(float(var.get()))))
                except ValueError:
                    raise ValueError("%s must be a whole number." % label)
            elif key == "input_device":
                new[key] = next((i for i, n in self.app.devices if n == var.get()), None)
            elif isinstance(var, tk.BooleanVar):
                new[key] = bool(var.get())
            elif key in FLOATS:
                try:
                    value = float(var.get())
                except ValueError:
                    raise ValueError("%s must be a number of seconds." % FLOATS[key])
                new[key] = round(min(3.0, max(0.0, value)), 2)
            else:
                new[key] = var.get().strip()
        if not new["model"]:
            raise ValueError("Pick a local model.")
        new["vocabulary"] = [w.strip() for w in self.vocab_text.get("1.0", "end").splitlines() if w.strip()]
        repl = {}
        for line in self.repl_text.get("1.0", "end").splitlines():
            if "=>" in line:
                spoken, written = line.split("=>", 1)
                if spoken.strip():
                    repl[spoken.strip()] = written.strip().replace("\\n", "\n")
        new["replacements"] = repl
        new["polish_prompt"] = self.prompt_text.get("1.0", "end").strip()
        new["app_list"] = [a.strip() for a in self.apps_text.get("1.0", "end").splitlines() if a.strip()]
        if new["type_into"] == "only" and not new["app_list"]:
            raise ValueError("Add at least one app, or choose \"All apps\".")
        return new

    def save(self):
        try:
            new = self.collect()
        except ValueError as exc:
            messagebox.showerror("LocalFlow", str(exc))
            return False
        for key, var in self.key_vars.items():
            if var.get().strip() != keystore.get(key):
                keystore.set(key, var.get())
        self.app.apply_settings(new)
        for widget in (self.vocab_text, self.repl_text, self.prompt_text, self.apps_text):
            widget.edit_modified(False)  # a "modified" event still in the queue must not re-dirty
        self._clean("Saved")
        return True

    def revert(self):
        self.load(self.app.cfg)

    def confirm_leave(self):
        """True if it's fine to navigate away (saved, discarded, or nothing to do)."""
        if not self.dirty:
            return True
        answer = messagebox.askyesnocancel("LocalFlow", "Save your settings changes?")
        if answer is None:
            return False
        if answer:
            return self.save()
        self.revert()
        return True

    def open_data(self):
        path = paths.data_dir()
        if sys.platform == "win32":
            os.startfile(path)  # noqa: S606
        elif IS_MAC:
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])
