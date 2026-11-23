// Advanced, Trace: run one OMP task with the current Launch choices and follow what it does, or read a recorded trace (ui.md 5.6).
(function () {
  "use strict";
  const AW = window.AW;
  const el = AW.el;
  const ui = AW.ui;
  const POLL_MS = 600;
  const PAGE = 100;
  const EVENTS = {
    target: "Target",
    agent_start: "Agent started",
    tool_execution_start: "Tool call",
    tool_execution_end: "Tool result",
    message_end: "Message",
    agent_end: "Agent finished",
    exit: "Exit code",
  };
  const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  const memory = { task: "Read the project README and summarize it.", edit: false, active: "", running: "" };

  function traceLabel(id) {
    const hit = /^(\d{4})(\d{2})(\d{2})-(\d{2})(\d{2})(\d{2})/.exec(id);
    if (!hit) return id;
    const [, year, month, day, hour, minute, second] = hit;
    return Number(day) + " " + (MONTHS[Number(month) - 1] || month) + " " + year + ", " + hour + ":" + minute + ":" + second;
  }

  function modelOf(form) {
    if (form.provider === "Managed local") {
      if (form.preset) return form.preset;
      const config = AW.state.config || {};
      const file = config.current && config.current.model ? AW.fmt.folder(config.current.model) : "";
      return file ? file + " on the local model" : "the local model";
    }
    const row = AW.form.connection();
    const where = row && row.name ? row.name : form.provider;
    if (form.provider === "Native login") return "its own sign-in";
    return form.remote_model ? form.remote_model + " from " + where : where + " and its default model";
  }

  AW.advanced.section("Trace", async (body, ctx) => {
    const info = await AW.advanced.info();
    if (!ctx.alive()) return;
    const checks = (info && info.checks) || {};
    const names = Array.isArray(info && info.trace_names) ? info.trace_names.slice() : [];

    const target = el("p", { class: "adv-target", id: "adv-trace-target" });
    const paintTarget = () => {
      const form = AW.form.get();
      target.textContent = form.workspace ? "Runs OMP in " + AW.fmt.folder(form.workspace) + " with " + modelOf(form) + "." : "Choose a project folder first. The trace runs OMP in the folder chosen in Launch.";
      target.title = AW.fmt.path(form.workspace);
    };
    paintTarget();
    ctx.cleanup(AW.form.on(paintTarget));

    const task = el("textarea", { id: "adv-trace-task", value: memory.task, attrs: { rows: 3, spellcheck: "true" } });
    task.addEventListener("input", () => (memory.task = task.value));
    const taskField = ui.field("Task for OMP", task, { hint: "OMP works on this task once, without a session, and stops after 5 minutes." });
    const edits = ui.toggle("Allow file edits", { id: "adv-trace-edit", description: "Lets OMP edit and write files in the folder. Off, it can only read and search them.", checked: memory.edit, onChange: (on) => (memory.edit = on) });
    const runErrors = el("div", { class: "adv-errors" });

    const select = el("select", { id: "adv-trace-list" });
    const status = el("p", { class: "hint adv-caption", attrs: { role: "status" } });
    const log = el("div", { class: "adv-log", role: "log", tabindex: 0, attrs: { "aria-label": "Trace log" } });
    const logErrors = el("div", { class: "adv-errors" });
    let timer = null;
    const reading = AW.ticket();
    ctx.cleanup(() => clearTimeout(timer));

    function paintList() {
      select.replaceChildren();
      if (!names.length) {
        select.append(el("option", { value: "", text: "No recorded traces yet" }));
        select.disabled = true;
        return;
      }
      select.disabled = false;
      for (const id of names) select.append(el("option", { value: id, text: traceLabel(id) + (id === memory.running ? " (running)" : "") }));
      select.value = memory.active && names.includes(memory.active) ? memory.active : names[0];
    }

    function row(item) {
      const event = String(item.event ?? "");
      const seconds = Number(item.elapsed_s);
      return el(
        "div",
        { class: "adv-log-row", data: { event } },
        el("span", { class: "adv-log-time", text: Number.isFinite(seconds) ? seconds.toFixed(1) + " s" : "" }),
        el("span", { class: "adv-log-event", text: EVENTS[event] || event }),
        el("span", { class: "adv-log-detail", text: String(item.details ?? "") }),
      );
    }

    function paintRunning(running) {
      cancel.hidden = !running;
    }

    async function read(id, offset, mine) {
      if (!ctx.alive() || !reading.current(mine)) return;
      let data;
      try {
        data = await AW.request("studio-trace-read", { id, offset });
      } catch (thrown) {
        if (!ctx.alive() || !reading.current(mine)) return;
        log.removeAttribute("aria-busy");
        status.textContent = "";
        ui.showError(logErrors, thrown, { retry: () => show(id) });
        return;
      }
      if (!ctx.alive() || !reading.current(mine)) return;
      log.removeAttribute("aria-busy");
      const rows = Array.isArray(data && data.rows) ? data.rows : [];
      const atEnd = log.scrollHeight - log.scrollTop - log.clientHeight < 24;
      if (rows.length) {
        log.querySelector(":scope > .empty")?.remove();
        log.append(...rows.map(row));
        if (atEnd) log.scrollTop = log.scrollHeight;
      }
      const count = log.querySelectorAll(".adv-log-row").length;
      const running = Boolean(data && data.running);
      if (running && memory.running !== id) {
        memory.running = id;
        paintList();
      } else if (!running && memory.running === id) {
        memory.running = "";
        paintList();
      }
      if (id === memory.active) paintRunning(running);
      if (!count) {
        if (!log.querySelector(":scope > .empty")) log.append(ui.empty(running ? "OMP is starting. Its steps appear here." : "This trace recorded no steps."));
      }
      status.textContent = running ? "Running · " + count + (count === 1 ? " step" : " steps") : count + (count === 1 ? " step recorded." : " steps recorded.");
      const next = Number(data && data.offset) || offset;
      if (rows.length === PAGE) read(id, next, mine);
      else if (running && !document.hidden) timer = setTimeout(() => read(id, next, mine), POLL_MS);
      else if (running) {
        const wake = () => {
          if (document.hidden) return;
          document.removeEventListener("visibilitychange", wake);
          read(id, next, mine);
        };
        document.addEventListener("visibilitychange", wake);
        ctx.cleanup(() => document.removeEventListener("visibilitychange", wake));
      }
    }

    function show(id) {
      clearTimeout(timer);
      const mine = reading.next();
      ui.clearError(logErrors);
      log.replaceChildren();
      memory.active = id || "";
      if (!id) {
        status.textContent = "";
        log.append(ui.empty("Run a trace to see each step OMP takes: the tools it calls and what it answers."));
        paintRunning(false);
        return;
      }
      log.setAttribute("aria-busy", "true");
      status.textContent = "Reading " + traceLabel(id) + "…";
      paintRunning(memory.running === id);
      read(id, 0, mine);
    }

    select.addEventListener("change", () => show(select.value));

    const runButton = ui.button("Run trace", {
      kind: "primary",
      async onClick() {
        ui.clearError(runErrors);
        if (!task.value.trim()) {
          taskField.setError("Enter a task for OMP.");
          task.focus();
          return;
        }
        taskField.setError(null);
        const form = AW.form.get();
        if (!form.workspace) {
          ui.showError(runErrors, { message: "Choose a project folder in Launch, then run the trace again." });
          return;
        }
        let id;
        try {
          id = await AW.jobs.run("studio-trace", { task: task.value, edit: edits.input.checked, form }, { title: "Starting a trace", origin: runButton, key: "trace" });
        } catch (thrown) {
          if (ctx.alive()) ui.showError(runErrors, thrown, { retry: () => runButton.click() });
          return;
        }
        if (typeof id !== "string" || !id) return;
        memory.running = id;
        if (!names.includes(id)) names.unshift(id);
        AW.advanced.info({ force: true }).catch(() => {});
        if (!ctx.alive()) return;
        paintList();
        select.value = id;
        show(id);
        ui.announce("Trace started.");
      },
    });
    const cancel = ui.button("Cancel trace", {
      pending: "Cancelling…",
      async onClick() {
        const id = memory.active;
        if (!id) return;
        try {
          await AW.request("studio-trace-stop", { id });
        } catch (thrown) {
          ui.showError(runErrors, thrown);
          return;
        }
        ui.announce("Cancelling the trace. OMP stops in a moment.");
      },
    });
    cancel.hidden = true;
    taskField.el.addEventListener("input", () => taskField.setError(null));

    const runPanel = ui.panel("Trace");
    if (!checks.OMP)
      runPanel.body.append(
        ui.notice("warn", "OMP is not installed on this computer, so a trace cannot run.", {
          actions: [{ label: "Open Advanced → Setup", run: () => AW.router.go("advanced", { section: "Setup", tool: "OMP" }) }],
        }),
      );
    runPanel.body.append(target, taskField.el, edits.el, el("div", { class: "actions adv-save" }, runButton, cancel), runErrors);

    const logPanel = ui.panel("Recorded traces");
    logPanel.body.append(ui.field("Trace", select, { hint: "Traces are kept in the traces folder of Agent Workbench’s data." }).el, status, logErrors, log);

    body.replaceChildren(runPanel.el, logPanel.el);
    paintList();
    show(names.length ? select.value : "");
  });
})();
