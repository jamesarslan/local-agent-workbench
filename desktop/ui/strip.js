// Status strip: the segments, the Local model and Sessions popovers and the keyboard shortcuts dialog (ui.md 2.4, 5.9, 5.13).
(function () {
  "use strict";
  const AW = (window.AW = window.AW || {});
  const el = AW.el;
  const SESSION_KINDS = ["embedded", "native", "remote"];
  let ticker = null;

  const sessions = () => (AW.state.sessions || []).filter((row) => SESSION_KINDS.includes(row.kind));
  const running = () => AW.jobs.list().filter((record) => record.state === "running");
  const say = (thrown) => AW.ui.toast(AW.asError(thrown).message, { kind: AW.asError(thrown).code === "cancelled" ? "neutral" : "danger" });

  function modelView() {
    const live = AW.state.live;
    if (!live) return AW.store.error("live") || AW.store.error("config") ? { mark: "warn", word: "Status unavailable", text: "Status unavailable" } : { mark: "idle", word: "Checking…", text: "Checking…" };
    const file = AW.fmt.folder(live.model);
    const context = AW.fmt.context(live.context);
    if (AW.store.error("live")) return { mark: "warn", word: "Status unavailable", text: "Status unavailable" };
    if (live.label === "Serving") return { mark: "ok", word: "Serving", text: ["Serving", file, context && context + " context"].filter(Boolean).join(" · ") };
    if (live.label === "Starting") {
      const seconds = live.started ? Math.max(0, Math.round(Date.now() / 1000 - live.started)) : 0;
      return { mark: "busy", word: "Loading…", text: "Loading… " + seconds + " s", loading: true };
    }
    if (live.label === "Port in use") return { mark: "down", word: "Port " + live.port + " in use", text: "Port " + live.port + " in use" };
    if (live.label === "Unexpected model") return { mark: "warn", word: "A different model is serving", text: ["A different model is serving", file].filter(Boolean).join(" · ") };
    if (live.label === "Stopped") return { mark: "idle", word: "Stopped", text: "Stopped" };
    return { mark: "busy", word: String(live.label || "Checking…"), text: String(live.label || "Checking…") };
  }

  function usingOther() {
    if (!AW.state.config || !AW.form.get().workspace) return "";
    const provider = AW.form.provider();
    if (provider === "Managed local") return "";
    const connection = AW.form.connection();
    return " · using " + (connection ? connection.name || connection.provider : provider === "Native login" ? "an agent account" : provider);
  }

  function setText(node, text) {
    if (node.textContent !== text) node.textContent = text;
  }

  function paintModel() {
    const seg = AW.$("strip-model");
    const view = modelView();
    AW.ui.setMark(seg.querySelector(".mark"), view.mark);
    setText(seg.querySelector(".seg-text"), "Local model: " + view.text + usingOther());
    tick(Boolean(view.loading) || running().length > 0);
  }

  function paintSessions() {
    const n = sessions().length;
    setText(AW.$("strip-sessions").querySelector(".seg-text"), n === 0 ? "No sessions" : n === 1 ? "1 session" : n + " sessions");
  }

  function paintTasks() {
    const seg = AW.$("strip-tasks");
    const tasks = running();
    const bar = seg.querySelector("progress");
    seg.hidden = tasks.length === 0;
    if (!tasks.length) return tick(Boolean(modelView().loading));
    let text = tasks.length + " tasks";
    bar.hidden = true;
    if (tasks.length === 1) {
      const progress = tasks[0].progress || {};
      const done = Number(progress.done);
      const total = Number(progress.total);
      if (Number.isFinite(done) && total > 0) {
        const percent = Math.max(0, Math.min(100, Math.round((done / total) * 100)));
        text = tasks[0].title + " " + percent + "%";
        bar.hidden = false;
        bar.value = percent;
      } else text = tasks[0].title + " " + AW.fmt.duration((Date.now() - tasks[0].started) / 1000);
    }
    setText(seg.querySelector(".seg-text"), text);
    tick(true);
  }

  function paintService() {
    const seg = AW.$("strip-service");
    const bridge = AW.state.bridge;
    const state = bridge ? bridge.state : "ready";
    seg.hidden = state === "ready" || state === "starting";
    if (seg.hidden) return;
    AW.ui.setMark(seg.querySelector(".mark"), state === "restarting" ? "busy" : "down");
    setText(seg.querySelector(".seg-text"), state === "restarting" ? "Service restarting…" : "Service stopped");
  }

  function tick(on) {
    if (on && !ticker)
      ticker = setInterval(() => {
        paintModel();
        paintTasks();
      }, 1000);
    else if (!on && ticker) {
      clearInterval(ticker);
      ticker = null;
    }
  }

  function startModel(origin) {
    return AW.jobs.run("server-start", AW.form.get(), { title: "Starting the local model", origin, key: "server" }).then(() => AW.store.refresh("live"));
  }
  async function stopModel(origin) {
    const live = AW.state.live || {};
    const using = (live.sessions || []).length;
    const question = using ? "Stop the local model? " + (using === 1 ? "1 session is" : using + " sessions are") + " using it." : "Stop the local model?";
    if (!(await AW.ui.confirm({ text: question, ok: "Stop", danger: true }))) return false;
    await AW.jobs.run("server-stop", {}, { title: "Stopping the local model", origin, key: "server-stop" });
    await AW.store.refresh("live");
    return true;
  }
  async function cancelLoad() {
    const job = AW.jobs.find("server");
    if (job) await AW.jobs.cancel(job.handle);
    else await AW.jobs.run("server-stop", {}, { title: "Stopping the local model", key: "server-stop" });
    return AW.store.refresh("live");
  }
  async function copyKey() {
    await AW.request("server-copy-key");
    AW.ui.toast("Copied.");
  }
  const modelChosen = () => {
    const config = AW.state.config || {};
    return Boolean(AW.form.get().preset || (config.current && config.current.model));
  };

  function memoryLine(hardware) {
    const kind = hardware.memory_kind === "unified" ? "Unified memory" : hardware.memory_kind === "vram" ? "VRAM" : "System memory";
    const total = Number(hardware.memory_gib);
    const free = hardware.free_gib === null || hardware.free_gib === undefined ? null : Number(hardware.free_gib);
    if (!Number.isFinite(total) || total <= 0) return { label: kind, value: "unknown", used: null, total: null };
    if (free === null || !Number.isFinite(free)) return { label: kind, value: "in use: unknown · " + total.toFixed(1) + " GiB", used: null, total };
    const used = Math.max(0, total - free);
    return { label: kind + " in use", value: used.toFixed(1) + " / " + total.toFixed(1) + " GiB", used, total };
  }

  function openModel() {
    const anchor = AW.$("strip-model");
    let off = [];
    const pop = AW.ui.popover(
      anchor,
      (root, close) => {
        const mark = AW.ui.mark("idle");
        const state = el("strong", { id: "server-state" });
        const file = el("div", { class: "model-file", id: "running-model" });
        const ctx = el("dd", { id: "running-ctx" });
        const port = el("dd", { id: "port" });
        const note = el("p", { class: "model-note", id: "server-note", hidden: true });
        const meter = el("div", { class: "meter" }, AW.ui.skeleton(1, "memory"));
        const errors = el("div", { class: "stack" });
        const start = AW.ui.button("Start", { id: "serve", kind: "primary", small: true, errorHost: errors, onClick: () => startModel(start) });
        start.setAttribute("aria-describedby", "server-note");
        const stop = AW.ui.button("Stop", { id: "stop", small: true, errorHost: errors, onClick: () => stopModel(stop) });
        const cancel = AW.ui.button("Cancel", { small: true, pending: "Cancelling…", errorHost: errors, onClick: cancelLoad });
        const adopt = AW.ui.button("Use it", { small: true, kind: "primary", errorHost: errors, onClick: () => AW.fix.run("server.adopt") });
        const attach = AW.ui.button("Attach the server on this port", { small: true, errorHost: errors, onClick: () => AW.fix.run("source.probe", (AW.state.live || {}).port, { origin: attach }) });
        attach.dataset.fix = "source.probe";
        const copy = AW.ui.button("Copy key", { small: true, errorHost: errors, onClick: copyKey });
        const connections = AW.ui.button("Open in Connections", {
          small: true,
          onClick() {
            close(false);
            return AW.router.go("accounts", { select: "Managed local" });
          },
        });
        root.append(
          el("div", { class: "popover-head" }, el("h2", { class: "popover-title", text: "Local model" })),
          el(
            "div",
            { class: "popover-body" },
            el("div", { class: "model-state" }, mark, state),
            file,
            el("dl", { class: "kv model-note" }, el("dt", { text: "Context" }), ctx, el("dt", { text: "API port" }), port),
            note,
            meter,
            errors,
            el("div", { class: "popover-actions" }, start, cancel, stop, adopt, attach, copy, connections),
          ),
        );
        const paint = () => {
          const live = AW.state.live || {};
          const view = modelView();
          AW.ui.setMark(mark, view.mark);
          setText(state, view.word === "Loading…" ? view.text : view.word);
          setText(file, AW.fmt.folder(live.model));
          setText(ctx, AW.fmt.context(live.context) || "—");
          setText(port, live.port ? String(live.port) : "—");
          const label = live.label;
          const loading = label === "Starting" || Boolean(AW.jobs.find("server"));
          const serving = label === "Serving" || label === "Unexpected model";
          start.hidden = loading || serving || label === "Port in use";
          cancel.hidden = !loading;
          stop.hidden = loading || !serving;
          adopt.hidden = label !== "Unexpected model";
          attach.hidden = label !== "Port in use";
          if (!attach.hidden) {
            attach.querySelector(".btn-label").textContent = "Attach the server on port " + live.port;
            if (AW.fix.has("source.probe")) attach.removeAttribute("aria-disabled");
            else {
              attach.setAttribute("aria-disabled", "true");
              attach.title = "Not available yet.";
            }
          }
          let text = "";
          if (label === "Port in use") text = "Port " + live.port + " is used by another program, so the local model cannot start.";
          else if (label === "Unexpected model") text = "A different local model is serving than the one this folder selects.";
          else if (!start.hidden && !modelChosen()) text = "Choose a model in Advanced → Model library first.";
          if (!start.hidden && !modelChosen()) start.setAttribute("aria-disabled", "true");
          else if (!start.dataset.pending && !AW.jobs.find("server")) start.removeAttribute("aria-disabled");
          note.hidden = !text;
          setText(note, text);
        };
        const paintMemory = () => {
          const hardware = AW.state.hardware;
          const error = AW.store.error("hardware");
          if (!hardware && !error) return;
          if (!hardware) {
            meter.replaceChildren(AW.ui.errorNotice(error, { retry: () => AW.store.refresh("hardware").catch(() => {}) }));
            return;
          }
          const line = memoryLine(hardware);
          const bar = el("progress", { attrs: { max: "100", "aria-label": line.label } });
          bar.value = line.used === null ? 0 : Math.round((line.used / line.total) * 100);
          bar.hidden = line.used === null;
          meter.replaceChildren(el("div", { class: "meter-line" }, el("span", { text: line.label }), el("span", { text: line.value })), bar);
          meter.classList.toggle("stale", Boolean(error));
        };
        off = [AW.store.on(["live", "config"], paint), AW.form.on(paint), AW.jobs.on(paint), AW.store.on("hardware", paintMemory)];
        paint();
        paintMemory();
        AW.store.refresh("hardware").catch(paintMemory);
      },
      { placement: "above", label: "Local model" },
    );
    pop.onClose = () => off.forEach((fn) => fn());
  }

  const REMOTE_WORD = { attached: "attached", detached: "detached", unverified: "not checked" };
  function sessionMeta(row) {
    const age = row.started ? AW.fmt.duration(Date.now() / 1000 - row.started) : "";
    if (row.kind === "embedded") return ["Terminal dock", age].filter(Boolean).join(" · ");
    if (row.kind === "native") return ["Terminal window", age].filter(Boolean).join(" · ");
    return ["SSH, " + (REMOTE_WORD[row.state] || row.state || "not checked"), age].filter(Boolean).join(" · ");
  }
  const groupOf = (row) => (row.kind === "remote" ? (row.host || "Remote machine") + " (SSH)" : AW.fmt.folder(row.workspace) || "No folder");

  async function showSession(row) {
    if (row.workspace && row.workspace !== AW.project.current()) await AW.project.select(row.workspace);
    AW.terminal.show(row.terminal);
  }
  async function endSession(row) {
    if (row.kind === "remote") {
      if (!(await AW.ui.confirm({ text: "End the session on " + row.host + "? The agent running in it is stopped.", ok: "End", danger: true }))) return;
      await AW.request("sessions-end", { host: row.host, name: row.name });
    } else {
      if (!(await AW.ui.confirm({ text: "End this terminal session?", ok: "End", danger: true }))) return;
      await AW.request("term-close", { workspace: row.workspace, id: row.terminal });
    }
    await AW.store.refresh("sessions");
  }

  function openSessions() {
    const anchor = AW.$("strip-sessions");
    let off = () => {};
    const checked = new Map();
    const pop = AW.ui.popover(
      anchor,
      (root, close) => {
        const title = el("h2", { class: "popover-title" });
        const body = el("div", { class: "popover-body" });
        const groups = new Map();
        const rowsBy = new Map();
        function build(row) {
          const note = el("div", { class: "session-note" });
          const actions = [];
          if (row.kind === "embedded")
            actions.push(
              AW.ui.button("Show", {
                small: true,
                onClick: () =>
                  showSession(row).then(() => close(false)),
              }),
            );
          if (row.kind === "remote")
            actions.push(
              AW.ui.button("Check", {
                small: true,
                pending: "Checking…",
                onClick: () =>
                  AW.request("sessions-remote", { host: row.host }).then(
                    (list) => {
                      const found = (list || []).find((item) => item.name === row.name);
                      checked.set(row.id, found ? found.state : "ended");
                      note.textContent = found ? "" : "This session has ended on " + row.host + ".";
                      paint();
                    },
                    (thrown) => {
                      note.textContent = AW.asError(thrown).message;
                    },
                  ),
              }),
            );
          if (row.kind !== "native")
            actions.push(
              AW.ui.button("End…", {
                small: true,
                onClick: () =>
                  endSession(row).catch((thrown) => {
                    note.textContent = AW.asError(thrown).message;
                  }),
              }),
            );
          else note.textContent = "Close its window to end it.";
          const part = AW.ui.row({ mark: "ok", title: row.title || row.harness || "Session", meta: sessionMeta(row), actions });
          part.el.classList.add("session-row");
          part.el.append(note);
          return part;
        }
        function paint() {
          const list = sessions();
          title.textContent = list.length ? "Sessions · " + list.length : "Sessions";
          const seen = new Set();
          for (const row of list) {
            seen.add(row.id);
            const name = groupOf(row);
            if (!groups.has(name)) {
              const head = el("h3", { class: "group-label popover-group", text: name });
              const host = el("div", { class: "rows" });
              groups.set(name, { head, host });
              body.append(head, host);
            }
            let part = rowsBy.get(row.id);
            if (!part) {
              part = build(row);
              part.group = name;
              rowsBy.set(row.id, part);
              groups.get(name).host.append(part.el);
            }
            const state = checked.get(row.id) || row.state;
            part.update({ mark: row.kind === "remote" && state !== "attached" ? "busy" : "ok", title: row.title || row.harness || "Session", meta: sessionMeta({ ...row, state }) });
          }
          for (const [id, part] of rowsBy)
            if (!seen.has(id)) {
              part.el.remove();
              rowsBy.delete(id);
            }
          for (const [name, group] of groups)
            if (!group.host.children.length) {
              group.head.remove();
              group.host.remove();
              groups.delete(name);
            }
          let empty = body.querySelector(":scope > .session-empty");
          if (!list.length && !empty) body.append(el("p", { class: "muted session-empty", text: "Nothing is running." }));
          else if (list.length && empty) empty.remove();
        }
        root.append(el("div", { class: "popover-head" }, title), body);
        off = AW.store.on("sessions", paint);
        paint();
        AW.store.refresh("sessions").catch((thrown) => AW.ui.showError(body, thrown, { retry: () => AW.store.refresh("sessions").catch(() => {}) }));
      },
      { placement: "above", label: "Sessions" },
    );
    pop.onClose = () => off();
  }

  const kbd = (...names) => names.flatMap((name, index) => (index ? [" ", el("kbd", { text: name })] : [el("kbd", { text: name })]));
  function keysBody() {
    const mod = AW.platform.mod;
    const list = el("dl", { class: "keys-list" });
    const row = (label, ...keys) => list.append(el("dt", { text: label }), el("dd", null, keys));
    row("Next region / previous region", kbd("F6"), " / ", kbd("Shift", "F6"));
    row("Launch · Overview · Project setup · Connections · Machines · Advanced", kbd(mod, "1"), " … ", kbd(mod, "6"));
    row("Project switcher", kbd(mod, "O"));
    row("Go to Launch", kbd(mod, "L"));
    row("Launch with the current choices", kbd(mod, "Enter"));
    row("Show or hide the terminal", kbd(mod, "J"));
    row("Tasks", kbd(mod, "."));
    if (!AW.$("command-open").hidden) row("Search or run a command", kbd(mod, "K"));
    if (AW.platform.mac) row("Back to the previous view", kbd("Option", "←"), " or ", kbd("Cmd", "["));
    else row("Back to the previous view", kbd("Alt", "←"));
    row("Close a dialog, popover or menu", kbd("Esc"));
    list.append(el("h3", { class: "group-label keys-group", text: "Inside the terminal" }));
    if (AW.platform.mac) {
      row("Copy a selection · Paste", kbd("Cmd", "C"), " · ", kbd("Cmd", "V"));
      row("Leave the terminal", kbd("F6"));
      list.append(el("p", { class: "hint keys-group", text: "Cmd shortcuts keep working inside the terminal." }));
    } else {
      row("Copy a selection", kbd("Ctrl", "C"));
      row("Paste", kbd("Ctrl", "Shift", "V"));
      row("Leave the terminal", kbd("F6"), " / ", kbd("Shift", "F6"));
      row("Hide the terminal", kbd("Ctrl", "Shift", "J"));
      if (!AW.$("command-open").hidden) row("Search or run a command", kbd("Ctrl", "Shift", "K"));
      list.append(el("p", { class: "hint keys-group", text: "Every other Ctrl key goes to the shell." }));
    }
    return list;
  }
  function openKeys() {
    AW.$("keys-dialog-body").replaceChildren(keysBody());
    AW.ui.dialog("keys-dialog").open();
  }

  AW.strip = { openModel, openSessions, openKeys };

  AW.shell.register(() => {
    AW.$("strip-model").addEventListener("click", openModel);
    AW.$("strip-sessions").addEventListener("click", openSessions);
    AW.$("strip-keys").addEventListener("click", openKeys);
    AW.store.on(["live", "config", "accounts", "folder"], paintModel);
    AW.form.on(paintModel);
    AW.store.on("sessions", paintSessions);
    AW.store.on("bridge", paintService);
    AW.jobs.on(paintTasks);
    paintModel();
    paintSessions();
    paintTasks();
    paintService();
  });

  AW.fix.register("server.adopt", async () => {
    await AW.request("server-adopt", AW.form.get());
    await AW.store.hydrate();
    AW.ui.toast("This folder now uses the running local model.", { kind: "ok" });
  });
  AW.fix.register("server.swap", async (arg, ctx) => {
    await AW.jobs.run("server-stop", {}, { title: "Stopping the local model", origin: ctx && ctx.origin, key: "server-stop" });
    return startModel(ctx && ctx.origin);
  });

  const label = () => (AW.state.live || {}).label;
  AW.commands.register({ id: "model.start", title: "Start local model", group: "Actions", when: () => label() === "Stopped" && modelChosen(), run: () => startModel(null).catch(say) });
  AW.commands.register({ id: "model.stop", title: "Stop local model", group: "Actions", when: () => label() === "Serving" || label() === "Unexpected model" || label() === "Starting", run: () => stopModel(null).catch(say) });
  AW.commands.register({ id: "model.key", title: "Copy local API key", group: "Actions", run: () => copyKey().catch(say) });
  AW.commands.register({ id: "keys.show", title: "Keyboard shortcuts", group: "Actions", run: openKeys });
  AW.commands.provide(() =>
    sessions().flatMap((row) => {
      const title = row.title || row.harness || "Session";
      const out = [];
      const where = groupOf(row);
      const named = title === where || title.startsWith(where + " · ") ? title : title + " · " + where;
      if (row.kind === "embedded") out.push({ id: "session.show:" + row.id, title: "Show " + named, group: "Sessions", run: () => showSession(row).catch(say) });
      if (row.kind !== "native") out.push({ id: "session.end:" + row.id, title: "End " + title, group: "Sessions", run: () => endSession(row).catch(say) });
      return out;
    }),
  );
})();
