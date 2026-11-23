// Project setup, Instructions tab: what the chosen agent reads in the folder, the pointer fix, and the AGENTS.md editor (ui.md 5.3).
(function () {
  "use strict";
  const AW = window.AW;
  const el = AW.el;
  const P = AW.projectSetup;
  const ticket = AW.ticket();

  let ctx = P.context();
  let parts = null;
  let editor = { open: false, exists: false, baseline: "", loadedFor: "", creating: false, error: null };

  const skeleton = (name) => "# " + (name || "Project") + "\n\nDescribe what this project is, the commands that build and test it, and the conventions to follow.\n";

  function dirty() {
    return Boolean(parts && editor.open && (editor.creating || parts.text.value !== editor.baseline));
  }

  function paintStatus() {
    const text = parts.text.value;
    const lines = P.lines(text);
    parts.status.textContent = (lines === 1 ? "1 line" : lines + " lines") + " · " + AW.fmt.bytes(P.bytes(text)) + (dirty() ? " · not saved" : "");
    for (const button of [parts.discard, parts.review]) {
      if (dirty()) button.removeAttribute("aria-disabled");
      else button.setAttribute("aria-disabled", "true");
    }
  }

  async function load(force = false) {
    if (!ctx.workspace) return;
    if (!force && (dirty() || editor.loadedFor === ctx.workspace)) return;
    const mine = ticket.next();
    const workspace = ctx.workspace;
    editor.loadedFor = workspace;
    P.loading(parts.editorBody, "AGENTS.md");
    try {
      const file = await AW.request("project-setup-read", { workspace, path: "AGENTS.md" });
      if (!ticket.current(mine)) return;
      editor = { ...editor, exists: Boolean(file && file.exists), baseline: (file && file.text) || "", creating: false, error: null };
      editor.open = editor.exists;
      parts.text.value = editor.baseline;
    } catch (thrown) {
      if (!ticket.current(mine)) return;
      editor = { ...editor, exists: false, open: false, error: AW.asError(thrown) };
      editor.loadedFor = "";
    }
    P.loaded(parts.editorBody);
    paint();
  }

  function startCreate() {
    editor = { ...editor, open: true, creating: true, baseline: skeleton(ctx.name) };
    parts.text.value = editor.baseline;
    paint();
    parts.text.focus();
    parts.text.setSelectionRange(parts.text.value.length, parts.text.value.length);
  }

  async function discard() {
    const yes = await AW.ui.confirm({ text: "Discard your edits to AGENTS.md?", ok: "Discard", danger: true });
    if (!yes) return;
    forget();
    await load(true);
  }

  function forget() {
    if (!parts) return;
    if (editor.creating) editor = { ...editor, open: false, creating: false, baseline: "" };
    parts.text.value = editor.baseline;
    paint();
  }

  function review() {
    const text = parts.text.value;
    const workspace = ctx.workspace;
    const harness = ctx.harness;
    return P.review({
      title: "Review changes",
      origin: parts.review,
      host: parts.editorErrors,
      plan: () => AW.request("project-setup-plan", { workspace, harness, agents_md: text }),
      apply: (plan, confirmed) => AW.request("project-setup-apply", { workspace, harness, agents_md: text, plan: plan.id, confirmed }),
    }).then((result) => {
      if (!result) return;
      editor.loadedFor = "";
      editor.baseline = text;
      load(true);
    });
  }

  function createPointer(pointer) {
    const workspace = ctx.workspace;
    const harness = ctx.harness;
    return P.review({
      title: "Create " + pointer.path,
      host: parts.notices,
      plan: () => AW.request("project-setup-plan", { workspace, harness, pointer: true }),
      apply: (plan, confirmed) => AW.request("project-setup-apply", { workspace, harness, pointer: true, plan: plan.id, confirmed }),
    });
  }

  function fileRow(row) {
    const own = row.path === "AGENTS.md";
    const lines = Number(row.lines) || 0;
    const actions = el("div", { class: "project-row-actions" });
    if (P.readable(row.path))
      actions.append(
        P.keyed(
          AW.ui.button("View", { small: true, onClick: () => P.viewFile(row.path) }),
          "view:" + row.path,
        ),
      );
    if (own)
      actions.append(
        P.keyed(
          AW.ui.button("Edit", {
            small: true,
            onClick() {
              if (!editor.open) startCreate();
              parts.editorPanel.scrollIntoView({ block: "nearest" });
              parts.text.focus();
            },
          }),
          "edit:" + row.path,
        ),
      );
    const node = el(
      "div",
      { class: "project-entry" },
      el("span", { class: "project-row-name mono", title: AW.fmt.path(row.abs || row.path), text: AW.fmt.path(row.path) }),
      el("span", { class: "project-row-chip" }, P.chip(row.status)),
      el("span", { class: "project-row-meta", text: lines === 1 ? "1 line" : lines + " lines" }),
      actions,
    );
    if (row.reason) node.append(el("p", { class: "project-row-reason" + (row.status === "read" ? "" : " warn"), text: row.reason }));
    return node;
  }

  function paint() {
    if (!parts) return;
    const data = ctx.resolve;
    const agent = ctx.harness || "The agent";
    parts.readTitle.textContent = "What " + agent + " reads in this folder";
    parts.panel.el.hidden = !ctx.workspace;
    if (ctx.error && !data) {
      P.swap(parts.rows, AW.ui.errorNotice(ctx.error, { retry: () => P.refresh({ fresh: true }) }));
      P.loaded(parts.rows);
      parts.notices.replaceChildren();
      parts.notes.replaceChildren();
      parts.notesWrap.hidden = true;
      parts.editorPanel.hidden = true;
      parts.empty.hidden = true;
      return;
    }
    if (!data) {
      if (ctx.loading) P.loading(parts.rows, "instruction files");
      parts.editorPanel.hidden = true;
      parts.empty.hidden = true;
      return;
    }
    if (ctx.loading) P.loading(parts.rows, "instruction files");
    else P.loaded(parts.rows);
    const rows = Array.isArray(data.instructions) ? data.instructions : [];
    const warnings = (data.warnings || []).filter((text) => P.topic(text) === "instructions");
    const notices = [];
    const pointer = data.pointer || {};
    if (pointer.needed)
      notices.push(
        P.keyed(
          AW.ui.notice("warn", el("div", null, el("strong", { class: "project-notice-title", text: agent + " will not read AGENTS.md in this folder" }), el("p", { text: pointer.reason || "" })), {
            actions: [{ label: "Create " + (pointer.path || "the pointer file") + "…", primary: true, pending: "Preparing…", run: () => createPointer(pointer) }],
          }),
          "pointer",
        ),
      );
    for (const text of warnings) notices.push(AW.ui.notice("warn", text));
    P.swap(parts.notices, notices);
    parts.panel.el.hidden = !rows.length;
    P.swap(parts.rows, rows.map(fileRow));
    const empty = !rows.length && !editor.open;
    parts.empty.hidden = !empty;
    parts.emptyText.textContent = "No instruction file in this folder. " + agent + " starts without project instructions.";
    const loaded = editor.loadedFor === ctx.workspace;
    parts.editorPanel.hidden = !editor.error && !editor.open && (empty || !loaded);
    parts.editorBody.hidden = !editor.open;
    parts.createRow.hidden = editor.open || Boolean(editor.error) || empty;
    P.swap(parts.loadError, editor.error ? AW.ui.errorNotice(editor.error, { retry: () => load(true) }) : null);
    parts.editorTitle.textContent = editor.creating ? "AGENTS.md · new file" : "AGENTS.md";
    parts.notes.replaceChildren(...(data.notes || []).map((text) => el("li", { text })));
    parts.notesWrap.hidden = !(data.notes || []).length;
    paintStatus();
  }

  function mount(panel) {
    const readTitle = el("h2", { class: "panel-title" });
    const rows = el("div", { class: "project-rows" });
    const panelNode = el("section", { class: "panel project-panel", attrs: { "aria-label": "Instruction files" } }, el("div", { class: "panel-head" }, readTitle), rows);
    const notices = el("div", { class: "project-notices" });
    const emptyText = el("p");
    const empty = el(
      "div",
      { class: "empty project-empty-state", hidden: true },
      emptyText,
      el(
        "div",
        { class: "empty-actions" },
        AW.ui.button("Create AGENTS.md", { kind: "primary", onClick: startCreate }),
        AW.ui.button("Apply a starter kit…", { onClick: () => P.go("kit") }),
      ),
    );
    const text = el("textarea", { id: "agents-md", class: "mono project-editor", attrs: { spellcheck: "false", rows: "14", "aria-describedby": "agents-md-status" } });
    const status = el("span", { class: "project-caption", id: "agents-md-status", attrs: { "aria-live": "off" } });
    const discardButton = AW.ui.button("Discard", { onClick: discard });
    const reviewButton = AW.ui.button("Review change…", { kind: "primary", onClick: review });
    const editorErrors = el("div", { class: "project-notices" });
    const editorBody = el(
      "div",
      { class: "project-editor-body" },
      el("label", { class: "sr-only", for: "agents-md", text: "AGENTS.md" }),
      text,
      el("div", { class: "project-editor-foot" }, status, el("div", { class: "actions" }, discardButton, reviewButton)),
    );
    const createRow = el(
      "div",
      { class: "project-create-row", hidden: true },
      el("p", { class: "project-caption", text: "This folder has no AGENTS.md of its own. It is the one instruction file every agent here can read." }),
      AW.ui.button("Create AGENTS.md", { onClick: startCreate }),
    );
    const editorTitle = el("h2", { class: "panel-title", text: "AGENTS.md" });
    const loadError = el("div", { class: "project-notices" });
    const editorPanel = el("section", { class: "panel project-panel", attrs: { "aria-label": "AGENTS.md" } }, el("div", { class: "panel-head" }, editorTitle), loadError, createRow, editorBody, editorErrors);
    const notes = el("ul", { class: "project-notes" });
    const notesWrap = el("div", { class: "project-notes-wrap", hidden: true }, el("h3", { class: "group-label", text: "Notes" }), notes);
    panel.append(notices, panelNode, empty, editorPanel, notesWrap);
    text.addEventListener("input", paintStatus);
    text.addEventListener("keydown", (event) => {
      if (event.key === "Enter" && (AW.platform.mac ? event.metaKey : event.ctrlKey) && dirty()) {
        event.preventDefault();
        reviewButton.click();
      }
    });
    parts = { readTitle, rows, panel: { el: panelNode }, notices, empty, emptyText, text, status, discard: discardButton, review: reviewButton, editorErrors, loadError, editorBody, createRow, editorTitle, editorPanel, notes, notesWrap };
  }

  P.tab({
    id: "instructions",
    label: "Instructions",
    order: 1,
    mount,
    update(next) {
      const moved = next.workspace !== ctx.workspace;
      ctx = next;
      if (!parts) return;
      if (moved) editor = { open: false, exists: false, baseline: "", loadedFor: "", creating: false, error: null };
      paint();
      if (next.workspace && (next.fresh || moved)) load(moved || !dirty());
    },
    dirty,
    discard: forget,
  });
})();
