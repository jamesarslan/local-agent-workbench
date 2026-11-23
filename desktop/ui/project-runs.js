// Run summaries: the Run summary dialog (copy as Markdown or JSON, save as a profile) and the Project setup tab with the folder's recent runs.
// AW.runs.open({ workspace, id, origin }) is also used by the terminal dock for the run of an agent tab.
(function () {
  "use strict";
  const AW = window.AW;
  const el = AW.el;
  const P = AW.projectSetup;
  const KIND_WORDS = { window: "own window", embedded: "terminal dock", resume: "resumed" };
  const LIMIT = 30;

  function copy(text, what) {
    return AW.clipboard.write(text).then((copied) => {
      AW.ui.toast(copied ? "Copied the " + what + "." : "The " + what + " could not be copied. Select the text and copy it instead.", copied ? { kind: "ok" } : { kind: "danger" });
    });
  }

  function changeLine(rows) {
    const count = rows.length;
    return (count === 1 ? "1 instruction file or skill has" : count + " instruction files and skills have") + " changed since this run. The summary lists them at the end.";
  }

  function afterSave(workspace) {
    if (!P) return;
    if (P.context().workspace === workspace) P.refresh({ fresh: true }).catch(() => {});
    else P.countProfiles(workspace);
  }

  function open({ workspace, id, origin } = {}) {
    if (!workspace || !id) return Promise.resolve(null);
    const dialog = AW.ui.dialog("run-dialog");
    const body = AW.$("run-dialog-body");
    AW.$("run-dialog-title").textContent = "Run summary";

    async function load() {
      body.replaceChildren(AW.ui.skeleton(5, "the run summary"));
      let summary;
      try {
        summary = await AW.request("run-summary", { workspace, id });
      } catch (thrown) {
        body.replaceChildren(AW.ui.errorNotice(thrown, { retry: load }), el("div", { class: "dialog-actions" }, AW.ui.button("Close", { onClick: () => dialog.close(null) })));
        return;
      }
      render(summary || {});
    }

    function render(summary) {
      const markdown = typeof summary.markdown === "string" ? summary.markdown : "";
      const json = typeof summary.json === "string" ? summary.json : "";
      const drift = Array.isArray(summary.drift) ? summary.drift : [];
      const nodes = [el("p", { class: "project-dialog-intro", text: "What this run used. Copy it to repeat the task later, or save its choices as a profile for this folder." })];
      if (drift.length) nodes.push(AW.ui.notice("warn", changeLine(drift)));
      nodes.push(el("pre", { class: "run-summary", tabindex: 0, attrs: { "aria-label": "Run summary" }, text: markdown }));
      const name = el("input", { type: "text", value: summary.name || "", attrs: { maxlength: "80", autocomplete: "off" } });
      const field = AW.ui.field("Save as a profile named", name);
      const errors = el("div", { class: "project-notices" });
      const save = AW.ui.button("Save as profile", {
        pending: "Saving…",
        async onClick() {
          field.setError(null);
          AW.ui.clearError(errors);
          let saved;
          try {
            saved = await AW.request("run-save-profile", { workspace, id, name: name.value });
          } catch (thrown) {
            AW.ui.showError(errors, thrown);
            return;
          }
          afterSave(workspace);
          AW.ui.toast("Saved the profile " + saved.name + ".", {
            kind: "ok",
            action: {
              label: "Open Project profiles",
              run() {
                dialog.close(null);
                return AW.router.go("project", { tab: "profiles" });
              },
            },
          });
        },
      });
      name.addEventListener("keydown", (event) => {
        if (event.key === "Enter") {
          event.preventDefault();
          save.click();
        }
      });
      nodes.push(
        el("div", { class: "project-save-profile" }, field.el, save),
        el("p", { class: "project-caption run-save-note", text: "The profile goes into .agent-workbench/profiles.json. Review and apply it in Project profiles." }),
        errors,
      );
      nodes.push(
        el(
          "div",
          { class: "dialog-actions" },
          AW.ui.button("Close", { onClick: () => dialog.close(null) }),
          AW.ui.button("Copy as JSON", { onClick: () => copy(json, "run as JSON") }),
          AW.ui.button("Copy summary", { kind: "primary", onClick: () => copy(markdown, "run summary") }),
        ),
      );
      body.replaceChildren(...nodes);
      AW.focus.first(body);
    }

    dialog.open();
    load();
    return dialog.opened().then(() => origin && origin.isConnected && origin.focus());
  }

  AW.runs = { open };

  if (!P) return;

  let ctx = P.context();
  let parts = null;
  let rows = null;
  let error = null;
  const ticket = AW.ticket();

  function meta(row) {
    const when = AW.fmt.ago(row.started);
    return [row.source, KIND_WORDS[row.kind] || "", when].filter(Boolean).join(" · ");
  }

  function runRow(row) {
    const title = [row.harness, row.model].filter(Boolean).join(" · ") || "Agent run";
    const view = AW.ui.button("Summary…", { small: true, onClick: () => open({ workspace: ctx.workspace, id: row.id, origin: view }) });
    const forget = AW.ui.button("Remove", { small: true, onClick: () => remove(row) });
    return el(
      "div",
      { class: "project-profile run-row", data: { id: row.id } },
      el("div", { class: "run-row-text" }, el("span", { class: "project-profile-name", text: title }), el("span", { class: "project-caption", text: meta(row) })),
      el("div", { class: "project-row-actions" }, P.keyed(view, "summary:" + row.id), P.keyed(forget, "remove:" + row.id)),
    );
  }

  function paint() {
    if (!parts) return;
    const host = parts.list;
    if (error && !rows) {
      P.loaded(host);
      P.swap(host, AW.ui.errorNotice(error, { retry: () => load() }));
      parts.clear.hidden = true;
      return;
    }
    if (!rows) {
      P.loading(host, "recent runs");
      return;
    }
    P.loaded(host);
    parts.count.textContent = rows.length ? String(rows.length) : "";
    parts.clear.hidden = !rows.length;
    if (!rows.length) {
      P.swap(host, AW.ui.empty("No runs in this folder yet. Each agent you start from Workbench adds one here."));
      return;
    }
    P.swap(host, rows.map(runRow));
  }

  async function load() {
    if (!ctx.workspace) return;
    const mine = ticket.next();
    error = null;
    paint();
    try {
      const value = await AW.request("run-list", { workspace: ctx.workspace, limit: LIMIT });
      if (!ticket.current(mine)) return;
      rows = Array.isArray(value) ? value : [];
      P.setCount("runs", rows.length || null);
    } catch (thrown) {
      if (!ticket.current(mine)) return;
      error = AW.asError(thrown);
    }
    paint();
  }

  async function remove(row) {
    try {
      await AW.request("run-forget", { workspace: ctx.workspace, id: row.id });
    } catch (thrown) {
      AW.ui.showError(parts.errors, thrown);
      return;
    }
    await load();
    AW.focus.first(parts.panel);
  }

  async function clearAll() {
    const yes = await AW.ui.confirm({ text: "Remove every run summary kept for " + (ctx.name || "this folder") + "?", ok: "Clear runs", danger: true });
    if (!yes) return;
    try {
      await AW.request("run-clear", { workspace: ctx.workspace });
    } catch (thrown) {
      AW.ui.showError(parts.errors, thrown);
      return;
    }
    AW.ui.toast("Cleared the runs for " + (ctx.name || "this folder") + ".", { kind: "ok" });
    await load();
  }

  P.tab({
    id: "runs",
    label: "Recent runs",
    order: 6,
    mount(panel) {
      const count = el("span", { class: "panel-count" });
      const list = el("div", { class: "project-profiles", id: "run-list" });
      const errors = el("div", { class: "project-notices" });
      const clear = AW.ui.button("Clear runs…", { small: true, onClick: clearAll });
      clear.hidden = true;
      const section = el(
        "section",
        { class: "panel project-panel", attrs: { "aria-label": "Recent runs" } },
        el("div", { class: "panel-head" }, el("h2", { class: "panel-title", text: "Recent runs" }), count, el("div", { class: "panel-actions" }, clear)),
        el("p", { class: "project-caption", text: "The model, agent, instruction files and skills each launch used. Open a summary to copy it or save it as a profile." }),
        errors,
        list,
      );
      panel.append(section);
      parts = { panel: section, count, list, errors, clear };
    },
    update(next) {
      const moved = next.workspace !== ctx.workspace;
      ctx = next;
      if (!parts || !ctx.workspace) return;
      if (moved) rows = null;
      paint();
      if (next.fresh || moved) load();
    },
  });
})();
