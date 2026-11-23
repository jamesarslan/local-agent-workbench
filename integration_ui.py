"""Additional desktop panels for agent backends, tools, containers and live OMP traces."""

import datetime
import json
import queue
import subprocess
import threading
import time
import tkinter as tk
from tkinter import filedialog, simpledialog, ttk

import boxes
import connections
import studio
import tooling
from host import open_folder, safe_argv
from runtime import DATA, QUIET


def panels(app):
    conn = app.tab("Connections")
    tools = app.tab("Setup")
    runs = app.tab("Trace")

    def entry(tab, title, key, values=None):
        r = app.row(tab)
        app.label(r, title)
        field = (
            ttk.Combobox(r, textvariable=app.vars[key], values=values)
            if values is not None
            else ttk.Entry(r, textvariable=app.vars[key])
        )
        field.pack(side="left", fill="x", expand=True)
        return field

    entry(conn, "Engine release", "channel", ["Tested", "Updated", "Unsloth"])
    entry(conn, "Harness", "harness", connections.HARNESS)
    entry(conn, "Provider", "provider", list(connections.PROVIDERS))
    entry(conn, "Attached API URL", "endpoint")
    models = entry(conn, "Provider model ID", "remote_model", [])

    def load_models():
        s = app.save()

        def show(ids):
            models.configure(values=ids)
            if ids and s["remote_model"] not in ids:
                models.set(ids[0])
            app.status.set(f"{len(ids)} models available")

        app.task("Querying provider", lambda: connections.discover(s), show)

    def credential():
        s = app.save()
        if (
            s["provider"] not in connections.PROVIDER_KEYS
            and s["provider"] != "Attached endpoint"
        ):
            raise ValueError("Choose an API provider to store a credential.")
        origin, _ = connections.endpoint(s)
        value = simpledialog.askstring(
            "System credential vault", "API key for " + origin, show="*", parent=app.win
        )
        if value:
            stored = connections.store_key(s, value)
            app.status.set(connections.key_message(stored, s))

    r = app.row(conn)
    app.button(r, "List models", load_models)
    app.button(r, "Store API key", credential)
    entry(conn, "vLLM checkpoint", "vllm_model")
    entry(conn, "vLLM GPU fraction", "vllm_fraction", ["0.40", "0.65", "0.80", "0.90"])
    entry(
        conn,
        "vLLM tool parser",
        "vllm_parser",
        ["", "hermes", "qwen3_xml", "llama3_json", "mistral"],
    )

    def browse(key):
        file = filedialog.askopenfilename(filetypes=[("JSON manifest", "*.json")])
        if file:
            app.vars[key].set(file)

    for label, k in [
        ("Extra OMP MCP pack", "extra_mcp"),
        ("Custom engine", "engine_manifest"),
    ]:
        f = entry(conn, label, k)
        app.button(f.master, "Browse", lambda k=k: browse(k))
    r = app.row(conn)
    app.button(r, "Save connections", app.save)
    ttk.Label(
        conn,
        text="Native login uses the selected CLI’s own account. Attached endpoints must speak the protocol the harness expects.",
        wraplength=850,
    ).pack(anchor="w", pady=8)

    catalog = app.tree(tools, [("Tool", 205), ("Detected installation", 540)])
    for t in tooling.TOOLS:
        catalog.insert("", "end", iid=t, values=(t, ""))

    def refresh():
        def check():
            return studio.checks(True)

        def show(status):
            for t, path in status.items():
                catalog.item(t, values=(t, path or "Available to install"))
            app.status.set("Tool scan complete")

        app.task("Checking tools", check, show)

    def selected(action):
        selection = catalog.selection()
        if not selection:
            return
        name = selection[0]
        app.task(
            action + " · " + name,
            lambda: tooling.setup(name, action),
            lambda result: app.status.set(result["message"]),
        )

    r = app.row(tools)
    app.button(r, "Scan", refresh)
    for action in ["Install", "Update", "Login", "Docs"]:
        app.button(r, action, lambda action=action: selected(action))

    def view_log():
        file = DATA / "setup.log"
        if not file.exists():
            file.touch()
        import webbrowser

        webbrowser.open(file.as_uri())

    app.button(r, "Setup log", view_log)
    r = app.row(tools)
    app.label(r, "Docker workspace")
    ttk.Entry(r, textvariable=app.vars["box"], width=17).pack(side="left")

    def sandbox(launch):
        s = app.save()
        if launch:
            app.task("Opening isolated OMP", lambda: boxes.agent(s["box"], s))
        else:
            app.task("Stopping Docker workspace", lambda: boxes.stop_box(s["box"]))

    app.button(r, "Open sandbox", lambda: sandbox(True))
    app.button(r, "Stop sandbox", lambda: sandbox(False))
    ttk.Label(
        tools,
        text="Each Docker workspace gets its own volume and a non-root OMP session. Build the Sandbox image once in Setup.",
        wraplength=850,
    ).pack(anchor="w")

    ttk.Label(runs, text="OMP task", font=("Segoe UI Semibold", 14)).pack(anchor="w")
    prompt = tk.Text(
        runs,
        height=3,
        wrap="word",
        bg="#1b2623",
        fg="#e9efea",
        insertbackground="white",
        padx=10,
        pady=8,
    )
    prompt.insert("1.0", "Read the project's README and summarize how to run it.")
    prompt.pack(fill="x", pady=5)
    view = app.tree(runs, [("Elapsed", 75), ("Event", 130), ("Details", 560)])
    events = queue.Queue()
    proc_ref = [None]
    cancel = threading.Event()
    edits = tk.BooleanVar(value=False)

    def show_events():
        for _ in range(100):
            try:
                t, kind, detail = events.get_nowait()
            except queue.Empty:
                break
            item = view.insert("", "end", values=(f"{t:.2f}s", kind, detail[:650]))
            view.see(item)
        app.win.after(100, show_events)

    show_events()

    def begin():
        s = app.save()
        task = prompt.get("1.0", "end-1c").strip()
        if not task:
            raise ValueError("Enter a task.")
        allow_edits = edits.get()
        cancel.clear()
        for i in view.get_children():
            view.delete(i)

        def work():
            argv, env, cwd = connections.prepare(s, "OMP")
            argv = safe_argv(
                argv
                + [
                    "--mode",
                    "json",
                    "--no-session",
                    "--no-extensions",
                    "--max-time",
                    "300",
                    "--tools",
                    "read,glob,grep,edit,write" if allow_edits else "read,glob,grep",
                    "-p",
                    task,
                ]
            )
            folder = DATA / "traces"
            folder.mkdir(exist_ok=True)
            path = folder / (
                datetime.datetime.now().strftime("%Y%m%d-%H%M%S-%f") + ".jsonl"
            )
            t0 = time.monotonic()
            with (
                path.open("w", encoding="utf8") as log,
                path.with_suffix(".stderr").open("w", encoding="utf8") as err,
            ):
                p = subprocess.Popen(
                    argv,
                    cwd=cwd,
                    env=env,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=err,
                    text=True,
                    encoding="utf8",
                    errors="replace",
                    creationflags=QUIET,
                )
                proc_ref[0] = p
                try:
                    for line in p.stdout:
                        if cancel.is_set():
                            break
                        try:
                            e = json.loads(line)
                        except (ValueError, RecursionError):
                            continue
                        if not isinstance(e, dict):
                            continue
                        kind = e.get("type", "")
                        if kind == "tool_execution_start":
                            details = (
                                str(e.get("toolName", ""))
                                + " "
                                + json.dumps(e.get("args", {}), ensure_ascii=False)
                            )
                        elif kind == "tool_execution_end":
                            details = str(e.get("toolName", "")) + (
                                " · failed" if e.get("isError") else " · complete"
                            )
                        elif kind == "message_end":
                            details = studio.said(e.get("message"))
                            if not details:
                                continue
                        elif kind in ("agent_start", "agent_end"):
                            details = s["provider"]
                        else:
                            continue
                        elapsed = round(time.monotonic() - t0, 3)
                        log.write(
                            json.dumps(
                                dict(elapsed_s=elapsed, event=kind, details=details),
                                ensure_ascii=False,
                            )
                            + "\n"
                        )
                        log.flush()
                        events.put((elapsed, kind, details))
                    code = p.wait()
                    if code and not cancel.is_set():
                        raise RuntimeError(
                            "OMP exited with code "
                            + str(code)
                            + ". See "
                            + str(path.with_suffix(".stderr"))
                        )
                finally:
                    if p.poll() is None:
                        p.terminate()
                    proc_ref[0] = None
            return path.name

        app.task(
            "Running traced OMP task",
            work,
            lambda name: app.status.set("Local trace saved · " + name),
        )

    def cancel_run():
        cancel.set()
        if proc_ref[0] and proc_ref[0].poll() is None:
            proc_ref[0].terminate()

    r = app.row(runs)
    app.button(r, "Run trace", begin, go=True)
    app.button(r, "Cancel", cancel_run)
    ttk.Checkbutton(r, text="Allow file edits", variable=edits).pack(
        side="left", padx=8
    )

    def open_traces():
        d = DATA / "traces"
        d.mkdir(exist_ok=True)
        open_folder(d)

    app.button(r, "Open traces", open_traces, side="right")
