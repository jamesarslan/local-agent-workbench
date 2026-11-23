// Saved sessions: the recent-session rows used on Launch, Overview and in the Resume or watch dialog, and resuming one (ui.md 5.12, 9.7).
// Beyond ui.md 9.7: recentList also takes harness (a string or a function), onStarted(id) and empty (the empty-state sentence);
// refresh(options) merges new options; AW.sessions.when(row) formats a row's time.
(function () {
  "use strict";
  const AW = window.AW;
  const el = AW.el;
  const NATIVE = "Native login";
  const LOCAL = "Managed local";
  const MISSING = "This feature is not included in this build of Agent Workbench. Update Agent Workbench, then try again.";
  const STORE_WORD = { account: "Agent account", workbench: "Workbench source" };
  const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  const text = (value) => (typeof value === "string" ? value.trim() : "");

  function clean(rows) {
    return (Array.isArray(rows) ? rows : [])
      .filter((row) => row && typeof row === "object" && typeof row.id === "string" && row.id)
      .map((row) => ({
        id: row.id,
        harness: text(row.harness),
        title: text(row.title),
        date: text(row.date),
        updated: text(row.updated),
        store: text(row.store),
        path: text(row.path),
        command: text(row.command),
      }));
  }

  async function recent(workspace, { harness, limit = 20 } = {}) {
    const size = Math.max(1, Math.min(80, Number(limit) || 20));
    try {
      return clean(await AW.request("sessions-recent", harness ? { workspace, harness, limit: size } : { workspace, limit: size }));
    } catch (thrown) {
      const error = AW.asError(thrown);
      if (error.message !== MISSING) throw error;
      const rows = await AW.request("term-history", { workspace, harness: harness || AW.form.get().harness || "OMP" });
      return clean(rows)
        .slice(0, size)
        .map((row) => ({ ...row, path: "" }));
    }
  }

  function when(row, now = new Date()) {
    const at = new Date(Date.parse(row.updated) || Date.parse(row.date) || NaN);
    if (Number.isNaN(at.getTime())) return row.date || "";
    const day = (d) => new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
    const days = Math.round((day(now) - day(at)) / 86400000);
    if (days <= 0) return "today " + String(at.getHours()).padStart(2, "0") + ":" + String(at.getMinutes()).padStart(2, "0");
    if (days === 1) return "yesterday";
    return at.getDate() + " " + MONTHS[at.getMonth()] + (at.getFullYear() === now.getFullYear() ? "" : " " + at.getFullYear());
  }

  let serial = 0;
  const watchable = (row) => row.harness === "OMP" && Boolean(row.path);

  function remembered() {
    const folder = AW.state.folder;
    const recipe = folder && folder.known && folder.recipe ? folder.recipe : null;
    if (!recipe) return null;
    if (recipe.connection_id) {
      const row = (AW.state.accounts || []).find((account) => account.connection_id === recipe.connection_id);
      if (!row || row.provider === NATIVE) return null;
      return {
        name: row.name || row.provider,
        patch: {
          provider: row.provider,
          connection_id: row.connection_id,
          remote_model: recipe.remote_model || row.remote_model || "",
          auth_provider: recipe.auth_provider || row.auth_provider || "",
        },
      };
    }
    if (recipe.provider === LOCAL) return { name: "Local model", patch: { provider: LOCAL, connection_id: "", preset: recipe.preset || "" } };
    return null;
  }

  function mismatch(row) {
    const provider = AW.form.provider();
    const agent = row.harness || "the agent";
    if (row.store === "account" && provider !== NATIVE)
      return {
        text: "This session used " + agent + "'s own account.",
        fix: { label: "Resume with Agent account", patch: { provider: NATIVE, connection_id: "", remote_model: "", auth_provider: "" } },
      };
    if (row.store === "workbench" && provider === NATIVE) {
      const source = remembered();
      return { text: "This session used a Workbench model source.", fix: source ? { label: "Resume with " + source.name, patch: source.patch } : null };
    }
    return null;
  }

  async function reviewed(workspace, harness) {
    const status = await AW.request("project-setup-consent", { workspace, harness });
    if (!status || !status.needed || status.granted) return true;
    return Boolean(await AW.consent.dialog(workspace, harness, { launch: true }));
  }

  async function resume(row, patch) {
    const form = { ...AW.form.get(), harness: row.harness || AW.form.get().harness };
    if (patch) Object.assign(form, patch);
    if (!(await reviewed(form.workspace, form.harness))) return null;
    return AW.terminal.start("resume", { saved: row.id, form });
  }

  function watch(row) {
    return AW.terminal.start("watch", { saved: row.id });
  }

  function recentList(root, options = {}) {
    let opts = { limit: 3, mode: "resume", ...options };
    const ticket = AW.ticket();
    let destroyed = false;
    let busy = false;

    const value = (option) => (typeof option === "function" ? option() : option);

    function rowFor(row) {
      const mode = opts.mode === "watch" ? "watch" : "resume";
      const agent = row.harness || "Agent";
      const errors = el("div", { class: "term-session-errors" });
      const off = mode === "watch" && !watchable(row);
      const problem = mode === "resume" ? mismatch(row) : null;
      const verb = mode === "watch" ? "Watch" : "Resume";
      const noteId = "term-session-note-" + ++serial;

      function run(button, work, label) {
        if (busy || button.getAttribute("aria-disabled") === "true") return;
        busy = true;
        AW.ui.clearError(errors);
        const done = Promise.resolve().then(work);
        AW.ui.pending(button, label, done);
        done.then(
          (id) => {
            busy = false;
            if (id && opts.onStarted) opts.onStarted(id);
          },
          (thrown) => {
            busy = false;
            AW.ui.showError(errors, thrown);
          },
        );
      }

      const primary = el(
        "button",
        { class: "term-session-main", type: "button", title: [row.title || "Untitled session", agent, when(row)].join(" · ") },
        el("span", { class: "sr-only", text: verb + " " }),
        el("span", { class: "term-session-title", text: row.title || "Untitled session" }),
        el("span", { class: "term-session-meta", text: " · " + agent + (when(row) ? " · " + when(row) : "") }),
      );
      if (off || (problem && !problem.fix)) {
        primary.setAttribute("aria-disabled", "true");
        primary.setAttribute("aria-describedby", noteId);
      }
      primary.addEventListener("click", () => {
        if (mode === "watch") run(primary, () => watch(row), "Opening…");
        else run(primary, () => resume(row, problem && problem.fix ? problem.fix.patch : null), "Opening…");
      });

      const foot = el("div", { class: "term-session-foot" }, el("span", { class: "term-session-store", text: STORE_WORD[row.store] || "Saved session" }));
      if (mode === "resume" && watchable(row)) {
        const button = AW.ui.button("Watch read-only", { small: true });
        button.addEventListener("click", () => run(button, () => watch(row), "Opening…"));
        foot.append(button);
      }
      if (row.command)
        foot.append(
          AW.ui.button("Copy the resume command", {
            small: true,
            onClick: () =>
              AW.clipboard.write(row.command).then((copied) => AW.ui.toast(copied ? "Copied." : "The command could not be copied.", copied ? {} : { kind: "danger" })),
          }),
        );

      const parts = [primary, foot];
      if (off) parts.push(el("p", { class: "term-session-note", id: noteId, text: "Watching is available for OMP sessions." }));
      else if (problem) {
        const note = el("p", { class: "term-session-note", id: noteId }, el("span", { text: problem.text }));
        if (problem.fix) {
          const button = AW.ui.button(problem.fix.label, { small: true });
          button.addEventListener("click", () => run(button, () => resume(row, problem.fix.patch), "Opening…"));
          note.append(" ", button);
        }
        parts.push(note);
      }
      parts.push(errors);
      return el("div", { class: "term-session" + (off ? " off" : ""), role: "listitem", data: { id: row.id } }, ...parts);
    }

    function failedNotice(error) {
      const notice = AW.ui.errorNotice(error, { retry: refresh });
      const retried = [...notice.querySelectorAll("button")].some((button) => button.textContent.trim() === "Try again");
      if (retried) return notice;
      const own = AW.ui.notice(error.code === "timeout" ? "warn" : "danger", error.message, {
        actions: [{ label: "Try again", primary: true, run: () => refresh() }],
        detail: error.detail,
      });
      own.dataset.error = error.code || "refused";
      return own;
    }

    async function refresh(next) {
      if (next && typeof next === "object" && !(next instanceof Event)) opts = { ...opts, ...next };
      if (destroyed) return [];
      const mine = ticket.next();
      const workspace = value(opts.workspace) || "";
      const harness = value(opts.harness) || "";
      if (!workspace) {
        root.removeAttribute("aria-busy");
        root.replaceChildren(AW.ui.empty("Choose a project folder to see its saved sessions."));
        return [];
      }
      root.setAttribute("aria-busy", "true");
      root.replaceChildren(el("p", { class: "term-sessions-loading", role: "status", text: "Reading saved sessions…" }));
      let rows;
      try {
        rows = await recent(workspace, { harness, limit: opts.limit });
      } catch (thrown) {
        if (destroyed || !ticket.current(mine)) return [];
        const error = AW.asError(thrown);
        root.removeAttribute("aria-busy");
        root.replaceChildren(failedNotice(error));
        AW.ui.announce(error.message, { assertive: true });
        return [];
      }
      if (destroyed || !ticket.current(mine)) return rows;
      root.removeAttribute("aria-busy");
      if (!rows.length) root.replaceChildren(AW.ui.empty(opts.empty || "No saved sessions in this folder."));
      else root.replaceChildren(el("div", { class: "term-sessions", role: "list" }, ...rows.map(rowFor)));
      return rows;
    }

    refresh();
    return {
      refresh,
      destroy() {
        destroyed = true;
        ticket.next();
        root.removeAttribute("aria-busy");
        root.replaceChildren();
      },
    };
  }

  let pickerList = null;
  async function picker(mode = "resume") {
    const kind = mode === "watch" ? "watch" : "resume";
    const dialog = AW.ui.dialog("session-picker");
    const workspace = AW.form.get().workspace || "";
    const name = AW.project.name(workspace) || "this folder";
    AW.$("session-picker-title").textContent = (kind === "watch" ? "Watch a session in " : "Resume a session in ") + name;
    const select = AW.$("session-agent");
    const agents = (AW.state.config && AW.state.config.agents) || [];
    const keep = select.value;
    select.replaceChildren(el("option", { value: "", text: "All agents" }), ...agents.map((agent) => el("option", { value: agent, text: agent })));
    select.value = agents.includes(keep) ? keep : "";
    if (pickerList) pickerList.destroy();
    let started = null;
    const list = recentList(AW.$("saved-sessions"), {
      workspace,
      limit: 40,
      mode: kind,
      harness: () => select.value,
      onStarted(id) {
        started = id;
        dialog.close(id);
      },
    });
    pickerList = list;
    const node = dialog.el;
    const closed = new Promise((resolve) => {
      const onClose = () => {
        if (node.open) return;
        node.removeEventListener("close", onClose);
        resolve();
      };
      node.addEventListener("close", onClose);
    });
    dialog.open();
    await closed;
    if (pickerList === list) {
      list.destroy();
      pickerList = null;
    }
    return started;
  }

  AW.sessions = {
    recent,
    recentList,
    picker,
    resume(row, { provider, connection_id } = {}) {
      if (!provider) return resume(row, null);
      const match = connection_id ? (AW.state.accounts || []).find((account) => account.connection_id === connection_id) : null;
      return resume(row, {
        provider,
        connection_id: connection_id || "",
        remote_model: provider === NATIVE ? "" : (match && match.remote_model) || AW.form.get().remote_model,
        auth_provider: provider === NATIVE ? "" : (match && match.auth_provider) || "",
      });
    },
    when,
  };

  AW.shell.register(() => {
    AW.$("session-agent").addEventListener("change", () => {
      if (pickerList) pickerList.refresh();
    });
  });

  AW.commands.register({ id: "sessions.resume", title: "Resume a session…", group: "Actions", keywords: "continue history saved", when: () => Boolean(AW.form.get().workspace), run: () => picker("resume") });
  AW.commands.register({ id: "sessions.watch", title: "Watch a session…", group: "Actions", keywords: "read-only follow transcript", when: () => Boolean(AW.form.get().workspace), run: () => picker("watch") });
})();
