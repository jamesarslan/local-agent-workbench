// Advanced, Agent settings: OMP's prompt supplement, reasoning effort, extra MCP manifest and CSV skill, the local API key and Firecrawl (ui.md 5.6).
(function () {
  "use strict";
  const AW = window.AW;
  const el = AW.el;
  const ui = AW.ui;
  const EFFORT = [
    ["off", "Off"],
    ["minimal", "Minimal"],
    ["low", "Low"],
    ["medium", "Medium"],
    ["high", "High"],
    ["xhigh", "Extra high"],
    ["max", "Maximum"],
  ];

  function settingsPanel(config, ctx) {
    const prompt = el("textarea", { id: "adv-prompt", value: String(config.prompt ?? ""), attrs: { rows: 6, spellcheck: "true" } });
    const effort = el("select", { id: "adv-thinking" });
    const choices = EFFORT.some(([value]) => value === config.thinking) || !config.thinking ? EFFORT : [...EFFORT, [String(config.thinking), String(config.thinking)]];
    for (const [value, label] of choices) effort.append(el("option", { value, text: label }));
    effort.value = config.thinking || "off";
    const manifest = el("input", { type: "text", id: "adv-extra-mcp", class: "mono", value: String(config.extra_mcp ?? ""), attrs: { autocomplete: "off", spellcheck: "false", placeholder: "/path/to/servers.json" } });
    const csv = ui.toggle("Enable CSV / TSV skill in OMP", { id: "adv-document-skill", description: "Gives OMP a skill for reading and editing CSV and TSV files.", checked: Boolean(config.document_skill) });

    const read = () => ({ prompt: prompt.value, thinking: effort.value, extra_mcp: manifest.value.trim(), document_skill: csv.input.checked });
    let saved = JSON.stringify(read());
    const status = el("p", { class: "hint adv-status", attrs: { role: "status" } });
    const errors = el("div", { class: "adv-errors" });
    const changed = () => JSON.stringify(read()) !== saved;
    const paint = () => {
      status.textContent = changed() ? "You have unsaved changes." : "";
    };
    for (const node of [prompt, effort, manifest, csv.input]) node.addEventListener(node === effort || node === csv.input ? "change" : "input", paint);
    ctx.guard(changed);

    const save = ui.button("Save settings", {
      kind: "primary",
      pending: "Saving…",
      async onClick() {
        ui.clearError(errors);
        const sent = read();
        try {
          await AW.request("studio-save", sent);
        } catch (thrown) {
          ui.showError(errors, thrown, { retry: () => save.click() });
          return;
        }
        saved = JSON.stringify(sent);
        manifest.value = sent.extra_mcp;
        paint();
        ui.toast("Agent settings saved.", { kind: "ok" });
        AW.advanced.info({ force: true }).catch(() => {});
        AW.store.refresh("config").catch(() => {});
      },
    });

    const panel = ui.panel("OMP");
    panel.body.append(
      ui.field("OMP prompt supplement", prompt, { hint: "Added to OMP’s system prompt in every session Workbench starts, and in the sandbox." }).el,
      ui.field("Reasoning effort", effort, { hint: "How much OMP reasons before it answers. Models without a reasoning mode ignore it." }).el,
      ui.field("Extra MCP manifest", manifest, { hint: "Path to a JSON file with an mcpServers object. OMP starts those servers next to the built-in tools. Leave it empty to add none." }).el,
      csv.el,
      el("div", { class: "actions adv-save" }, save, status),
      errors,
    );
    return panel.el;
  }

  function keyPanel() {
    const errors = el("div", { class: "adv-errors" });
    const copy = ui.button("Copy local API key", {
      icon: "copy",
      pending: "Copying…",
      async onClick() {
        ui.clearError(errors);
        try {
          await AW.request("server-copy-key");
          ui.toast("Copied the local API key.", { kind: "ok" });
        } catch (thrown) {
          ui.showError(errors, thrown, { retry: () => copy.click() });
        }
      },
    });
    const panel = ui.panel("Local API key");
    panel.body.append(el("p", { class: "muted adv-text", text: "Agents reach the local model with this key. Copy it to use the local model from another program on this computer." }), el("div", { class: "actions" }, copy), errors);
    return panel.el;
  }

  function firecrawlPanel(ctx) {
    const host = el("div", { class: "adv-errors" });
    let tracked = null;
    const lock = (on) => {
      for (const button of [startButton, stopButton]) {
        if (on) button.setAttribute("aria-disabled", "true");
        else button.removeAttribute("aria-disabled");
      }
    };
    const follow = () => {
      if (tracked) tracked.destroy();
      tracked = AW.advanced.track(host, "firecrawl", {
        onDone() {
          tracked = null;
          lock(false);
        },
      });
      if (tracked) lock(true);
    };
    ctx.cleanup(() => tracked && tracked.destroy());
    const run = (start, origin) => {
      const promise = AW.jobs.run("studio-web", { start }, { title: start ? "Starting Firecrawl" : "Stopping Firecrawl", origin, key: "firecrawl" });
      follow();
      return promise.then(
        () => origin.isConnected && ui.toast(start ? "Firecrawl is running." : "Firecrawl stopped.", { kind: "ok" }),
        () => {},
      );
    };
    const startButton = ui.button("Start Firecrawl", { onClick: () => run(true, startButton) });
    const stopButton = ui.button("Stop Firecrawl", { onClick: () => run(false, stopButton) });
    const panel = ui.panel("Page crawling");
    panel.body.append(
      el("p", { class: "muted adv-text", text: "Firecrawl reads web pages for agents when Page crawling is on in Launch. It runs in Docker on this computer, and Workbench starts it when you launch. Start it here ahead of time, or stop it to free memory." }),
      el("div", { class: "actions" }, startButton, stopButton),
      host,
    );
    follow();
    return panel.el;
  }

  AW.advanced.section("Agent settings", async (body, ctx) => {
    const info = await AW.advanced.info();
    if (!ctx.alive()) return;
    const config = (info && info.config) || {};
    body.replaceChildren(settingsPanel(config, ctx), keyPanel(), firecrawlPanel(ctx));
  });
})();
