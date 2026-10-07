import os
import sys
import threading
import tkinter as tk
import webbrowser
from tkinter import ttk

import vrcft_to_vts as bridge

BG = "#16161c"
CARD = "#22222b"
TEXT = "#f4f1ea"
MUTED = "#a8a3b5"
GREEN = "#7dce7a"
AMBER = "#e2b656"
RED = "#e07a7a"


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("VR to VTube Studio")
        self.configure(bg=BG)
        self._set_icon()
        self.geometry("530x700")
        self.minsize(530, 520)
        self._locking = False
        self.worker = None
        self.vars = {}
        self.tune_open = False

        outer = tk.Frame(self, bg=BG)
        outer.pack(fill="both", expand=True)
        self.canvas = tk.Canvas(outer, bg=BG, highlightthickness=0, borderwidth=0)
        scroll = ttk.Scrollbar(outer, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)

        body = tk.Frame(self.canvas, bg=BG)
        self._window = self.canvas.create_window((0, 0), window=body, anchor="nw")
        body.bind("<Configure>", lambda _e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfigure(self._window, width=e.width))
        self.canvas.bind_all("<MouseWheel>", self._wheel)

        tk.Label(body, text="VR to VTube Studio", bg=BG, fg=TEXT, font=("Segoe UI", 22, "bold")).pack(anchor="w", padx=24, pady=(22, 2))
        tk.Label(
            body,
            text="Your headset face, on your VTube model.",
            bg=BG, fg=MUTED, font=("Segoe UI", 11), justify="left",
        ).pack(anchor="w", padx=24, pady=(0, 16))

        card = tk.Frame(body, bg=CARD)
        card.pack(fill="x", padx=24, pady=(0, 14))
        self.dots = {}
        self.labels = {}
        initial = {
            "headset": "Waiting for SteamVR",
            "tracking": "Waiting for VRCFaceTracking",
            "vts": "Waiting for VTube Studio",
        }
        for key, title in (("headset", "Neck"), ("tracking", "Face tracking"), ("vts", "VTube Studio")):
            row = tk.Frame(card, bg=CARD)
            row.pack(fill="x", padx=16, pady=8)
            dot = tk.Label(row, text="\u25cf", bg=CARD, fg=MUTED, font=("Segoe UI", 16))
            dot.pack(side="left")
            tk.Label(row, text=title, bg=CARD, fg=TEXT, font=("Segoe UI", 12, "bold"), width=16, anchor="w").pack(side="left", padx=(8, 0))
            value = tk.Label(row, text=initial[key], bg=CARD, fg=MUTED, font=("Segoe UI", 11), anchor="w")
            value.pack(side="left")
            self.dots[key] = dot
            self.labels[key] = value

        tk.Label(
            body,
            text=(
                "You will need SteamVR, VRCFaceTracking, and VTube Studio.\n"
                "1. Start SteamVR with your headset on.\n"
                "2. Press Start and look straight ahead.\n"
                "3. Open VRCFaceTracking.\n"
                "4. Open VTube Studio and turn on Start API.\n"
                "5. Allow the popup in VTube Studio the first time."
            ),
            bg=BG, fg=TEXT, font=("Segoe UI", 11), justify="left", anchor="w",
        ).pack(anchor="w", padx=24, pady=(0, 6))

        links = tk.Frame(body, bg=BG)
        links.pack(anchor="w", padx=24, pady=(0, 8))
        self._link(links, "Get SteamVR", "https://store.steampowered.com/app/250820/SteamVR/")
        tk.Label(links, text="   ", bg=BG).pack(side="left")
        self._link(links, "Get VRCFaceTracking", "https://vrcft.io/")
        tk.Label(links, text="   ", bg=BG).pack(side="left")
        self._link(links, "Get VTube Studio", "https://denchisoft.com/")

        self.tune_btn = tk.Button(
            body, text="Adjustments   \u25b8", command=self.toggle_tune,
            bg=BG, fg=TEXT, activebackground=BG, activeforeground=TEXT,
            font=("Segoe UI", 13, "bold"), relief="flat", anchor="w", padx=0,
        )
        self.tune_btn.pack(fill="x", padx=24, pady=(8, 0))

        self.tune = tk.Frame(body, bg=BG)
        self._slider(self.tune, "Neck amount", 0.15, 1.0, bridge.HEAD_GAIN, "HEAD_GAIN", "Calmer", "Stronger")
        self._slider(self.tune, "Eye size", 0.15, 0.9, bridge.EYE_REST, "EYE_REST", "Smaller", "Wider")
        self._slider(self.tune, "Eye movement", 0.3, 1.6, bridge.EYE_GAZE, "EYE_GAZE", "Less", "More")
        self._slider(self.tune, "Mouth open", 0.3, 1.8, bridge.MOUTH_GAIN, "MOUTH_GAIN", "Less", "More")
        self._slider(self.tune, "Smile", 0.15, 0.85, bridge.SMILE_REST, "SMILE_REST", "Frown", "Smile")
        self._slider(self.tune, "Brows", 0.3, 1.8, bridge.BROW_GAIN, "BROW_GAIN", "Calmer", "Stronger")

        flips = tk.Frame(self.tune, bg=BG)
        flips.pack(anchor="w", pady=(10, 4))
        self.yaw = tk.BooleanVar(value=bridge.INVERT_YAW)
        self.pitch = tk.BooleanVar(value=bridge.INVERT_PITCH)
        self.roll = tk.BooleanVar(value=bridge.INVERT_ROLL)
        for text, var, attr in (
            ("Flip turn", self.yaw, "INVERT_YAW"),
            ("Flip nod", self.pitch, "INVERT_PITCH"),
            ("Flip tilt", self.roll, "INVERT_ROLL"),
        ):
            tk.Checkbutton(
                flips, text=text, variable=var, command=lambda a=attr, v=var: setattr(bridge, a, v.get()),
                bg=BG, fg=TEXT, selectcolor=CARD, activebackground=BG, activeforeground=TEXT,
                font=("Segoe UI", 10),
            ).pack(side="left", padx=(0, 12))

        buttons = tk.Frame(body, bg=BG)
        buttons.pack(fill="x", padx=24, pady=(12, 8))
        self.start_btn = tk.Button(buttons, text="Start", command=self.start, bg=GREEN, fg="#14210f", font=("Segoe UI", 13, "bold"), relief="flat", padx=18, pady=8)
        self.start_btn.pack(side="left")
        tk.Button(buttons, text="Stop", command=self.stop, bg=CARD, fg=TEXT, font=("Segoe UI", 13), relief="flat", padx=18, pady=8).pack(side="left", padx=8)
        tk.Button(buttons, text="Reset neck", command=bridge.recenter, bg=CARD, fg=TEXT, font=("Segoe UI", 13), relief="flat", padx=18, pady=8).pack(side="left")

        self.detail = tk.Label(body, text=bridge.status["detail"], bg=BG, fg=MUTED, font=("Segoe UI", 11), wraplength=400, justify="left")
        self.detail.pack(anchor="w", padx=24, pady=(8, 24))
        self._body = body
        self.after(400, self.refresh)
        self.after(50, self._lock_size)
        self.bind("<Configure>", self._guard_size)
        self.protocol("WM_DELETE_WINDOW", self.close)

    def _lock_size(self):
        self.update_idletasks()
        width = max(530, self._body.winfo_reqwidth() + 28)
        height = 520
        self.minsize(width, height)
        if self.winfo_width() < width or self.winfo_height() < height:
            self._locking = True
            self.geometry(f"{max(self.winfo_width(), width)}x{max(self.winfo_height(), 700)}")
            self._locking = False

    def _guard_size(self, event):
        if event.widget is not self or self._locking:
            return
        min_w, min_h = self.minsize()
        if event.width < min_w or event.height < min_h:
            self._locking = True
            self.geometry(f"{max(event.width, min_w)}x{max(event.height, min_h)}")
            self._locking = False

    def _link(self, parent, text, url):
        label = tk.Label(
            parent, text=text, bg=BG, fg="#9eb6ff", cursor="hand2",
            font=("Segoe UI", 11, "underline"),
        )
        label.pack(side="left")
        label.bind("<Button-1>", lambda _e: webbrowser.open(url))

    def _icon_file(self):
        if getattr(sys, "frozen", False):
            base = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
        else:
            base = os.path.dirname(os.path.abspath(__file__))
        return os.path.join(base, "icon.ico")

    def _set_icon(self):
        path = self._icon_file()
        if not os.path.exists(path):
            return
        try:
            self.iconbitmap(default=path)
        except tk.TclError:
            try:
                self.iconbitmap(path)
            except tk.TclError:
                pass

    def _wheel(self, event):
        self.canvas.yview_scroll(int(-event.delta / 120), "units")

    def toggle_tune(self):
        self.tune_open = not self.tune_open
        if self.tune_open:
            self.tune.pack(fill="x", padx=24, after=self.tune_btn)
            self.tune_btn.configure(text="Adjustments   \u25be")
        else:
            self.tune.pack_forget()
            self.tune_btn.configure(text="Adjustments   \u25b8")

    def _slider(self, parent, label, lo, hi, value, attr, left, right):
        var = tk.DoubleVar(value=value)
        self.vars[attr] = var
        tk.Label(parent, text=label, bg=BG, fg=TEXT, font=("Segoe UI", 11)).pack(anchor="w", pady=(8, 0))
        ttk.Scale(parent, from_=lo, to=hi, variable=var, command=lambda _v, a=attr, s=var: setattr(bridge, a, float(s.get()))).pack(fill="x")
        ends = tk.Frame(parent, bg=BG)
        ends.pack(fill="x")
        tk.Label(ends, text=left, bg=BG, fg=MUTED, font=("Segoe UI", 9)).pack(side="left")
        tk.Label(ends, text=right, bg=BG, fg=MUTED, font=("Segoe UI", 9)).pack(side="right")

    def _apply_sliders(self):
        for attr, var in self.vars.items():
            setattr(bridge, attr, float(var.get()))

    def start(self):
        if self.worker and self.worker.is_alive():
            return
        self._apply_sliders()
        bridge.recenter()
        self.worker = threading.Thread(target=bridge.main, daemon=True)
        self.worker.start()
        self.start_btn.configure(state="disabled")

    def stop(self):
        bridge.stop_event.set()
        self.start_btn.configure(state="normal")

    def close(self):
        bridge.stop_event.set()
        self.destroy()

    def refresh(self):
        for key, label in self.labels.items():
            text = bridge.status.get(key, "")
            label.configure(text=text)
            if text == "Connected":
                color = GREEN
            elif text.startswith("Waiting"):
                color = AMBER
            else:
                color = RED
            self.dots[key].configure(fg=color)
        self.detail.configure(text=bridge.status.get("detail", ""))
        if self.worker and not self.worker.is_alive():
            self.start_btn.configure(state="normal")
        self.after(400, self.refresh)


if __name__ == "__main__":
    App().mainloop()