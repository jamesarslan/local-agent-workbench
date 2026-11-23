"""Windows desktop launcher for a local OMP workspace."""

import queue

import subprocess

import sys

import threading

import tkinter as tk

from tkinter import filedialog, messagebox, simpledialog, ttk

from pathlib import Path


from tkinterdnd2 import DND_FILES, TkinterDnD

import catalog
from host import open_folder

from runtime import (
    DATA,
    ROOT,
    URL,
    key,
    owned,
    preset_rows,
    settings,
    start,
    stop,
    write_json,
)


BG = "#111917"

PANEL = "#1b2623"

FG = "#e9efea"

MUTED = "#a8b8af"

ACCENT = "#a7e5b7"


def in_form(value):
    """True for a value the form holds as text or a switch; lists and tables keep their saved value."""
    return not isinstance(value, (list, dict))


class Workbench:
    def __init__(self, win):

        self.win = win

        win.title("Agent Workbench")

        win.geometry("1120x815")

        win.minsize(940, 700)

        win.configure(bg=BG)

        self.s = settings()

        self.q = queue.Queue()

        self.busy = False

        self.vars = {
            k: (
                tk.BooleanVar(value=v)
                if isinstance(v, bool)
                else tk.StringVar(value=str(v))
            )
            for k, v in self.s.items()
            if k not in ("presets", "named_profiles")
        }

        self.gpu, self.total, free, ram = catalog.hardware()

        self.ram = ram

        style = ttk.Style(win)

        style.theme_use("clam")

        style.configure(
            ".",
            font=("Segoe UI", 10),
            background=BG,
            foreground=FG,
            fieldbackground=PANEL,
        )

        style.configure("TNotebook", borderwidth=0)

        style.configure("TNotebook.Tab", padding=(18, 8), background=PANEL)

        style.map(
            "TNotebook.Tab",
            background=[("selected", ACCENT)],
            foreground=[("selected", BG)],
        )

        style.configure("TButton", padding=(13, 7), background=PANEL)

        style.map("TButton", background=[("active", "#365044")])

        style.configure(
            "Go.TButton",
            background=ACCENT,
            foreground=BG,
            font=("Segoe UI", 10, "bold"),
        )

        style.map("Go.TButton", background=[("active", "#c5f4d0")])

        style.configure(
            "Treeview",
            rowheight=31,
            fieldbackground=PANEL,
            background=PANEL,
            foreground=FG,
        )

        style.configure(
            "Treeview.Heading", background="#253530", foreground=FG, padding=5
        )

        style.map(
            "Treeview",
            background=[("selected", "#375f4d")],
            foreground=[("selected", "white")],
        )

        style.map(
            "TCombobox",
            fieldbackground=[("readonly", PANEL)],
            foreground=[("readonly", FG)],
        )

        style.configure("TCheckbutton", padding=4)

        win.option_add("*TCombobox*Listbox.background", PANEL)

        win.option_add("*TCombobox*Listbox.foreground", FG)

        head = ttk.Frame(win, padding=(24, 18, 24, 10))
        head.pack(fill="x")

        ttk.Label(head, text="Agent Workbench", font=("Segoe UI Semibold", 24)).pack(
            side="left"
        )

        ttk.Label(
            head,
            text=f"{self.gpu.strip()}   /   {self.total:.1f} GiB VRAM   /   {ram:.1f} GiB RAM",
            foreground=MUTED,
        ).pack(side="right")

        self.tabs = ttk.Notebook(win)

        self.lib = self.tab("Model library")
        self.config = self.tab("Agent settings")

        self.hub = self.tab("Hugging Face")
        self.perf = self.tab("Benchmarks")

        bar = ttk.Frame(win, padding=(24, 5, 24, 17))
        bar.pack(side="bottom", fill="x")
        self.tabs.pack(fill="both", expand=True, padx=24, pady=6)

        self.status = tk.StringVar(value="Ready")

        ttk.Label(
            bar, textvariable=self.status, foreground=ACCENT, wraplength=520
        ).pack(side="left")

        self.button(
            bar, "Stop model", lambda: self.task("Stopping", stop), side="right"
        )

        self.button(bar, "Launch agent", lambda: self.run(True), side="right", go=True)

        self.button(bar, "Serve API", lambda: self.run(False), side="right")

        self.library()
        self.agent_settings()
        self.huggingface()
        self.benchmarks()
        from integration_ui import panels

        panels(self)

        win.drop_target_register(DND_FILES)

        win.dnd_bind("<<Drop>>", self.drop)

        win.protocol("WM_DELETE_WINDOW", self.close)

        self.scan()

        win.after(100, self.poll)

    def tab(self, title):

        tab = ttk.Frame(self.tabs, padding=15)
        self.tabs.add(tab, text=title)
        return tab

    def button(self, parent, text, fn, side="left", go=False):

        ttk.Button(
            parent, text=text, command=fn, style="Go.TButton" if go else "TButton"
        ).pack(side=side, padx=3)

    def row(self, parent):

        frame = ttk.Frame(parent)
        frame.pack(fill="x", pady=5)
        return frame

    def label(self, frame, text):

        ttk.Label(frame, text=text, width=17, anchor="w").pack(side="left")

    def tree(self, parent, columns):

        box = ttk.Frame(parent)
        box.pack(fill="both", expand=True, pady=7)

        view = ttk.Treeview(
            box, columns=[c[0] for c in columns], show="headings", selectmode="browse"
        )

        for name, width in columns:
            view.heading(name, text=name)
            view.column(name, width=width, stretch=name == columns[0][0])

        scroll = ttk.Scrollbar(box, orient="vertical", command=view.yview)

        view.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        view.pack(fill="both", expand=True)

        return view

    def library(self):

        r = self.row(self.lib)
        ttk.Label(r, text="Local weights", font=("Segoe UI Semibold", 16)).pack(
            side="left"
        )

        self.button(r, "Choose folder", self.pick_folder, "right")
        self.button(r, "Rescan", self.scan, "right")

        ttk.Label(self.lib, textvariable=self.vars["library"], foreground=MUTED).pack(
            anchor="w"
        )

        self.models = self.tree(
            self.lib, [("Model", 470), ("Size", 85), ("Memory estimate", 155)]
        )

        self.models.bind("<<TreeviewSelect>>", self.select)

        ttk.Label(
            self.lib,
            text="Drop a model folder or weight file here to use it in place.",
            foreground=MUTED,
        ).pack(anchor="w")

        r = self.row(self.lib)
        self.label(r, "Runtime")

        ttk.Combobox(
            r,
            textvariable=self.vars["engine"],
            values=["Auto", "Strata", "llama.cpp", "Flash preset", "vLLM", "Custom"],
            state="readonly",
            width=14,
        ).pack(side="left")

        ttk.Label(r, text="Context tokens").pack(side="left", padx=(24, 8))

        ttk.Combobox(
            r,
            textvariable=self.vars["context"],
            values=[8192, 32768, 65536, 131072, 262144, 524288, 1048576],
            width=11,
        ).pack(side="left")

        for k, t in [("draft", "Drafting"), ("vision", "Vision companion")]:
            ttk.Checkbutton(r, text=t, variable=self.vars[k]).pack(side="left", padx=6)

        r = self.row(self.lib)
        self.label(r, "Strata KV cache")

        ttk.Combobox(
            r,
            textvariable=self.vars["kv"],
            values=["auto", "fp16", "fp8"],
            state="readonly",
            width=14,
        ).pack(side="left")

        ttk.Checkbutton(
            r, text="Use matched Qwen Pi LoRA", variable=self.vars["lora"]
        ).pack(side="left", padx=12)

        self.button(r, "Save profile", self.save, side="right")

        r = self.row(self.lib)
        self.label(r, "Saved setups")

        self.named = ttk.Combobox(
            r, values=sorted(self.s["named_profiles"]), state="readonly", width=34
        )

        self.named.pack(side="left")

        self.named.bind("<<ComboboxSelected>>", self.apply_named)

        self.button(r, "Save as…", self.save_named)

        def shortcut():
            from shortcuts import create

            name = self.named.get()
            if name not in self.s["named_profiles"]:
                raise ValueError("Select or save a named setup first.")
            self.save()
            path = create(name)
            open_folder(path.parent)

        self.button(r, "Create launcher", shortcut)

    def save_named(self):

        name = simpledialog.askstring("Save setup", "Setup name", parent=self.win)

        if name and name.strip():
            name = name.strip()

            if name in self.s["named_profiles"] and not messagebox.askyesno(
                "Save setup", "Replace this saved setup?"
            ):
                return

            s = self.save()

            self.s["named_profiles"][name] = {k: s[k] for k in self.vars}

            write_json(DATA / "settings.json", self.s)
            self.named.configure(values=sorted(self.s["named_profiles"]))

            self.named.set(name)
            self.status.set("Saved " + name)

    def apply_named(self, event=None):

        data = self.s["named_profiles"][self.named.get()]

        for k, value in data.items():
            if in_form(value):
                self.vars[k].set(value)
            elif k not in ("presets", "named_profiles"):
                self.s[k] = value

        self.prompt.delete("1.0", "end")
        self.prompt.insert("1.0", self.vars["prompt"].get())

        self.scan()
        self.save()

    def scan(self):

        for k in self.models.get_children():
            self.models.delete(k)

        rows = catalog.discover(self.vars["library"].get())

        for name, _, size in rows:
            self.models.insert(
                "",
                "end",
                iid=name,
                values=(
                    Path(name).name,
                    f"{size / catalog.GIB:.1f} GiB",
                    catalog.fit(size, self.total, self.ram),
                ),
            )

        by_name = {Path(name).name: name for name, _, _ in rows}
        for template in preset_rows():
            path = by_name.get(template["file"])
            if not path:
                continue
            name = template["name"]
            saved = self.s["named_profiles"].get(name)
            if saved and Path(saved["model"]).is_file():
                continue
            values = {
                k: v for k, v in template.items() if k not in ("file", "name")
            }
            values["model"] = path
            self.s["named_profiles"][name] = values
        self.named.configure(values=sorted(self.s["named_profiles"]))
        selected = self.vars["model"].get()

        if selected and self.models.exists(selected):
            self.models.selection_set(selected)
            self.models.see(selected)

        elif rows:
            popular = next(
                (n for n, _, _ in rows if n.endswith("SQ-20.strata")), rows[0][0]
            )

            self.models.selection_set(popular)

        proc, rec = owned()

        self.status.set(
            "Serving " + Path(rec["spec"]["model"]).name
            if proc
            else f"{len(rows)} model configurations in library"
        )

    def select(self, event=None):

        chosen = self.models.selection()

        if not chosen:
            return

        name = chosen[0]

        if name != self.vars["model"].get():
            self.remember()

            for k, v in dict(
                engine="Auto",
                context=32768,
                draft=True,
                vision=True,
                kv="auto",
                lora=True,
            ).items():
                self.vars[k].set(v)

            for k, v in self.s["presets"].get(name, {}).items():
                self.vars[k].set(v)

        self.vars["model"].set(name)

    def remember(self):

        name = self.vars["model"].get()

        if name:
            self.s["presets"][name] = {
                k: self.vars[k].get()
                for k in ["engine", "context", "draft", "vision", "kv", "lora"]
            }

    def drop(self, event):

        paths = self.win.tk.splitlist(event.data)

        if paths:
            p = Path(paths[0])
            self.vars["library"].set(str(p if p.is_dir() else p.parent))

            self.scan()

            if self.models.exists(str(p)):
                self.models.selection_set(str(p))

            self.tabs.select(self.lib)

    def pick_folder(self):

        path = filedialog.askdirectory()

        if path:
            self.vars["library"].set(path)
            self.scan()

    def agent_settings(self):

        r = self.row(self.config)
        self.label(r, "Working directory")

        ttk.Entry(r, textvariable=self.vars["workspace"]).pack(
            side="left", fill="x", expand=True
        )

        def choose():

            p = filedialog.askdirectory()

            if p:
                self.vars["workspace"].set(p)

        self.button(r, "Browse", choose)

        r = self.row(self.config)
        self.label(r, "Thinking effort")

        ttk.Combobox(
            r,
            textvariable=self.vars["thinking"],
            values=["off", "low", "medium", "high"],
            state="readonly",
            width=12,
        ).pack(side="left")

        r = self.row(self.config)
        self.label(r, "MCP servers")

        for k, t in [
            ("search", "Web search"),
            ("firecrawl", "Firecrawl"),
            ("blender", "Blender"),
            ("desktop", "Windows"),
        ]:
            ttk.Checkbutton(r, text=t, variable=self.vars[k]).pack(side="left", padx=4)

        r = self.row(self.config)
        self.button(r, "Start Firecrawl", lambda: self.web("web-start"))

        self.button(r, "Stop Firecrawl", lambda: self.web("web-stop"))

        ttk.Label(
            self.config,
            text="OMP system prompt supplement",
            font=("Segoe UI Semibold", 13),
        ).pack(anchor="w", pady=(16, 5))

        self.prompt = tk.Text(
            self.config,
            bg=PANEL,
            fg=FG,
            insertbackground=FG,
            font=("Segoe UI", 11),
            wrap="word",
            height=7,
            padx=12,
            pady=10,
        )

        self.prompt.pack(fill="both", expand=True)
        self.prompt.insert("1.0", self.vars["prompt"].get())

        r = self.row(self.config)
        self.button(r, "Save settings", self.save)
        self.button(r, "Open local files", lambda: open_folder(DATA))

        def copy_key():

            self.win.clipboard_clear()
            self.win.clipboard_append(key())
            self.status.set("Local API key copied")

        self.button(r, "Copy API key", copy_key)

        ttk.Label(r, text=URL + "/v1", foreground=MUTED).pack(side="right")

    def web(self, action):

        self.task(
            "Updating Firecrawl",
            lambda: subprocess.run(
                [sys.executable, str(ROOT / "cli.py"), action],
                check=True,
                stdin=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            ),
        )

    def save(self):

        self.remember()

        self.vars["prompt"].set(self.prompt.get("1.0", "end-1c"))

        s = {
            **self.s,
            **{k: v.get() for k, v in self.vars.items() if in_form(self.s.get(k))},
        }

        try:
            s["context"] = int(s["context"])
            assert 1024 <= s["context"] <= 1048576

        except (ValueError, AssertionError):
            raise ValueError("Context must be between 1,024 and 1,048,576 tokens.")

        write_json(DATA / "settings.json", s)
        self.s = s
        self.status.set("Profile saved")
        return dict(s)

    def run(self, agent):

        s = self.save()

        from connections import launch

        self.task(
            "Opening " + s["harness"] if agent else "Loading " + Path(s["model"]).name,
            lambda: (launch if agent else start)(s),
            lambda _: self.status.set(
                s["harness"] + " opened" if agent else "Serving " + URL + "/v1"
            ),
        )

    def huggingface(self):

        r = self.row(self.hub)
        self.query = tk.StringVar(value="strata-engine/Inkling-2-Mini-Strata")

        ttk.Entry(r, textvariable=self.query).pack(side="left", expand=True, fill="x")

        self.button(r, "Find models", self.lookup)

        tip = (
            "Inkling SQ-20 is the highest fidelity Strata pack recommended for this GPU."
            if self.total >= 24
            else "Inkling SQ-15 is a candidate for this GPU."
            if self.total >= 16
            else "Inkling SQ-10 is a candidate for this GPU."
            if self.total >= 12
            else "Smaller quants may need CPU offload on this system."
        )

        ttk.Label(self.hub, text=tip, foreground=ACCENT).pack(anchor="w", pady=(6, 3))

        ttk.Label(
            self.hub,
            text="Search by model name or enter an owner/repository. Double-click a repository to see its files.",
            foreground=MUTED,
        ).pack(anchor="w")

        self.remote = self.tree(
            self.hub,
            [("Repository or file", 500), ("Download", 95), ("Memory estimate", 155)],
        )

        self.remote.bind("<Double-1>", lambda e: self.remote_choice(False))

        r = self.row(self.hub)
        self.button(
            r, "Download selected weights", lambda: self.remote_choice(True), go=True
        )
        self.hub_vision = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            r, text="Include native Inkling vision companion", variable=self.hub_vision
        ).pack(side="left")

        self.hubrows = []

    def lookup(self):

        query = self.query.get().strip()

        def display(rows):

            self.hubrows = rows

            for i in self.remote.get_children():
                self.remote.delete(i)

            for i, row in enumerate(rows):
                vals = (
                    (row, "", "")
                    if isinstance(row, str)
                    else (
                        row[2],
                        f"{row[4] / catalog.GIB:.1f} GiB",
                        catalog.fit(row[4], self.total, self.ram),
                    )
                )

                self.remote.insert("", "end", iid=str(i), values=vals)

            self.status.set(f"{len(rows)} catalog results")

        self.task("Searching Hugging Face", lambda: catalog.hub_files(query), display)

    def remote_choice(self, download):

        pick = self.remote.selection()

        if not pick:
            return

        row = self.hubrows[int(pick[0])]

        if isinstance(row, str):
            self.query.set(row)
            self.lookup()
            return

        folder = self.vars["library"].get()

        if download:
            vision = self.hub_vision.get()
            self.task(
                "Downloading " + Path(row[2]).name,
                lambda: catalog.download(row, folder, vision),
                lambda p: (
                    self.scan(),
                    self.models.selection_set(str(p.resolve())),
                    self.tabs.select(self.lib),
                ),
            )

    def benchmarks(self):

        self.runs = self.tree(
            self.perf,
            [
                ("Run", 385),
                ("Decode tok/s", 110),
                ("First token", 100),
                ("Short checks", 105),
            ],
        )

        self.refresh_runs()

        r = self.row(self.perf)

        self.button(r, "Benchmark selected model", self.benchmark)

        self.button(r, "Open measurements", lambda: open_folder(ROOT / "results"))

        ttk.Label(
            self.perf,
            text="Single request. Decode includes reasoning tokens. The five short checks have a 96-token output limit.",
            foreground=MUTED,
        ).pack(anchor="w")

    def refresh_runs(self):

        import json

        for i in self.runs.get_children():
            self.runs.delete(i)

        for p in sorted(
            [*(ROOT / "results").glob("*.json"), *DATA.glob("benchmark-*.json")]
        ):
            d = json.loads(p.read_text(encoding="utf8"))

            if not isinstance(d, dict) or "median_decode_tps" not in d:
                continue

            tt = d.get("median_ttft_s")

            self.runs.insert(
                "",
                "end",
                values=(
                    d["condition"],
                    f"{d['median_decode_tps']:.1f}",
                    f"{tt:.2f} s" if tt else "—",
                    f"{d['checks_passed']} / {d['checks_total']}",
                ),
            )

    def benchmark(self):

        self.save()

        self.task(
            "Running benchmark",
            lambda: subprocess.run(
                [sys.executable, str(ROOT / "cli.py"), "bench"],
                check=True,
                stdin=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            ),
            lambda _: (self.refresh_runs(), self.status.set("Benchmark saved")),
        )

    def task(self, label, fn, done=None):

        if self.busy:
            self.status.set("An operation is already running")
            return

        self.busy = True
        self.status.set(label + " …")

        def worker():

            try:
                self.q.put((done, fn(), None))

            except Exception as err:
                self.q.put((None, None, str(err)))

        threading.Thread(target=worker, daemon=True).start()

    def poll(self):

        try:
            done, result, error = self.q.get_nowait()

        except queue.Empty:
            pass

        else:
            self.busy = False

            try:
                if error:
                    self.status.set("Operation failed")
                    messagebox.showerror("Agent Workbench", error)

                elif done:
                    done(result)

                else:
                    self.status.set("Done")

            except Exception as err:
                self.status.set("Operation failed")
                messagebox.showerror("Agent Workbench", str(err))

        self.win.after(125, self.poll)

    def close(self):

        if self.busy:
            messagebox.showinfo(
                "Agent Workbench",
                "Wait for the current operation to finish before closing.",
            )
            return

        self.save()
        self.win.destroy()


if __name__ == "__main__":
    root = TkinterDnD.Tk()

    root.report_callback_exception = lambda kind, error, tb: messagebox.showerror(
        "Agent Workbench", str(error)
    )

    app = Workbench(root)
    if len(sys.argv) > 1:
        for tab in app.tabs.tabs():
            if app.tabs.tab(tab, "text") == sys.argv[1]:
                app.tabs.select(tab)
                break
    root.mainloop()
