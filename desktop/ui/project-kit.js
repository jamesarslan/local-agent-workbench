// Project setup, Starter kit tab: the kit choice with the folder's suggestion, the shared kit preview and "Review and write" (ui.md 5.3).
(function () {
  "use strict";
  const AW = window.AW;
  const el = AW.el;
  const P = AW.projectSetup;

  let ctx = P.context();
  let parts = null;
  let kits = null;
  let kitsError = null;
  let detected = null;
  let detectedFor = "";
  let chosen = "";
  let preview = null;
  let plan = null;
  let result = null;
  const detectTicket = AW.ticket();

  const sentence = (text) => {
    const value = String(text || "").trim();
    if (!value) return "";
    return value.charAt(0).toUpperCase() + value.slice(1) + (/[.!?]$/.test(value) ? "" : ".");
  };

  function applied() {
    const folder = AW.state.folder;
    return folder && folder.path === ctx.workspace ? folder.kit || "" : "";
  }

  function kitLabel(id) {
    const kit = (kits || []).find((row) => row.id === id);
    return kit ? kit.label : id;
  }

  function writes(current) {
    return current && Array.isArray(current.items) ? current.items.filter((item) => item.action && item.action !== "skip").length : 0;
  }

  function paintButton() {
    const n = writes(plan);
    const label = plan ? (n === 1 ? "Review and write 1 file…" : "Review and write " + n + " files…") : "Review and write files…";
    parts.write.querySelector(".btn-label").textContent = label;
    if (plan && n > 0 && chosen) parts.write.removeAttribute("aria-disabled");
    else parts.write.setAttribute("aria-disabled", "true");
    parts.write.parentNode.hidden = Boolean(plan) && n === 0;
  }

  function onPlan(next) {
    plan = next || null;
    if (parts) paintButton();
  }

  function mountPreview() {
    if (!parts || !chosen || !ctx.workspace) return;
    const options = { workspace: ctx.workspace, harness: ctx.harness, kit: chosen, links: true, onPlan };
    if (preview) {
      plan = null;
      paintButton();
      preview.update(options);
      return;
    }
    preview = AW.kitPreview.mount(parts.preview, options);
  }

  function selection(current) {
    const skills = (current.skills || []).filter((row) => row.selected).map((row) => row.name);
    const servers = (current.servers || []).filter((row) => row.selected).map((row) => (row.builtin ? "builtin:" + row.builtin : row.name));
    return { skills, servers };
  }

  async function review() {
    const current = (preview && preview.plan && preview.plan()) || plan;
    if (!current || !writes(current)) return;
    const kit = chosen;
    const request = current.request || { workspace: ctx.workspace, kit, harness: ctx.harness, ...selection(current) };
    let first = true;
    parts.result.replaceChildren();
    const done = await P.review({
      title: "Write the " + kitLabel(kit) + " kit",
      host: parts.errors,
      plan() {
        if (first) {
          first = false;
          return Promise.resolve(current);
        }
        return AW.request("kit-preview", request);
      },
      apply: (fresh, confirmed) => AW.request("kit-apply", { ...request, plan: fresh.id, confirmed }),
    });
    if (!done) return;
    result = done;
    parts.result.replaceChildren(P.resultNotice(done) || el("span"));
    AW.store.refresh("folder").catch(() => {});
    mountPreview();
  }

  function paintChoices() {
    const here = applied();
    const suggested = detected && detected.suggested && detected.suggested !== "blank" ? detected.suggested : "";
    if (!chosen || !(kits || []).some((kit) => kit.id === chosen)) chosen = here || suggested || ((kits || []).some((kit) => kit.id === "general") ? "general" : (kits && kits[0] && kits[0].id) || "");
    const cards = (kits || []).map((kit) => {
      const badges = el("span", { class: "project-kit-badges" });
      if (kit.id === here) badges.append(AW.ui.chip("applied here", "ok"));
      if (kit.id === suggested) badges.append(AW.ui.chip("suggested", "accent"));
      const card = AW.ui.choice({ name: "project-kit", value: kit.id, title: kit.label, description: kit.summary, badge: badges });
      card.input.checked = kit.id === chosen;
      P.keyed(card.input, "kit:" + kit.id);
      card.input.addEventListener("change", () => {
        if (!card.input.checked) return;
        chosen = kit.id;
        result = null;
        parts.result.replaceChildren();
        mountPreview();
      });
      return card.el;
    });
    P.swap(parts.choices, cards);
    let line = "";
    if (detected) {
      if (suggested) line = "Suggested for this folder: " + kitLabel(suggested) + ". " + ((detected.evidence && detected.evidence[suggested]) || []).map(sentence).join(" ");
      else line = "No kit stood out for this folder. General software is a safe start.";
      if (detected.truncated) line += " Only part of this large folder was scanned.";
    }
    parts.suggestion.textContent = line.trim();
    parts.suggestion.hidden = !line;
  }

  function paint() {
    if (!parts) return;
    if (kitsError && !kits) {
      P.loaded(parts.choices);
      P.swap(parts.choices, AW.ui.errorNotice(kitsError, { retry: () => loadKits() }));
      parts.body.hidden = true;
      return;
    }
    if (!kits) {
      P.loading(parts.choices, "starter kits");
      parts.body.hidden = true;
      return;
    }
    P.loaded(parts.choices);
    if (!kits.length) {
      P.swap(parts.choices, AW.ui.empty("No starter kits are installed with this copy of Agent Workbench. Reinstall Agent Workbench to restore them."));
      parts.body.hidden = true;
      return;
    }
    paintChoices();
    parts.body.hidden = !chosen;
    paintButton();
  }

  async function loadKits() {
    kitsError = null;
    paint();
    try {
      const list = await P.kits();
      kits = Array.isArray(list) ? list : [];
    } catch (thrown) {
      kitsError = AW.asError(thrown);
    }
    paint();
    mountPreview();
  }

  async function detect() {
    if (!ctx.workspace || detectedFor === ctx.workspace) return;
    const workspace = ctx.workspace;
    const mine = detectTicket.next();
    detectedFor = workspace;
    try {
      const found = await AW.request("kit-detect", { workspace });
      if (!detectTicket.current(mine)) return;
      detected = found;
    } catch (thrown) {
      if (!detectTicket.current(mine)) return;
      detected = null;
      detectedFor = "";
      AW.ui.showError(parts.errors, thrown, { retry: () => detect() });
    }
    const before = chosen;
    if (!applied() && detected && detected.suggested && detected.suggested !== "blank" && !result) chosen = detected.suggested;
    paint();
    if (chosen !== before || !preview) mountPreview();
  }

  P.tab({
    id: "kit",
    label: "Starter kit",
    order: 4,
    mount(panel) {
      const suggestion = el("p", { class: "project-kit-suggestion", hidden: true });
      const choices = el("div", { class: "project-kit-choices", role: "radiogroup", attrs: { "aria-label": "Starter kit" } });
      const previewHost = el("div", { class: "project-kit-preview" });
      const write = AW.ui.button("Review and write files…", { kind: "primary", onClick: review });
      write.setAttribute("aria-disabled", "true");
      const errors = el("div", { class: "project-notices" });
      const resultHost = el("div", { class: "project-notices" });
      const body = el(
        "div",
        { class: "project-kit-body", hidden: true },
        previewHost,
        errors,
        el("div", { class: "project-kit-foot" }, write),
        resultHost,
      );
      panel.append(
        el(
          "section",
          { class: "panel project-panel", attrs: { "aria-label": "Starter kits" } },
          el("div", { class: "panel-head" }, el("h2", { class: "panel-title", text: "Starter kits" })),
          el("p", { class: "project-caption", text: "A starter kit adds an AGENTS.md instruction file, a few skills and a set of tools that suit the project. You see every file before it is written, and files you already have are kept." }),
          suggestion,
          choices,
          body,
        ),
      );
      parts = { suggestion, choices, preview: previewHost, write, errors, result: resultHost, body };
      AW.store.on(["folder"], () => parts && kits && paintChoices());
      loadKits();
    },
    update(next) {
      const moved = next.workspace !== ctx.workspace;
      const agentMoved = next.harness !== ctx.harness;
      ctx = next;
      if (!parts || !ctx.workspace) return;
      if (moved) {
        detected = null;
        detectedFor = "";
        chosen = "";
        result = null;
        parts.result.replaceChildren();
      }
      paint();
      detect();
      if ((moved || agentMoved) && kits) mountPreview();
    },
  });
})();
