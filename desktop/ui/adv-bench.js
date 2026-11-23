// Advanced, Benchmarks: local benchmark reports, the tool context cost of the selected tools, the tool selection benchmark and archived runs (ui.md 5.6).
(function () {
  "use strict";
  const AW = window.AW;
  const el = AW.el;
  const ui = AW.ui;
  const TOOL_NAMES = { search: "Web search", firecrawl: "Page crawling", blender: "Blender", desktop: "Windows desktop" };
  const memory = { report: "", measured: null };

  const number = (value) => (Number.isFinite(Number(value)) ? Math.round(Number(value)).toLocaleString("en-GB") : "");
  const fixed = (value, digits) => (Number.isFinite(Number(value)) ? Number(value).toFixed(digits) : "");

  function reportLabel(name) {
    const hit = /^benchmark-(\d{4})(\d{2})(\d{2})-(\d{2})(\d{2})(\d{2})\.json$/.exec(name);
    if (!hit) return name;
    const [, year, month, day, hour, minute] = hit;
    const date = new Date(Number(year), Number(month) - 1, Number(day), Number(hour), Number(minute));
    return date.toLocaleString("en-GB", { day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" });
  }

  function loading(host, label) {
    host.replaceChildren(ui.skeleton(3, label));
  }

  function reportSummary(report) {
    const data = report && typeof report === "object" ? report : {};
    const meta = data.meta && typeof data.meta === "object" ? data.meta : {};
    const pairs = [];
    if (data.model) pairs.push(["Model", String(data.model)]);
    if (meta.platform) pairs.push(["Computer", String(meta.platform)]);
    if (meta.context) pairs.push(["Context", AW.fmt.context(meta.context) + " context"]);
    if (meta.started_at) pairs.push(["Recorded", AW.fmt.ago(meta.started_at)]);
    if (Number(data.checks_total) > 0) pairs.push(["Tool-call checks", number(data.checks_passed) + " of " + number(data.checks_total) + " passed"]);
    if (Number(data.median_decode_tps) > 0) pairs.push(["Median generation rate", fixed(data.median_decode_tps, 1) + " tokens per second"]);
    if (Number(data.median_ttft_s) > 0) pairs.push(["Median time to first token", fixed(data.median_ttft_s, 2) + " s"]);
    const nodes = [pairs.length ? ui.kv(pairs) : el("p", { class: "muted", text: "This report holds no summary." })];
    const missed = Array.isArray(data.incomplete) ? data.incomplete.filter((row) => row && row.name) : [];
    if (missed.length) {
      const list = el("ul", { class: "adv-missed" });
      for (const row of missed) list.append(el("li", null, el("span", { class: "mono", text: String(row.name) }), " · ", String(row.reason || row.outcome || "")));
      nodes.push(el("div", { class: "adv-block" }, el("h3", { class: "group-label", text: "Not completed" }), list));
    }
    nodes.push(el("details", { class: "adv-details" }, el("summary", { text: "Full report" }), el("pre", { class: "adv-pre", tabindex: 0, text: JSON.stringify(report, null, 2) })));
    nodes.push(el("p", { class: "hint", text: "These numbers were measured on this computer for this run only." }));
    return nodes;
  }

  function reportsPanel(ctx) {
    const panel = ui.panel("Local reports");
    const select = el("select", { id: "adv-report" });
    const view = el("div", { class: "adv-report" });
    const pick = ui.field("Report", select);
    const runHost = el("div", { class: "adv-errors" });
    const content = el("div", { class: "stack" });
    let tracked = null;
    const reportTicket = AW.ticket();
    ctx.cleanup(() => tracked && tracked.destroy());

    async function open(name) {
      memory.report = name;
      const mine = reportTicket.next();
      view.classList.add("stale");
      view.setAttribute("aria-busy", "true");
      try {
        const report = await AW.request("studio-result", { name });
        if (!ctx.alive() || !reportTicket.current(mine)) return;
        view.replaceChildren(...reportSummary(report));
      } catch (thrown) {
        if (!ctx.alive() || !reportTicket.current(mine)) return;
        view.replaceChildren();
        ui.showError(view, thrown, { retry: () => open(name) });
      } finally {
        if (reportTicket.current(mine)) {
          view.classList.remove("stale");
          view.removeAttribute("aria-busy");
        }
      }
    }
    select.addEventListener("change", () => open(select.value));

    async function load(prefer) {
      loading(content, "local reports");
      let names;
      try {
        names = await AW.request("studio-results");
      } catch (thrown) {
        if (!ctx.alive()) return;
        content.replaceChildren();
        ui.showError(content, thrown, { retry: () => load(prefer) });
        return;
      }
      if (!ctx.alive()) return;
      const list = Array.isArray(names) ? names.filter((name) => typeof name === "string") : [];
      if (!list.length) {
        content.replaceChildren(ui.empty("No local reports yet. Run the local benchmark to create one."));
        return;
      }
      select.replaceChildren(...list.map((name) => el("option", { value: name, text: reportLabel(name) })));
      select.value = list.includes(prefer) ? prefer : list.includes(memory.report) ? memory.report : list[0];
      content.replaceChildren(pick.el, view);
      open(select.value);
    }

    const run = ui.button("Run local benchmark", {
      onClick() {
        const promise = AW.jobs.run("studio-bench", {}, { title: "Running the local benchmark", origin: run, key: "bench" });
        follow();
        return promise.then(
          (path) => {
            if (!ctx.alive()) return;
            const name = AW.fmt.folder(path);
            load(name);
            if (run.isConnected) ui.toast("Benchmark report saved.", { kind: "ok" });
          },
          () => {},
        );
      },
    });
    const follow = () => {
      if (tracked) tracked.destroy();
      tracked = AW.advanced.track(runHost, "bench", { onDone: () => (tracked = null) });
    };
    panel.body.append(
      el("p", { class: "muted adv-text", text: "The local benchmark sends a fixed set of requests to the model chosen in Model library and saves a report on this computer: generation rate, time to first token and tool-call checks." }),
      el("div", { class: "actions" }, run, el("span", { class: "hint", text: "This can take up to 60 minutes." })),
      runHost,
      content,
    );
    follow();
    load("");
    return panel.el;
  }

  function selectedTools() {
    const form = AW.form.get();
    const names = Object.keys(TOOL_NAMES).filter((key) => form[key]).map((key) => TOOL_NAMES[key]);
    for (const name of String(form.plugins || "").split(",").map((item) => item.trim()).filter(Boolean)) names.push(name);
    return names;
  }

  function measured(value) {
    const data = value && typeof value === "object" ? value : {};
    const rows = Array.isArray(data.servers) ? data.servers : [];
    const nodes = [];
    if (rows.length) {
      const body = el("tbody");
      for (const row of rows) body.append(el("tr", null, el("td", { text: String(row.name ?? "") }), el("td", { text: number(row.tools) }), el("td", { text: "+" + number(row.tokens) })));
      nodes.push(el("table", { class: "table adv-table" }, el("thead", null, el("tr", null, el("th", { text: "Server" }), el("th", { text: "Tools" }), el("th", { text: "Extra input tokens" }))), body));
    }
    const context = Number(data.context);
    const share = context > 0 ? " (" + ((100 * Number(data.tokens || 0)) / context).toFixed(1) + " % of the " + number(context) + "-token context)" : "";
    nodes.push(el("p", { class: "adv-total", text: rows.length ? "Together: " + number(data.tokens) + " tokens" + share + "." : "No MCP server was selected, so the tools add no input tokens." }));
    return nodes;
  }

  function measurePanel(ctx) {
    const panel = ui.panel("Tool context cost");
    const selection = el("p", { class: "adv-target" });
    const paint = () => {
      const names = selectedTools();
      selection.textContent = names.length ? "Switched on in Launch: " + AW.fmt.list(names) + "." : "No tools are switched on in Launch.";
    };
    paint();
    ctx.cleanup(AW.form.on(paint));
    const out = el("div", { id: "measured", class: "stack", attrs: { "aria-live": "polite" } });
    if (memory.measured) out.append(...measured(memory.measured));
    const host = el("div", { class: "adv-errors" });
    let tracked = null;
    ctx.cleanup(() => tracked && tracked.destroy());
    const follow = () => {
      if (tracked) tracked.destroy();
      tracked = AW.advanced.track(host, "measure", { onDone: () => (tracked = null) });
    };
    const button = ui.button("Measure selected plugins", {
      id: "measure",
      onClick() {
        const promise = AW.jobs.run("plugin-measure", AW.form.get(), { title: "Measuring tool context cost", origin: button, key: "measure" });
        follow();
        return promise.then(
          (value) => {
            memory.measured = value;
            if (out.isConnected) out.replaceChildren(...measured(value));
          },
          () => {},
        );
      },
    });
    panel.body.append(
      el("p", { class: "muted adv-text", text: "Measures how many input tokens the tool descriptions of the selected MCP servers add, using the running local model. This starts their MCP services and lists their tools." }),
      selection,
      el("div", { class: "actions" }, button),
      host,
      out,
    );
    follow();
    return panel.el;
  }

  function benchmarkPanel(ctx) {
    const panel = ui.panel("Tool selection benchmark");
    const caption = el("p", { id: "bench-caption", class: "adv-target" });
    const rows = el("tbody", { id: "bench-rows" });
    const table = el("table", { class: "table adv-table" }, el("thead", null, el("tr", null, el("th", { text: "Tools" }), el("th", { text: "Extra input" }), el("th", { text: "Correct calls" }))), rows);
    const warnings = el("div", { class: "stack" });
    const content = el("div", { class: "stack" });
    const host = el("div", { class: "adv-errors" });
    let tracked = null;
    ctx.cleanup(() => tracked && tracked.destroy());

    function paint(data) {
      const m = data && data.measurement && typeof data.measurement === "object" ? data.measurement : {};
      warnings.replaceChildren(...(Array.isArray(data && data.warnings) ? data.warnings : []).map((text) => ui.notice("warn", String(text))));
      rows.replaceChildren();
      const list = Array.isArray(m.rows) ? m.rows : [];
      if (!list.length) {
        caption.textContent = "";
        content.replaceChildren(ui.empty("No measurement yet. Start the local model, then benchmark it."));
        return;
      }
      const parts = [m.model ? String(m.model) : "", m.context ? AW.fmt.context(m.context) + " context" : "", "three routing tasks per tool count"].filter(Boolean);
      caption.textContent = parts.join(" · ") + ". Extra input grows with the size of the tool descriptions." + (m.limit ? " " + String(m.limit) : "");
      for (const row of list) {
        const cases = Array.isArray(row.cases) ? row.cases : [];
        const count = cases.length || 1;
        const tokens = cases.reduce((sum, item) => sum + (Number(item.additional_input_tokens) || 0), 0) / count;
        const passed = cases.filter((item) => item && item.passed).length;
        rows.append(el("tr", null, el("td", { text: number(row.tools) }), el("td", { text: number(tokens) + " tokens" }), el("td", { text: passed + " of " + cases.length })));
      }
      content.replaceChildren(caption, table);
    }

    async function load() {
      loading(content, "tool selection benchmark");
      try {
        const data = await AW.request("plugin-list");
        if (ctx.alive()) paint(data);
      } catch (thrown) {
        if (!ctx.alive()) return;
        content.replaceChildren();
        ui.showError(content, thrown, { retry: load });
      }
    }

    const follow = () => {
      if (tracked) tracked.destroy();
      tracked = AW.advanced.track(host, "bench-plugins", { onDone: () => (tracked = null) });
    };
    const button = ui.button("Benchmark running model", {
      id: "bench-plugins",
      onClick() {
        const promise = AW.jobs.run("plugin-benchmark", {}, { title: "Running the tool selection benchmark", origin: button, key: "bench-plugins" });
        follow();
        return promise.then(
          () => ctx.alive() && load(),
          () => {},
        );
      },
    });
    panel.body.append(
      el("p", { class: "muted adv-text", text: "Checks whether the running local model still picks the right tool as more tools are offered. The newest result on this computer is shown; until you run it, the reference measurement that ships with Agent Workbench is shown." }),
      el("div", { class: "actions" }, button),
      host,
      warnings,
      content,
    );
    follow();
    load();
    return panel.el;
  }

  function archivePanel(ctx) {
    const panel = ui.panel("Archived Terminal-Bench 2.1 runs");
    const content = el("div", { class: "stack" });
    panel.body.append(content);
    async function load() {
      loading(content, "archived runs");
      let rows;
      try {
        rows = await AW.request("studio-evidence");
      } catch (thrown) {
        if (!ctx.alive()) return;
        content.replaceChildren();
        ui.showError(content, thrown, { retry: load });
        return;
      }
      if (!ctx.alive()) return;
      const list = Array.isArray(rows) ? rows.filter((row) => row && typeof row === "object") : [];
      if (!list.length) {
        panel.el.hidden = true;
        return;
      }
      const first = list[0];
      const items = el("ul", { class: "adv-runs" });
      for (const row of list) {
        const spend = Number.isFinite(Number(row.reported_model_spend_usd)) ? " · $" + Number(row.reported_model_spend_usd).toFixed(2) + " reported model spend" : "";
        items.append(el("li", { text: String(row.reasoning ?? "") + " reasoning · " + number(row.passed) + " of " + number(row.attempts) + " tasks passed" + spend }));
      }
      content.replaceChildren(el("p", { class: "adv-target", text: String(first.model ?? "") + " · " + String(first.agent ?? "") + " · one attempt per task. Spend comes from the run notes." }), items);
    }
    load();
    return panel.el;
  }

  AW.advanced.section("Benchmarks", (body, ctx) => {
    body.replaceChildren(reportsPanel(ctx), measurePanel(ctx), benchmarkPanel(ctx), archivePanel(ctx));
  });
})();
