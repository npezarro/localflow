"""The "Quick settings" drop-down in the main window: everyday toggles and actions, so you
don't have to scroll through the Settings tab. Changes save immediately."""
import tkinter as tk

from . import config, hotkey
from .settings_ui import POLISH

BEAMS = [(1, "Fast"), (5, "Thorough")]


def build(app, menu):
    """(Re)fill ``menu`` from the current settings; called every time it opens."""
    menu.delete(0, "end")
    cfg = app.cfg
    menu._vars = []  # keep Tk variables alive while the menu is open

    def check(label, key, accel=""):
        var = tk.BooleanVar(value=bool(cfg[key]))
        menu._vars.append(var)
        menu.add_checkbutton(label=label, variable=var, accelerator=accel,
                             command=lambda: app.quick_set(key, var.get()))

    def radio(parent, key, choices):
        var = tk.StringVar(value=str(cfg[key]))
        menu._vars.append(var)
        for value, label in choices:
            parent.add_radiobutton(label=label, value=str(value), variable=var,
                                   command=lambda v=value: app.quick_set(key, v))

    def submenu(label):
        sub = tk.Menu(menu, tearoff=False)
        menu.add_cascade(label=label, menu=sub)
        return sub

    fmt = hotkey.format_combo
    # --- live modes (runtime state, not just settings)
    live = tk.BooleanVar(value=app.continuous)
    menu._vars.append(live)
    menu.add_checkbutton(label="Always-on live listening", variable=live, accelerator=fmt(cfg["live_pause_hotkey"]),
                         command=lambda: app.ctl_q.put(("continuous",)))
    check("Live typing", "live_typing", fmt(cfg["live_hotkey"]))
    menu.add_separator()

    # --- everyday settings
    radio(submenu("Hotkey mode"), "mode", [("hold", "Hold to talk"), ("toggle", "Press to start / stop")])
    models = submenu("Model")
    choices = list(config.MODEL_CHOICES)
    if cfg["model"] not in choices:
        choices.insert(0, cfg["model"])
    radio(models, "model", [(m, m) for m in choices])
    radio(submenu("Speed"), "beam_size", BEAMS)
    mics = submenu("Microphone")
    var = tk.StringVar(value=str(cfg["input_device"]))
    menu._vars.append(var)
    for index, name in app.devices:
        mics.add_radiobutton(label=name, value=str(index), variable=var,
                             command=lambda i=index: app.quick_set("input_device", i))
    ai = submenu("AI clean-up")
    radio(ai, "polish", [(k, label.split(":")[0]) for k, label in POLISH])
    ai.add_separator()
    ai.add_command(label="Set up / test…", command=app.open_setup)
    menu.add_separator()
    check("Paste into the focused app", "auto_paste")
    check("Start/stop sounds", "sounds")
    check("Remove filler words (um, uh)", "remove_fillers")
    check("Learn from my corrections", "learn")
    check("Fix voice corrections in place", "feedback_fix_in_place")
    menu.add_separator()

    # --- actions
    menu.add_command(label="Paste last transcript", accelerator=fmt(cfg["paste_last_hotkey"]),
                     command=lambda: app.ctl_q.put(("paste_last",)))
    menu.add_command(label="Test microphone", command=app.test_microphone)
    menu.add_command(label="Check for updates…", command=lambda: app.check_for_updates(True, app.set_status))
    menu.add_command(label="Back up my data…", command=app.settings.backup)
    menu.add_command(label="Open data folder", command=app.settings.open_data)
    menu.add_separator()
    menu.add_command(label="All settings…", command=lambda: app.nb.select(app.settings_tab))
