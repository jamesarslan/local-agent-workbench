// Project setup, Skills tab: the skills the chosen agent loads in the folder, from the resolver (ui.md 5.3).
(function () {
  "use strict";
  const AW = window.AW;
  const el = AW.el;
  const P = AW.projectSetup;

  let ctx = P.context();
  let parts = null;

  function skillRow(row) {
    const node = el(
      "div",
      { class: "project-entry" },
      el("span", { class: "project-row-name", text: row.name || row.path }),
      el("span", { class: "project-row-chip" }, P.chip(row.status)),
      el("span", { class: "project-row-meta" }),
      el("div", { class: "project-row-actions" }, P.readable(row.path) ? P.keyed(AW.ui.button("View", { small: true, onClick: () => P.viewFile(row.path) }), "view:" + row.path) : null),
    );
    if (row.description) node.append(el("p", { class: "project-row-desc", text: row.description }));
    node.append(el("p", { class: "project-row-path mono", title: AW.fmt.path(row.path), text: AW.fmt.path(row.path) }));
    if (row.reason) node.append(el("p", { class: "project-row-reason" + (row.status === "read" || row.status === "duplicate" ? "" : " warn"), text: row.reason }));
    return node;
  }

  function paint() {
    if (!parts) return;
    const data = ctx.resolve;
    const agent = ctx.harness || "this agent";
    parts.title.textContent = "Skills " + agent + " loads here";
    if (ctx.error && !data) {
      P.loaded(parts.rows);
      P.swap(parts.rows, AW.ui.errorNotice(ctx.error, { retry: () => P.refresh({ fresh: true }) }));
      parts.notices.replaceChildren();
      parts.foot.hidden = true;
      return;
    }
    if (!data) {
      if (ctx.loading) P.loading(parts.rows, "skills");
      parts.foot.hidden = true;
      return;
    }
    if (ctx.loading) P.loading(parts.rows, "skills");
    else P.loaded(parts.rows);
    const rows = Array.isArray(data.skills) ? data.skills : [];
    parts.count.textContent = rows.length ? String(rows.length) : "";
    P.swap(parts.notices, (data.warnings || []).filter((text) => P.topic(text) === "skills").map((text) => AW.ui.notice("warn", text)));
    if (!rows.length) {
      P.swap(parts.rows, AW.ui.empty("No skills in this folder for " + agent + ".", [{ label: "Open Starter kit", run: () => P.go("kit") }]));
      parts.foot.hidden = true;
      return;
    }
    P.swap(parts.rows, rows.map(skillRow));
    parts.foot.hidden = false;
  }

  P.tab({
    id: "skills",
    label: "Skills",
    order: 2,
    mount(panel) {
      const title = el("h2", { class: "panel-title" });
      const count = el("span", { class: "panel-count" });
      const rows = el("div", { class: "project-rows" });
      const foot = el(
        "div",
        { class: "project-skills-foot", hidden: true },
        el("p", { class: "project-caption", text: "Skills are added by a starter kit." }),
        AW.ui.button("Open Starter kit", { small: true, onClick: () => P.go("kit") }),
      );
      const notices = el("div", { class: "project-notices" });
      panel.append(notices, el("section", { class: "panel project-panel", attrs: { "aria-label": "Skills" } }, el("div", { class: "panel-head" }, title, count), rows, foot));
      parts = { title, count, rows, foot, notices };
    },
    update(next) {
      ctx = next;
      paint();
    },
  });
})();
