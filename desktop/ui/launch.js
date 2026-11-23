// The launch sequence (AW.launch.start, ui.md 9.8): readiness, the folder review, a fresh check of an attached server, the local model, then the agent.
// Beyond ui.md 9.8: AW.launch.on(fn) reports the run in progress as {agent, where, folder, workspace, steps, error} (null when none),
// AW.launch.run() returns it, AW.launch.clear() drops a finished run's error, and AW.launch.focusBlocker is set by the Launch view.
(function () {
  "use strict";
  const AW = window.AW;
  const LOCAL = "Managed local";
  const ATTACHED = "Attached endpoint";
  const listeners = new Set();
  let run = null;
  let flight = null;

  function tell() {
    for (const fn of [...listeners]) {
      try {
        fn(run);
      } catch (thrown) {
        console.error(thrown);
      }
    }
  }

  function nameOf(form) {
    const row = form.connection_id ? (AW.state.accounts || []).find((item) => item.connection_id === form.connection_id) : null;
    return (row && (row.name || row.provider)) || form.provider || "this source";
  }

  function markUnreachable(id, error) {
    const next = { ...(AW.state.unreachable || {}) };
    if (error) next[id] = error;
    else delete next[id];
    AW.store.set("unreachable", next);
  }

  const launchShown = () => AW.router.current().id === "launch" && !document.hidden;

  async function showBlocker() {
    if (AW.router.current().id !== "launch") await AW.router.go("launch", { focus: "launch" });
    await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));
    if (AW.launch.focusBlocker) AW.launch.focusBlocker();
  }

  function step(key, title) {
    return { key, title, state: "waiting", handle: null, started: 0 };
  }

  function begin(target) {
    const where = target.where;
    const opening = where === "dock" ? "Opening " + target.agent + " in the terminal dock" : "Opening " + target.agent + " in a new terminal window";
    run = { ...target, steps: [step("open", opening)], error: null, checking: true };
    tell();
  }

  function finish(ok) {
    if (ok || !run || !run.error) run = null;
    tell();
    return ok;
  }

  async function checkAttached(form) {
    const id = form.connection_id;
    const name = nameOf(form);
    let answer;
    try {
      answer = await AW.request("endpoint-refresh", { connection_id: id });
    } catch (thrown) {
      markUnreachable(id, AW.asError(thrown));
      return false;
    }
    const models = answer && Array.isArray(answer.models) ? answer.models : [];
    const auth = answer && answer.auth;
    if ((auth === "required" || auth === "rejected") && !models.length) {
      markUnreachable(id, {
        message:
          auth === "rejected"
            ? name + " did not accept the saved API key. Save a new key for it in Connections, then launch again."
            : name + " asks for an API key. Save one for it in Connections, then launch again.",
      });
      return false;
    }
    markUnreachable(id, null);
    AW.store.refresh("accounts").catch(() => {});
    return true;
  }

  async function needsLoad(form) {
    const live = AW.state.live || {};
    if (live.label !== "Serving") return true;
    return !(await AW.request("server-match", form));
  }

  async function sequence(opts) {
    const form = { ...(opts.form || AW.form.get()) };
    const where = opts.where === "dock" || opts.where === "window" ? opts.where : AW.form.where();
    const origin = opts.origin || null;
    const agent = form.harness;
    const result = AW.readiness.current();
    const stops = result.items.filter((item) => (item.level === "block" || item.level === "work") && item.id !== "setup.consent");
    if (stops.length || !form.workspace || !agent) {
      await showBlocker();
      return false;
    }
    const folder = AW.project.name(form.workspace);
    begin({ agent, where, folder, workspace: form.workspace, origin });
    let current = null;
    try {
      const review = await AW.request("project-setup-consent", { workspace: form.workspace, harness: agent });
      if (review && review.needed && !review.granted) {
        const allowed = await AW.consent.dialog(form.workspace, agent, { launch: true });
        AW.store.refresh("consent").catch(() => {});
        if (!allowed) return finish(false);
      }
      if (form.provider === ATTACHED && form.connection_id && !(await checkAttached(form))) {
        run = null;
        tell();
        await showBlocker();
        return false;
      }
      if (form.provider === LOCAL && (await needsLoad(form))) {
        current = step("model", "Starting the local model");
        run.steps.unshift(current);
        current.state = "running";
        current.started = Date.now();
        run.checking = false;
        const loading = AW.jobs.run("server-start", form, { title: "Starting the local model", origin, key: "server" });
        const record = AW.jobs.find("server");
        current.handle = record ? record.handle : null;
        tell();
        await loading;
        current.state = "done";
        AW.store.refresh("live").catch(() => {});
      }
      current = run.steps[run.steps.length - 1];
      current.state = "running";
      current.started = Date.now();
      run.checking = false;
      tell();
      if (where === "dock") {
        const id = await AW.terminal.start("agent", { form, origin });
        if (!id) return finish(false);
      } else {
        await AW.jobs.run("launch-start", form, { title: "Opening " + agent + " in a terminal window", origin });
      }
      current.state = "done";
      AW.ui.toast(where === "dock" ? agent + " is running in " + folder + "." : agent + " opened in a new terminal window.", { kind: "ok" });
      for (const key of ["sessions", "folder", "folders"]) AW.store.refresh(key).catch(() => {});
      if (JSON.stringify(AW.form.get()) === JSON.stringify(form)) AW.form.markSaved();
      return finish(true);
    } catch (thrown) {
      const error = AW.asError(thrown);
      if (current) current.state = error.code === "cancelled" ? "cancelled" : "failed";
      if (run) {
        run.error = error;
        run.checking = false;
      }
      tell();
      if (!launchShown())
        AW.ui.toast(error.message, {
          kind: error.code === "cancelled" ? "neutral" : "danger",
          action: { label: "Show", run: () => AW.router.go("launch") },
        });
      return false;
    }
  }

  AW.launch = AW.launch || {};
  AW.launch.start = function (opts = {}) {
    if (!flight)
      flight = sequence(opts || {}).finally(() => {
        flight = null;
      });
    return flight;
  };
  AW.launch.on = function (fn) {
    listeners.add(fn);
    return () => listeners.delete(fn);
  };
  AW.launch.run = () => run;
  AW.launch.busy = () => Boolean(flight);
  AW.launch.clear = function () {
    if (flight || !run) return;
    run = null;
    tell();
  };
})();
