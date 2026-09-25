"""AI clean-up setup assistant. Opens on first launch, when a clean-up option is
chosen but not yet verified, and after clean-up fails. It checks every install,
says in plain words what is wrong, and offers the fix (install link, a terminal
that runs the sign-in, or an API key box)."""
import queue
import threading
import tkinter as tk
import webbrowser
from tkinter import ttk

from . import cloud, keystore, polish

MUTED = "#6b6f78"
OK, BAD = "#2e8b57", "#c0392b"
SAMPLE = "um so I think we should uh meet on tuesday at 3 pm, actually no wednesday"

CHOICES = [
    ("off", "Off", "Paste the plain transcript. No account needed."),
    ("claude", "Claude", "Uses your Claude Pro/Max subscription through Claude Code on this computer. About 2-4 s."),
    ("codex", "ChatGPT", "Uses your ChatGPT subscription through the Codex CLI on this computer. About 3-5 s."),
    ("groq", "Groq API key", "Fastest (about 0.4 s). Free tier; paste a key from console.groq.com."),
    ("openai", "OpenAI API key", "Paid API key from platform.openai.com."),
]
STATUS_TEXT = {
    "ok": "works",
    "signed_out": "installed but not signed in",
    "timeout": "installed but not responding (an old or broken install; update it or use another)",
    "error": "failed",
}


class SetupDialog:
    def __init__(self, app, preselect=None, reason=""):
        self.app = app
        self.q = queue.Queue()
        self.result = None  # (provider, path_label) once a check passes
        cfg = app.cfg
        current = preselect or cfg["polish"]
        if current == "api":
            current = cfg.get("polish_api_provider", "groq")
        win = self.win = tk.Toplevel(app.root)
        win.title("Set up AI clean-up")
        win.transient(app.root)
        win.resizable(False, False)
        body = ttk.Frame(win, padding=16)
        body.pack(fill="both", expand=True)
        if reason:
            ttk.Label(body, text=reason, foreground=BAD, wraplength=520, justify="left").pack(anchor="w",
                                                                                         pady=(0, 8))
        ttk.Label(body, text="AI clean-up (optional)", font=("TkDefaultFont", 12, "bold")).pack(anchor="w")
        ttk.Label(body, wraplength=520, justify="left", foreground=MUTED,
                  text="Transcription already runs on this computer with no account. Clean-up is an "
                       "extra pass that fixes punctuation, removes false starts and applies "
                       "corrections (\"Tuesday, actually Wednesday\" becomes \"Wednesday\"). It uses "
                       "an account you already have; nothing is stored in LocalFlow except API keys, "
                       "which go in the system keychain.").pack(anchor="w", pady=(2, 10))

        self.choice = tk.StringVar(value=current if current in [c[0] for c in CHOICES] else "off")
        self.key_var = tk.StringVar()
        for key, title, note in CHOICES:
            row = ttk.Frame(body)
            row.pack(fill="x", pady=2)
            ttk.Radiobutton(row, text=title, value=key, variable=self.choice,
                            command=self._choice_changed).pack(anchor="w")
            ttk.Label(row, text=note, foreground=MUTED, wraplength=500).pack(anchor="w", padx=(22, 0))

        self.key_row = ttk.Frame(body)
        ttk.Label(self.key_row, text="API key").pack(side="left")
        ttk.Entry(self.key_row, textvariable=self.key_var, show="•", width=44).pack(side="left", padx=6)
        self.key_link = ttk.Button(self.key_row, text="Get a key")
        self.key_link.pack(side="left")

        self.check_btn = ttk.Button(body, text="Check", command=self.check)
        self.check_btn.pack(anchor="w", pady=(10, 4))
        self.status = tk.Text(body, height=7, width=70, wrap="word", relief="flat", padx=6, pady=4,
                              font=("TkDefaultFont", 9), background=win.cget("background"))
        self.status.tag_configure("ok", foreground=OK)
        self.status.tag_configure("bad", foreground=BAD)
        self.status.tag_configure("muted", foreground=MUTED)
        self.status.pack(fill="x")
        self.actions = ttk.Frame(body)
        self.actions.pack(fill="x", pady=(4, 0))

        footer = ttk.Frame(body)
        footer.pack(fill="x", pady=(12, 0))
        self.use_btn = ttk.Button(footer, text="Use this", command=self.use)
        self.use_btn.pack(side="right")
        ttk.Button(footer, text="Not now", command=self.close).pack(side="right", padx=6)
        win.protocol("WM_DELETE_WINDOW", self.close)
        self._choice_changed()
        win.after(100, self._poll)
        if reason and self.choice.get() in ("claude", "codex"):
            win.after(300, self.check)  # something went wrong: diagnose straight away
        win.lift()
        win.focus_force()

    # ------------------------------------------------------------------ helpers
    def _say(self, text, tag=None):
        self.status.configure(state="normal")
        self.status.insert("end", text + "\n", tag)
        self.status.see("end")
        self.status.configure(state="disabled")

    def _clear(self):
        self.status.configure(state="normal")
        self.status.delete("1.0", "end")
        self.status.configure(state="disabled")
        for child in self.actions.winfo_children():
            child.destroy()

    def _choice_changed(self):
        choice = self.choice.get()
        self.result = None
        self._clear()
        if choice in ("groq", "openai"):
            key_name = cloud.STT_PRESETS[choice]["key_name"]
            self.key_var.set(keystore.get(key_name))
            self.key_link.configure(command=lambda: webbrowser.open(cloud.STT_PRESETS[choice]["key_url"]))
            self.key_row.pack(fill="x", pady=(6, 0), before=self.check_btn)
        else:
            self.key_row.pack_forget()
        self.check_btn.configure(state="disabled" if choice == "off" else "normal")
        self.use_btn.configure(state="normal" if choice == "off" else "disabled")
        if choice != "off":
            self._say("Press Check to test it on this computer.", "muted")

    def _cfg_for(self, choice):
        cfg = dict(self.app.cfg)
        if choice in ("groq", "openai"):
            cfg.update(polish="api", polish_api_provider=choice, polish_api_model="")
        else:
            cfg.update(polish=choice, claude_path="", codex_path="")
        return cfg

    # ------------------------------------------------------------------ checking
    def check(self):
        choice = self.choice.get()
        self._clear()
        self.check_btn.configure(state="disabled")
        self.use_btn.configure(state="disabled")
        cfg = self._cfg_for(choice)
        key = self.key_var.get().strip()
        threading.Thread(target=self._run_check, args=(choice, cfg, key), daemon=True).start()

    def _run_check(self, choice, cfg, key):
        try:
            if choice in ("groq", "openai"):
                if not key:
                    self.q.put(("line", "Paste an API key first.", "bad"))
                    return
                self.q.put(("line", "Testing %s…" % choice, "muted"))
                out = polish.polish(SAMPLE, cfg, lambda _n: key)
                self.q.put(("line", "Works. Sample result: %s" % out, "ok"))
                self.q.put(("passed", choice, ""))
                return
            report = polish.diagnose(choice, cfg, on_progress=lambda m: self.q.put(("line", m, "muted")))
            self.q.put(("report", choice, report))
        except Exception as exc:
            self.q.put(("line", "Failed: %s" % str(exc)[:300], "bad"))
        finally:
            self.q.put(("done",))

    def _poll(self):
        try:
            while True:
                self._handle(self.q.get_nowait())
        except queue.Empty:
            pass
        if self.win.winfo_exists():
            self.win.after(100, self._poll)

    def _handle(self, ev):
        kind = ev[0]
        if kind == "line":
            self._say(ev[1], ev[2])
        elif kind == "passed":
            self.result = (ev[1], ev[2])
            self.use_btn.configure(state="normal")
        elif kind == "done":
            self.check_btn.configure(state="normal")
        elif kind == "report":
            self._show_report(ev[1], ev[2])

    def _show_report(self, name, report):
        app_name = {"claude": "Claude Code", "codex": "Codex CLI"}[name]
        if not report:
            self._say("%s isn't installed on this computer." % app_name, "bad")
            self._say("Install it, sign in once, then press Check again.", "muted")
            ttk.Button(self.actions, text="How to install %s" % app_name,
                       command=lambda: webbrowser.open(polish.INSTALL_URLS[name])).pack(side="left")
            return
        for item in report:
            tag = "ok" if item["status"] == "ok" else "bad"
            self._say("%s %s: %s" % ("✓" if tag == "ok" else "✗", item["label"],
                                      STATUS_TEXT[item["status"]]), tag)
            if item["status"] == "error":
                self._say("   " + item["detail"], "muted")
        good = next((i for i in report if i["status"] == "ok"), None)
        if good:
            self._say("Ready. Press Use this to turn clean-up on.", "ok")
            self.result = (name, good["label"])
            self.use_btn.configure(state="normal")
            return
        signed_out = next((i for i in report if i["status"] == "signed_out"), None)
        if signed_out:
            self._say("Sign in: a terminal will open; follow its prompts, close it, then press Check.",
                      "muted")
            ttk.Button(self.actions, text="Sign in to %s" % app_name,
                       command=lambda: polish.open_login(name, signed_out["prefix"])).pack(side="left")
        ttk.Button(self.actions, text="How to install / update",
                   command=lambda: webbrowser.open(polish.INSTALL_URLS[name])).pack(side="left", padx=6)

    # ------------------------------------------------------------------ finish
    def use(self):
        choice = self.choice.get()
        new = dict(self.app.cfg)
        new["setup_seen"] = True
        if choice == "off":
            new.update(polish="off", polish_verified="")
        elif self.result:
            if choice in ("groq", "openai"):
                keystore.set(cloud.STT_PRESETS[choice]["key_name"], self.key_var.get())
                new.update(polish="api", polish_api_provider=choice, polish_verified="api")
            else:
                new.update(polish=choice, polish_verified=choice)
                new[choice + "_path"] = self.result[1]
        self.app.apply_settings(new)
        self.app.settings.load(self.app.cfg)
        self.app.polish_failures = 0
        self.win.destroy()

    def close(self):
        if not self.app.cfg.get("setup_seen"):
            new = dict(self.app.cfg)
            new["setup_seen"] = True
            self.app.apply_settings(new)
        self.win.destroy()
