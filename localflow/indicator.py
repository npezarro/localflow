"""Wispr-style floating dictation pill: a rounded dark pill at the bottom centre of
the monitor you're working on, with a waveform that follows your voice while
listening and a travelling wave while transcribing. Never takes focus and ignores
the mouse, so it can't steal the paste target."""
import math
import sys
import time
import tkinter as tk

from . import monitors, platform_fix
from .ui import px

IS_MAC = sys.platform == "darwin"
IS_WIN = sys.platform == "win32"

KEY = "#010203"  # colour made transparent around the pill (Windows)
PILL, EDGE, BAR, DIM, RED, ACCENT, TEXT = ("#111216", "#34363f", "#f4f4f6", "#8b8e99", "#ff4d57",
                                            "#8fa2ff", "#e9e9ee")
N_BARS = 13
BASE_H = 36  # pill height in 96-dpi pixels; scaled per display at runtime
BASE_MARGIN = 18


class Indicator:
    def __init__(self, root):
        self.root = root
        self.mode = None
        self.level = 0.0
        self.levels = [0.0] * N_BARS
        self._alpha = 0.0
        self._hide_at = None
        self.H, self.M = px(BASE_H), px(BASE_MARGIN)
        self.width = px(132)
        self.live = False

        win = self.win = tk.Toplevel(root)
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        if IS_MAC:
            bg = "systemTransparent"
            win.attributes("-transparent", True)
        else:
            bg = KEY
            if IS_WIN:
                win.attributes("-transparentcolor", KEY)
        win.configure(bg=bg)
        self._set_alpha(0.0)
        self.canvas = tk.Canvas(win, width=px(260), height=self.H, bg=bg, highlightthickness=0, borderwidth=0)
        self.canvas.pack()
        win.geometry("%dx%d+-10000+-10000" % (px(260), self.H))
        win.update_idletasks()
        if IS_WIN:
            platform_fix.windows_no_activate(int(win.wm_frame(), 16))

    # ------------------------------------------------------------------ public API
    def show(self, mode, text=""):
        """mode: listening | locked | busy | polish | message"""
        first = self.mode is None
        self.mode = mode
        self.text = text
        self._hide_at = time.monotonic() + 3.0 if mode == "message" else None
        width = self._width_for(mode, text)
        if first or mode == "message" or width != self.width:
            self.width = width
            self._place()
        self._draw()
        self.win.lift()

    def hide(self):
        self.mode = None
        self._set_alpha(0.0)
        self.win.geometry("+-10000+-10000")

    def tick(self, level):
        """Call ~25x/s from the Tk thread."""
        if self.mode is None:
            return
        if self._hide_at and time.monotonic() > self._hide_at:
            self.hide()
            return
        if self._alpha < 0.96:
            self._set_alpha(min(0.96, self._alpha + 0.3))
        smoothed = max(level, self.levels[-1] * 0.8)
        self.levels = self.levels[1:] + [smoothed]
        self._draw()

    # ------------------------------------------------------------------ internals
    def _set_alpha(self, value):
        self._alpha = value
        try:
            self.win.attributes("-alpha", value)
        except tk.TclError:
            pass

    def _width_for(self, mode, text):
        return px(self._base_width(mode, text))

    def _base_width(self, mode, text):
        if mode == "continuous":
            return 176
        if mode == "correcting":
            return 196
        extra = 38 if self.live and mode in ("listening", "locked") else 0
        if mode == "locked":
            return 196 + extra
        if mode == "listening":
            return 132 + extra
        if mode == "message":
            return min(520, 40 + 7 * len(text))  # base pixels; _width_for scales
        return 132

    def _place(self):
        x, y, w, h = monitors.active_work_area(self.root)
        px_ = x + (w - self.width) // 2
        py = y + h - self.H - self.M
        self.win.geometry("%dx%d+%d+%d" % (self.width, self.H, px_, py))

    def _pill(self, c, width):
        H = self.H
        r = H // 2
        c.create_oval(0, 0, H - 1, H - 1, fill=EDGE, outline="")
        c.create_oval(width - H, 0, width - 1, H - 1, fill=EDGE, outline="")
        c.create_rectangle(r, 0, width - r, H - 1, fill=EDGE, outline="")
        c.create_oval(1, 1, H - 2, H - 2, fill=PILL, outline="")
        c.create_oval(width - H + 1, 1, width - 2, H - 2, fill=PILL, outline="")
        c.create_rectangle(r, 1, width - r, H - 2, fill=PILL, outline="")

    def _draw(self):
        c = self.canvas
        c.delete("all")
        width = self.width
        c.configure(width=width)
        self._pill(c, width)
        H = self.H
        mid = H / 2
        if self.mode == "message":
            c.create_text(width / 2, mid, text=self.text, fill=TEXT, font=("TkDefaultFont", 10))
            return
        left = px(20)
        if self.mode == "locked":
            c.create_oval(px(14), mid - px(4), px(22), mid + px(4), fill=RED, outline="")
            c.create_text(width - px(14), mid, text="hands-free", anchor="e", fill=DIM,
                          font=("TkDefaultFont", 8))
            left = px(30)
        if self.mode == "continuous":  # always-on live listening
            c.create_oval(px(14), mid - px(4), px(22), mid + px(4), fill=RED, outline="")
            c.create_text(width - px(14), mid, text="LIVE", anchor="e", fill=ACCENT,
                          font=("TkDefaultFont", 8, "bold"))
            left = px(30)
        if self.mode == "correcting":  # recording a spoken correction
            c.create_text(width - px(14), mid, text="correction", anchor="e", fill=ACCENT,
                          font=("TkDefaultFont", 8, "bold"))
        right = (px(84) if self.mode == "locked" else px(50) if self.mode == "continuous"
                 else px(80) if self.mode == "correcting" else px(20))
        if self.live and self.mode in ("listening", "locked"):
            right += px(38)
            c.create_text(width - (px(84) if self.mode == "locked" else px(16)), mid, text="LIVE", anchor="e",
                          fill=ACCENT, font=("TkDefaultFont", 8, "bold"))
        span = (width - left - right)
        step = span / N_BARS
        t = time.monotonic()
        for i in range(N_BARS):
            centre_weight = 1 - abs(i - (N_BARS - 1) / 2) / (N_BARS / 2)  # taller in the middle
            if self.mode in ("listening", "locked", "continuous", "correcting"):
                lv = self.levels[-1 - abs(i - N_BARS // 2)]  # recent level radiates from the centre
                wobble = 0.75 + 0.25 * math.sin(t * 9 + i * 1.7)
                bar_h = px(3) + (H - px(12)) * min(1.0, lv * 1.4) * (0.45 + 0.55 * centre_weight) * wobble
                colour = BAR
            else:  # busy / polish: travelling wave
                bar_h = px(3) + px(9) * (0.5 + 0.5 * math.sin(t * 7 - i * 0.6)) * (0.5 + 0.5 * centre_weight)
                colour = ACCENT if self.mode == "polish" else DIM
            x = left + i * step + step / 2
            c.create_line(x, mid - bar_h / 2, x, mid + bar_h / 2, fill=colour, width=px(3), capstyle="round")
