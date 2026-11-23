// Review changes dialog (ui.md 5.3): every file a plan would write, a tick per file, its change, then the write.
// AW.planReview.open({ title, plan, apply(confirmed, plan) -> Promise<result>, replan() -> Promise<plan> }) -> Promise<result | null>
//   apply receives the plan on screen, which is a fresh one after the folder changed under the review: send its id.
//   plan is a plan object (project-setup-plan, project-setup-export-plan, kit-preview) or a promise of one; without it replan() supplies it.
//   The promise resolves with the apply result once files were written, or null when the dialog closes before that.
// AW.planReview.resultNotice(result) -> the notice that lists written, skipped and failed paths with their reasons.
(function () {
  "use strict";
  const AW = window.AW;
  const el = AW.el;
  const ui = AW.ui;
  const ID = "plan-review";
  const files = (n) => n + (n === 1 ? " file" : " files");
  const rows = (value) => (Array.isArray(value) ? value : []);
  const items = (plan) => rows(plan && plan.items).filter((item) => item && typeof item === "object");
  const writes = (plan) => items(plan).filter((item) => item.action !== "skip");
  const text = (value) => (typeof value === "string" ? value : "");
  const KIND_REASONS = {
    instructions: () => "Project instructions in the portable AGENTS.md form.",
    pointer: (agent) => agent + " reads AGENTS.md through this file.",
    skill: () => "A skill: instructions an agent loads when a task calls for them.",
    mcp: () => "The project's MCP servers. Secrets are never written here.",
  };

  function pathList(title, entries, kind) {
    const list = el("ul", { class: "stack", attrs: { "aria-label": title } });
    list.style.gap = "var(--space-2)";
    for (const entry of entries) {
      const path = typeof entry === "string" ? entry : text(entry && entry.path);
      const reason = typeof entry === "string" ? "" : text(entry && entry.reason);
      list.append(el("li", { class: "wrap" }, el("span", { class: "plan-path", text: AW.fmt.path(path) || "(no file)" }), reason ? el("span", { class: kind === "danger" ? "" : "muted", text: "  " + reason }) : null));
    }
    const part = el("div", { class: "stack" }, el("p", { class: "group-label", text: title }), list);
    part.style.gap = "var(--space-3)";
    return part;
  }

  function resultNotice(result) {
    const written = rows(result && result.written).filter((path) => typeof path === "string");
    const skipped = rows(result && result.skipped);
    const failed = rows(result && result.failed);
    const kind = failed.length ? "danger" : written.length ? "ok" : "warn";
    const body = el(
      "div",
      { class: "stack" },
      el("p", { text: written.length ? "Wrote " + files(written.length) + "." : "No files were written." }),
      written.length ? pathList("Written", written) : null,
      skipped.length ? pathList("Not written", skipped) : null,
      failed.length ? pathList("Could not be written", failed, "danger") : null,
    );
    return ui.notice(kind, body);
  }

  function changeText(item) {
    const diff = text(item.diff);
    if (diff) return diff;
    const content = text(item.content);
    if (!content) return "";
    return content.replace(/\n$/, "").split("\n").map((line) => "+" + line).join("\n");
  }

  let session = null;
  let dialog = null;
  const closer = () => AW.$(ID).querySelector(".dialog-head [data-close]");

  function handle() {
    if (dialog) return dialog;
    dialog = ui.dialog(ID, {
      onClose() {
        const ended = session;
        session = null;
        if (ended) ended.resolve(ended.result);
      },
    });
    dialog.el.addEventListener("cancel", (event) => {
      if (session && session.busy) event.preventDefault();
    });
    return dialog;
  }

  function open({ title, plan, apply, replan } = {}) {
    const dialog = handle();
    if (session) {
      const ended = session;
      session = null;
      ended.resolve(ended.result);
    }
    const body = AW.$(ID + "-body");
    const heading = AW.$(ID + "-title");
    heading.textContent = title || "Review changes";
    const head = closer();
    if (head) head.disabled = false;

    let resolve;
    const answer = new Promise((done) => (resolve = done));
    const state = { resolve, result: null, busy: false };
    session = state;
    const live = () => session === state;

    let current = null;
    let table = null;
    const lede = el("p", { class: "muted wrap" });
    const warnings = el("div", { class: "stack" });
    const tableHost = el("div");
    const diffLabel = el("p", { class: "group-label wrap", id: ID + "-diff-label" });
    const diffHost = el("div");
    const diffPart = el("div", { class: "stack", hidden: true }, diffLabel, diffHost);
    diffPart.style.gap = "var(--space-3)";
    const errors = el("div", { class: "stack", hidden: true });
    const showError = (thrown, options) => {
      errors.hidden = false;
      ui.showError(errors, thrown, options);
    };
    const clearError = () => {
      ui.clearError(errors);
      errors.hidden = true;
    };
    const content = el("div", { class: "stack" }, lede, warnings, tableHost, diffPart, errors);
    const cancel = ui.button("Cancel", { onClick: () => dialog.close(null) });
    const write = ui.button("Write files", { kind: "primary", onClick: () => commit() });
    const again = ui.button("Try again", { kind: "primary", onClick: () => load(Promise.resolve().then(replan)) });
    again.hidden = true;
    body.replaceChildren(content, el("div", { class: "dialog-actions" }, cancel, again, write));

    function count() {
      const n = table ? table.confirmed().length : 0;
      if (!write.dataset.pending) write.querySelector(".btn-label").textContent = table ? "Write " + files(n) : "Write files";
      if (n && !state.busy) write.removeAttribute("aria-disabled");
      else write.setAttribute("aria-disabled", "true");
      return n;
    }

    function showChange(item) {
      const path = text(item.path);
      const change = changeText(item);
      diffPart.hidden = false;
      diffLabel.textContent = path ? "Changes in " + AW.fmt.path(path) : "Details";
      if (change) {
        const pane = ui.diff(change);
        pane.removeAttribute("aria-label");
        pane.setAttribute("aria-labelledby", diffLabel.id);
        diffHost.replaceChildren(pane);
      } else diffHost.replaceChildren(el("p", { class: "muted wrap", text: text(item.reason) || "There is no change to show for this file." }));
    }

    function reasonOf(item, harness) {
      const reason = text(item.reason);
      if (reason || item.action === "skip") return reason;
      return KIND_REASONS[item.kind] ? KIND_REASONS[item.kind](harness || "The agent") : "";
    }

    function render(next) {
      current = next;
      const where = text(next.workspace);
      const list = items(next);
      const n = writes(next).length;
      const at = where ? " in " + AW.fmt.path(where) : "";
      if (!list.length) lede.textContent = "There is nothing to write" + at + ".";
      else if (!n) lede.textContent = "Nothing needs to be written" + at + ". Every file below stays as it is.";
      else lede.textContent = "Workbench will write these files" + at + ". Untick any you do not want.";
      warnings.replaceChildren(...rows(next.warnings).filter((line) => typeof line === "string" && line).map((line) => ui.notice("warn", line)));
      warnings.hidden = !warnings.firstChild;
      write.hidden = false;
      again.hidden = true;
      table = ui.planTable(list.map((item) => ({ ...item, reason: reasonOf(item, text(next.harness)) })), { onToggle: count, onSelect: showChange });
      tableHost.hidden = false;
      for (const box of table.el.querySelectorAll("input[disabled]")) box.remove();
      tableHost.replaceChildren(table.el);
      const first = list.find((item) => item.action !== "skip" && changeText(item)) || list.find((item) => changeText(item));
      if (first) {
        const row = table.el.tBodies[0].rows[list.indexOf(first)];
        if (row) row.setAttribute("aria-selected", "true");
        showChange(first);
      } else {
        diffPart.hidden = true;
        diffHost.replaceChildren();
      }
      count();
    }

    function load(source) {
      table = null;
      lede.textContent = "Preparing the changes…";
      warnings.replaceChildren();
      warnings.hidden = true;
      tableHost.hidden = false;
      again.hidden = true;
      write.hidden = false;
      tableHost.replaceChildren(ui.skeleton(3, "the changes"));
      diffPart.hidden = true;
      clearError();
      count();
      return Promise.resolve(source)
        .then((next) => {
          if (!next || typeof next !== "object") throw { message: "Workbench could not prepare these changes. Try again." };
          if (live()) render(next);
        })
        .catch((thrown) => {
          if (!live()) return;
          lede.textContent = "Workbench could not prepare the changes. Close this and check the folder, or try again.";
          tableHost.replaceChildren();
          tableHost.hidden = true;
          write.hidden = true;
          again.hidden = !replan;
          showError(thrown);
        });
    }

    function hold(on) {
      state.busy = on;
      const head = closer();
      if (head) head.disabled = on;
      if (on) cancel.setAttribute("aria-disabled", "true");
      else cancel.removeAttribute("aria-disabled");
    }

    function finish(value) {
      state.result = value && typeof value === "object" ? value : { written: [], skipped: [], failed: [] };
      const workspace = text(current.workspace);
      if (workspace && workspace === AW.project.current())
        for (const key of ["resolve", "consent", "folder"]) AW.store.refresh(key, { force: true }).catch(() => {});
      const written = rows(state.result.written).length;
      heading.textContent = written ? "Changes written" : "Nothing was written";
      const done = ui.button("Done", { kind: "primary", onClick: () => dialog.close(state.result) });
      body.replaceChildren(el("div", { class: "stack" }, resultNotice(state.result)), el("div", { class: "dialog-actions" }, done));
      ui.announce(written ? "Wrote " + files(written) + "." : "No files were written.");
      done.focus();
    }

    async function commit() {
      if (state.busy || !current || !table) return;
      const confirmed = table.confirmed();
      if (!confirmed.length) return;
      clearError();
      hold(true);
      const asked = Promise.resolve().then(() => apply(confirmed, current));
      ui.pending(write, "Writing…", asked).catch(() => {});
      let value;
      try {
        value = await asked;
      } catch (thrown) {
        const error = AW.asError(thrown);
        const fresh = replan && error.code !== "cancelled" && error.code !== "busy" ? await Promise.resolve().then(replan).catch(() => null) : null;
        hold(false);
        if (!live()) return;
        if (fresh && typeof fresh === "object" && fresh.id !== current.id) {
          render(fresh);
          warnings.prepend(ui.notice("warn", error.message));
          warnings.hidden = false;
          ui.announce(error.message, { assertive: true });
          AW.focus.first(tableHost);
        } else {
          count();
          showError(error, { retry: () => commit() });
        }
        return;
      }
      hold(false);
      if (live()) finish(value);
    }

    dialog.open();
    load(plan || (replan ? Promise.resolve().then(replan) : null));
    return answer;
  }

  AW.planReview = { open, resultNotice };
})();
