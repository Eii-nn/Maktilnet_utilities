import json
import os
import sys
import threading
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Optional

from .config import CONFIG_FILE, RouterOSDesiredVersion
from .deployment_workflow import PreflightWorkflow
from .progress import INSTALL_STEPS, STEP_ORDER, friendly_error_message

# Visual states for the Windows-style step list
COLOR_PENDING = "#5a5a7a"
COLOR_ACTIVE = "#ffffff"
COLOR_ACTIVE_SUB = "#00d2ff"
COLOR_DONE = "#6c72a0"
COLOR_ERROR = "#e74c3c"
COLOR_SUCCESS = "#2ecc71"
COLOR_HINT = "#a2a8d3"
BG_MAIN = "#1a1a2e"
PAD_X = 24


class GUIOutput:
    """Redirects print() to the Tkinter Text widget, thread-safely."""

    def __init__(self, widget, root):
        self.widget = widget
        self.root = root

    def write(self, string):
        self.root.after(0, self._safe_write, string)

    def _safe_write(self, string):
        self.widget.insert(tk.END, string)
        self.widget.see(tk.END)

    def flush(self):
        pass


class ScrollableFrame(ttk.Frame):
    """Vertically scrollable frame that grows with its parent width."""

    def __init__(self, parent, bg=BG_MAIN, **kwargs):
        super().__init__(parent, **kwargs)
        self._bg = bg

        self.canvas = tk.Canvas(
            self,
            bg=bg,
            highlightthickness=0,
            borderwidth=0,
        )
        self.scrollbar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.inner = tk.Frame(self.canvas, bg=bg)

        self._window_id = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.canvas.configure(yscrollcommand=self.scrollbar.set)

        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.scrollbar.grid(row=0, column=1, sticky="ns")
        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(0, weight=1)

        self.inner.bind("<Configure>", self._on_inner_configure)
        self.canvas.bind("<Configure>", self._on_canvas_configure)

        self.canvas.bind("<Enter>", self._bind_mousewheel)
        self.canvas.bind("<Leave>", self._unbind_mousewheel)

    def _on_inner_configure(self, _event=None):
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _on_canvas_configure(self, event):
        self.canvas.itemconfigure(self._window_id, width=event.width)

    def _on_mousewheel(self, event):
        if sys.platform == "darwin":
            delta = event.delta
        else:
            delta = int(-1 * (event.delta / 120))
        self.canvas.yview_scroll(delta, "units")

    def _bind_mousewheel(self, _event=None):
        self.canvas.bind_all("<MouseWheel>", self._on_mousewheel)
        self.canvas.bind_all("<Button-4>", self._on_mousewheel_linux)
        self.canvas.bind_all("<Button-5>", self._on_mousewheel_linux)

    def _unbind_mousewheel(self, _event=None):
        self.canvas.unbind_all("<MouseWheel>")
        self.canvas.unbind_all("<Button-4>")
        self.canvas.unbind_all("<Button-5>")

    def _on_mousewheel_linux(self, event):
        if event.num == 4:
            self.canvas.yview_scroll(-1, "units")
        elif event.num == 5:
            self.canvas.yview_scroll(1, "units")


class PreflightApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Router Setup")
        self.root.geometry("520x720")
        self.root.configure(bg=BG_MAIN)
        self.root.minsize(360, 480)

        self._step_labels: dict[str, tk.Label] = {}
        self._sub_labels: dict[tuple[str, str], tk.Label] = {}
        self._current_step_id: Optional[str] = None
        self._current_sub_id: Optional[str] = None
        self._details_visible = False
        self._resize_after_id: Optional[str] = None

        self.root.grid_rowconfigure(0, weight=1)
        self.root.grid_columnconfigure(0, weight=1)

        self._setup_ui()
        self.load_config()
        self._reset_step_display()
        self.root.bind("<Configure>", self._on_root_configure)

    def _setup_ui(self):
        self.shell = tk.Frame(self.root, bg=BG_MAIN)
        self.shell.grid(row=0, column=0, sticky="nsew")
        self.shell.grid_columnconfigure(0, weight=1)
        # 0 header, 1 password, 2 steps (expand), 3 progress, 4 hint,
        # 5 details btn, 6 log (expand), 7 button
        self.shell.grid_rowconfigure(2, weight=2)
        self.shell.grid_rowconfigure(6, weight=3)

        # ── Header ────────────────────────────────────────────────────────────
        header = tk.Frame(self.shell, bg=BG_MAIN)
        header.grid(row=0, column=0, sticky="ew", padx=PAD_X, pady=(16, 8))

        tk.Label(
            header,
            text="AUTO PREFLIGHT CONFIGURATION",
            fg="#00d2ff",
            bg=BG_MAIN,
            font=("Segoe UI", 18, "bold"),
            anchor="w",
        ).pack(fill="x")

        tk.Label(
            header,
            text=f"Router software version {RouterOSDesiredVersion}" + " - 192.168.88.100",
            fg=COLOR_HINT,
            bg=BG_MAIN,
            font=("Segoe UI", 10),
            anchor="w",
        ).pack(fill="x", pady=(4, 8))

        tk.Frame(header, height=1, bg="#2a2a4a").pack(fill="x")

        # ── Password ──────────────────────────────────────────────────────────
        form = tk.Frame(self.shell, bg=BG_MAIN)
        form.grid(row=1, column=0, sticky="ew", padx=PAD_X, pady=(8, 4))

        tk.Label(
            form,
            text="Router password",
            fg=COLOR_HINT,
            bg=BG_MAIN,
            font=("Segoe UI", 9),
            anchor="w",
        ).pack(fill="x")

        self.mikrotik_entry = tk.Entry(
            form,
            show="*",
            bg="#16213e",
            fg="white",
            insertbackground="white",
            relief="flat",
            highlightthickness=1,
            highlightbackground="#2a2a6e",
            highlightcolor="#00d2ff",
            font=("Segoe UI", 10),
        )
        self.mikrotik_entry.pack(fill="x", ipady=7, pady=(4, 0))

        # ── Scrollable status / steps ─────────────────────────────────────────
        status_outer = tk.Frame(self.shell, bg=BG_MAIN)
        status_outer.grid(row=2, column=0, sticky="nsew", padx=PAD_X, pady=(4, 0))
        status_outer.grid_rowconfigure(1, weight=1)
        status_outer.grid_columnconfigure(0, weight=1)

        tk.Label(
            status_outer,
            text="Status",
            fg=COLOR_HINT,
            bg=BG_MAIN,
            font=("Segoe UI", 10, "bold"),
            anchor="w",
        ).grid(row=0, column=0, sticky="ew", pady=(0, 6))

        self.steps_scroll = ScrollableFrame(status_outer, bg=BG_MAIN)
        self.steps_scroll.grid(row=1, column=0, sticky="nsew")
        self.steps_frame = self.steps_scroll.inner
        self.steps_scroll.scrollbar.config(style="Pre.Vertical.TScrollbar")

        for step in INSTALL_STEPS:
            step_row = tk.Frame(self.steps_frame, bg=BG_MAIN)
            step_row.pack(fill="x", pady=(0, 2))

            step_label = tk.Label(
                step_row,
                text=f"○  {step['label']}",
                fg=COLOR_PENDING,
                bg=BG_MAIN,
                font=("Segoe UI", 10),
                anchor="w",
                justify="left",
            )
            step_label.pack(fill="x", anchor="w")
            self._step_labels[step["id"]] = step_label

            for sub in step.get("subs", []):
                sub_label = tk.Label(
                    step_row,
                    text=f"    –  {sub['label']}",
                    fg=COLOR_PENDING,
                    bg=BG_MAIN,
                    font=("Segoe UI", 9),
                    anchor="w",
                    justify="left",
                )
                sub_label.pack(fill="x", anchor="w", padx=(8, 0))
                self._sub_labels[(step["id"], sub["id"])] = sub_label

        # ── Progress ──────────────────────────────────────────────────────────
        progress_frame = tk.Frame(self.shell, bg=BG_MAIN)
        progress_frame.grid(row=3, column=0, sticky="ew", padx=PAD_X, pady=(10, 4))
        progress_frame.grid_columnconfigure(0, weight=1)

        pb_style = ttk.Style()
        pb_style.theme_use("clam")
        pb_style.configure(
            "Pre.Horizontal.TProgressbar",
            troughcolor="#16213e",
            background="#00d2ff",
            bordercolor=BG_MAIN,
            lightcolor="#00d2ff",
            darkcolor="#0099bb",
        )
        pb_style.configure(
            "Pre.Vertical.TScrollbar",
            troughcolor=BG_MAIN,
            background="#2a2a4a",
            bordercolor=BG_MAIN,
            lightcolor="#2a2a4a",
            darkcolor="#2a2a4a",
            troughrelief="flat",
            relief="flat",
            arrowcolor="#6c72a0",
            gripcount=0,
        )

        self.progress = ttk.Progressbar(
            progress_frame,
            style="Pre.Horizontal.TProgressbar",
            maximum=100,
        )
        self.progress.grid(row=0, column=0, sticky="ew")

        # ── Hint line ─────────────────────────────────────────────────────────
        self.status_label = tk.Label(
            self.shell,
            text="Enter the router password, then press Start.",
            bg=BG_MAIN,
            fg=COLOR_HINT,
            font=("Segoe UI", 9),
            wraplength=400,
            justify="left",
            anchor="w",
        )
        self.status_label.grid(row=4, column=0, sticky="ew", padx=PAD_X, pady=(0, 6))

        # ── Technical details toggle ──────────────────────────────────────────
        self.details_btn = tk.Button(
            self.shell,
            text="▶  Show technical details",
            command=self._toggle_details,
            bg=BG_MAIN,
            fg="#6c72a0",
            activebackground=BG_MAIN,
            activeforeground="#00d2ff",
            relief="flat",
            font=("Segoe UI", 8),
            cursor="hand2",
            anchor="w",
        )
        self.details_btn.grid(row=5, column=0, sticky="ew", padx=PAD_X, pady=(0, 4))

        # ── Log (hidden until expanded) ───────────────────────────────────────
        self.log_container = tk.Frame(
            self.shell,
            bg="#0f0f1b",
            highlightthickness=1,
            highlightbackground="#2a2a4a",
        )
        self.log_container.grid_columnconfigure(0, weight=1)
        self.log_container.grid_rowconfigure(0, weight=1)

        self.log_area = tk.Text(
            self.log_container,
            bg="#0a0a16",
            fg="#00ff9f",
            font=("Courier", 8),
            relief="flat",
            selectbackground="#2a2a6e",
            wrap="word",
            height=6,
        )
        log_scroll = ttk.Scrollbar(
            self.log_container,
            command=self.log_area.yview,
            style="Pre.Vertical.TScrollbar",
        )
        self.log_area.configure(yscrollcommand=log_scroll.set)
        self.log_area.grid(row=0, column=0, sticky="nsew", padx=4, pady=4)
        log_scroll.grid(row=0, column=1, sticky="ns")

        # ── Start button (pinned to bottom) ───────────────────────────────────
        btn_row = tk.Frame(self.shell, bg=BG_MAIN)
        btn_row.grid(row=7, column=0, sticky="ew", padx=PAD_X, pady=(8, 16))

        self.deploy_btn = tk.Button(
            btn_row,
            text="Start Setup",
            command=self.on_deploy_click,
            bg="#00d2ff",
            fg="#0a0a16",
            activebackground="#0099bb",
            activeforeground="#0a0a16",
            font=("Segoe UI", 11, "bold"),
            relief="flat",
            padx=32,
            pady=10,
            cursor="hand2",
        )
        self.deploy_btn.pack(fill="x")

    def _on_root_configure(self, event):
        if event.widget is not self.root:
            return
        if self._resize_after_id:
            self.root.after_cancel(self._resize_after_id)
        self._resize_after_id = self.root.after(80, self._apply_responsive_layout)

    def _apply_responsive_layout(self):
        self._resize_after_id = None
        width = max(self.root.winfo_width() - (PAD_X * 2) - 8, 200)
        self.status_label.config(wraplength=width)

        for label in self._step_labels.values():
            label.config(wraplength=width)
        for label in self._sub_labels.values():
            label.config(wraplength=max(width - 16, 160))

    def _toggle_details(self):
        self._details_visible = not self._details_visible
        if self._details_visible:
            self.details_btn.config(text="▼  Hide technical details")
            self.log_container.grid(row=6, column=0, sticky="nsew", padx=PAD_X, pady=(0, 4))
        else:
            self.details_btn.config(text="▶  Show technical details")
            self.log_container.grid_forget()
        self._apply_responsive_layout()

    def _reset_step_display(self):
        self._current_step_id = None
        self._current_sub_id = None
        for step in INSTALL_STEPS:
            label = self._step_labels[step["id"]]
            label.config(
                text=f"○  {step['label']}",
                fg=COLOR_PENDING,
                font=("Segoe UI", 10),
            )
            for sub in step.get("subs", []):
                sub_label = self._sub_labels[(step["id"], sub["id"])]
                sub_label.config(
                    text=f"    –  {sub['label']}",
                    fg=COLOR_PENDING,
                    font=("Segoe UI", 9),
                )

    def _refresh_step_display(self, step_id: str, sub_id: Optional[str], failed: bool = False):
        active_index = STEP_ORDER.index(step_id) if step_id in STEP_ORDER else -1

        for index, step in enumerate(INSTALL_STEPS):
            sid = step["id"]
            main_label = self._step_labels[sid]
            subs = step.get("subs", [])

            if index < active_index:
                main_label.config(
                    text=f"✓  {step['label']}",
                    fg=COLOR_DONE,
                    font=("Segoe UI", 10),
                )
                for sub in subs:
                    self._sub_labels[(sid, sub["id"])].config(
                        text=f"    ✓  {sub['label']}",
                        fg=COLOR_DONE,
                        font=("Segoe UI", 9),
                    )
            elif index > active_index:
                main_label.config(
                    text=f"○  {step['label']}",
                    fg=COLOR_PENDING,
                    font=("Segoe UI", 10),
                )
                for sub in subs:
                    self._sub_labels[(sid, sub["id"])].config(
                        text=f"    –  {sub['label']}",
                        fg=COLOR_PENDING,
                        font=("Segoe UI", 9),
                    )
            else:
                marker = "●" if not failed else "✕"
                color = COLOR_ERROR if failed else COLOR_ACTIVE
                main_label.config(
                    text=f"{marker}  {step['label']}",
                    fg=color,
                    font=("Segoe UI", 10, "bold"),
                )

                if not subs:
                    continue

                sub_index = next(
                    (i for i, sub in enumerate(subs) if sub["id"] == sub_id),
                    -1,
                )
                for sub_i, sub in enumerate(subs):
                    sub_label = self._sub_labels[(sid, sub["id"])]
                    if sub_id and sub_i < sub_index:
                        sub_label.config(
                            text=f"    ✓  {sub['label']}",
                            fg=COLOR_DONE,
                            font=("Segoe UI", 9),
                        )
                    elif sub_id and sub["id"] == sub_id:
                        sub_marker = "✕" if failed else "–"
                        sub_color = COLOR_ERROR if failed else COLOR_ACTIVE_SUB
                        sub_label.config(
                            text=f"    {sub_marker}  {sub['label']}",
                            fg=sub_color,
                            font=("Segoe UI", 9, "bold"),
                        )
                    else:
                        sub_label.config(
                            text=f"    –  {sub['label']}",
                            fg=COLOR_PENDING,
                            font=("Segoe UI", 9),
                        )

    def save_config(self):
        try:
            with open(CONFIG_FILE, "w") as f:
                json.dump({"m_pass": self.mikrotik_entry.get()}, f)
        except Exception:
            pass

    def load_config(self):
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, "r") as f:
                    data = json.load(f)
                    self.mikrotik_entry.insert(0, data.get("m_pass", ""))
            except Exception:
                pass

    def update_progress(
        self,
        percent: int,
        step_id: str,
        sub_id: Optional[str] = None,
        hint: Optional[str] = None,
        failed: bool = False,
    ):
        self.root.after(
            0,
            lambda: self._apply_progress(percent, step_id, sub_id, hint, failed),
        )

    def _apply_progress(
        self,
        percent: int,
        step_id: str,
        sub_id: Optional[str],
        hint: Optional[str],
        failed: bool,
    ):
        self._current_step_id = step_id
        self._current_sub_id = sub_id
        self.progress["value"] = percent
        self._refresh_step_display(step_id, sub_id, failed=failed)

        if failed:
            self.status_label.config(
                text=hint or "Setup could not be completed.",
                fg=COLOR_ERROR,
            )
        elif step_id == "finish":
            self.status_label.config(
                text=hint or "Your router is ready. You can close this window.",
                fg=COLOR_SUCCESS,
            )
        else:
            self.status_label.config(
                text=hint or "Please wait. Do not unplug the router.",
                fg=COLOR_HINT,
            )

        self._apply_responsive_layout()

    def on_success(self):
        self.root.after(
            0,
            lambda: messagebox.showinfo(
                "Setup Complete",
                "Your router has been set up successfully.",
            ),
        )
        self.root.after(0, lambda: self.deploy_btn.config(state="normal"))

    def on_error(self, e):
        friendly = friendly_error_message(e)
        self.update_progress(
            int(self.progress["value"]),
            self._current_step_id or "find_router",
            self._current_sub_id,
            hint=friendly,
            failed=True,
        )
        self.root.after(
            0,
            lambda: messagebox.showerror("Setup Failed", friendly),
        )
        self.root.after(0, lambda: self.deploy_btn.config(state="normal"))

    def on_deploy_click(self):
        self.deploy_btn.config(state="disabled")
        self.log_area.delete(1.0, tk.END)
        self.save_config()
        self._reset_step_display()
        self.progress["value"] = 0
        self.status_label.config(
            text="Starting setup. Please wait…",
            fg=COLOR_HINT,
        )

        password = self.mikrotik_entry.get() or ""
        sys.stdout = GUIOutput(self.log_area, self.root)

        workflow = PreflightWorkflow(
            password=password,
            on_progress=self.update_progress,
            on_success=self.on_success,
            on_error=self.on_error,
        )

        threading.Thread(target=workflow.run, daemon=True).start()
