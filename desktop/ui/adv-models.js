// Advanced, Model library: the recommended model, the catalog with fit and status, your model folder with its engine settings and named
// setups, and the engine for local models (ui.md 5.6). Downloads and installs run as tasks through AW.models; a dropped file or folder is
// accepted only on the model folder panel and asks first. The window-level drag handlers only stop the window from opening the file.
(function () {
  "use strict";
  const AW = window.AW;
  const el = AW.el;
  const ui = AW.ui;
  const LOCAL = "Managed local";
  const FRESH_MS = 30000;
  const FIT_WORDS = { comfortable: "Yes", tight: "Tight fit", listed: "Likely", try: "Very tight", unknown: "Unknown", no: "Too large" };
  const BACKENDS = { metal: "Metal", cuda: "CUDA", vulkan: "Vulkan", cpu: "CPU" };
  const WEIGHTS = /\.(?:gguf|strata|gguf_file)$/i;
  const notes = new Map();
  let dropZone = null;

  const base = (path) => String(path || "").split(/[\\/]+/).filter(Boolean).pop() || "";
  const parent = (path) => String(path || "").replace(/[\\/]+[^\\/]*[\\/]*$/, "") || String(path || "");
  const fits = (entry) => Boolean(entry && entry.sizing && entry.sizing.fit !== "no");
  const onDisk = (entry) => Boolean(entry && entry.installed && entry.installed.installed);
  const partial = (entry) => Boolean(entry && entry.installed && !entry.installed.installed && Number(entry.installed.have_bytes) > 0);
  const keyOf = (entry) => "model:" + entry.id;
  const gib = (n) => (Number.isFinite(Number(n)) ? Number(n).toFixed(1) + " GiB" : "");

  function inUse(entry) {
    const config = AW.state.config;
    const current = config && config.current && config.current.model;
    return onDisk(entry) && Boolean(current) && base(entry.installed.model) === current;
  }

  function sizingSentence(entry) {
    const sizing = entry.sizing || {};
    if (sizing.tier_ok === false && Number(entry.min_memory_gib) > 0)
      return entry.label + " is listed for computers with " + gib(entry.min_memory_gib) + " of memory or more; this computer has " + gib(sizing.total_gib) + ".";
    if (Number(sizing.need_gib) > 0 && Number(sizing.usable_gib) > 0)
      return entry.label + " needs about " + gib(sizing.need_gib) + " and this computer can give it " + gib(sizing.usable_gib) + ".";
    return typeof sizing.arithmetic === "string" ? sizing.arithmetic : "";
  }

  function facts(entry) {
    const parts = [AW.fmt.bytes(entry.download_bytes)];
    const context = entry.settings && AW.fmt.context(entry.settings.context);
    if (context) parts.push(context + " context");
    if (entry.licence) parts.push(entry.licence + " licence");
    return parts.filter(Boolean).join(" · ");
  }

  function noteFor(key) {
    const error = notes.get(key);
    if (!error) return null;
    if (error.code === "cancelled")
      return ui.notice("neutral", error.message + (error.partial ? " The part already downloaded is kept." : ""), { dismissible: true, onDismiss: () => notes.delete(key) });
    return ui.errorNotice(error, { retry: error.retry });
  }

  function follower(ctx) {
    const watchers = new Set();
    function attach(watch) {
      if (watch.task || !watch.host.isConnected) return;
      const record = AW.jobs.find(watch.key);
      if (!record) return;
      watch.task = ui.taskRow(record.handle);
      watch.handle = record.handle;
      watch.host.append(watch.task.el);
      watch.host.hidden = false;
    }
    const off = AW.jobs.on((record, kind) => {
      if (!record.key || kind === "progress") return;
      for (const watch of [...watchers]) {
        if (watch.key !== record.key) continue;
        if (kind === "started") attach(watch);
        else if (watch.handle === record.handle) {
          const focused = watch.task && watch.task.el.contains(document.activeElement);
          if (watch.task) watch.task.destroy();
          watch.task = null;
          watch.handle = null;
          if (watch.onDone) watch.onDone(record, focused);
        }
      }
    });
    ctx.cleanup(() => {
      off();
      for (const watch of watchers) if (watch.task) watch.task.destroy();
      watchers.clear();
    });
    return {
      watch(key, host, onDone) {
        const watch = { key, host, onDone, task: null, handle: null };
        watchers.add(watch);
        attach(watch);
        return () => {
          watchers.delete(watch);
          if (watch.task) watch.task.destroy();
        };
      },
      reset() {
        for (const watch of watchers) if (watch.task) watch.task.destroy();
        watchers.clear();
      },
      sweep() {
        for (const watch of watchers) attach(watch);
      },
    };
  }

  function installButton(entry, ctx, hooks, { primary = false } = {}) {
    const resume = partial(entry) && !AW.jobs.find(keyOf(entry));
    const label = onDisk(entry) ? "Use" : resume ? "Resume download" : "Download";
    const name = onDisk(entry) ? "Use " + entry.label : (resume ? "Resume the download of " : "Download ") + entry.label;
    const button = ui.button(label, { small: true, kind: primary ? "primary" : undefined });
    button.setAttribute("aria-label", name);
    if (AW.jobs.find(keyOf(entry))) button.setAttribute("aria-disabled", "true");
    button.addEventListener("click", () => {
      if (button.getAttribute("aria-disabled") === "true") return;
      hooks.install(entry, button);
    });
    return button;
  }

  function models(ctx, hooks) {
    const ticket = AW.ticket();
    const jobs = follower(ctx);
    const data = { catalog: null, recommend: null, info: null };
    const recPanel = ui.panel("Recommended for this computer");
    const catPanel = ui.panel("Catalog");
    recPanel.body.append(ui.skeleton(3, "the recommended model"));
    catPanel.body.append(ui.skeleton(3, "the catalog"));

    function install(entry, button) {
      const key = keyOf(entry);
      const use = onDisk(entry);
      const clicked = Date.now();
      notes.delete(key);
      const promise = AW.models.install(entry, { origin: button });
      return promise.then(
        (result) => {
          if (use) {
            AW.form.set({ provider: LOCAL, connection_id: "", preset: result.preset_name });
            ui.toast("Launch now uses " + result.preset_name + ".", { kind: "ok" });
          } else ui.toast(entry.label + " is downloaded and saved as the setup " + result.preset_name + ".", { kind: "ok" });
          reload();
        },
        (thrown) => {
          const error = AW.asError(thrown);
          const started = AW.jobs.list().some((record) => record.key === key && record.started >= clicked);
          notes.set(key, { ...error, partial: error.code === "cancelled" && !use && (started || partial(entry)), retry: () => install(entry, button) });
          reload();
        },
      );
    }
    hooks.install = install;

    function hostFor(entry) {
      const host = el("div", { class: "stack", hidden: true, data: { entry: entry.id, jobKey: keyOf(entry) } });
      host.style.gap = "var(--space-3)";
      const note = noteFor(keyOf(entry));
      if (note) {
        host.append(note);
        host.hidden = false;
        note.addEventListener("click", (event) => {
          if (event.target.closest(".btn")) setTimeout(() => !note.isConnected && notes.delete(keyOf(entry)));
        });
      }
      jobs.watch(keyOf(entry), host, (record, focused) => {
        if (record.state !== "done" && record.error) notes.set(keyOf(entry), { ...record.error, partial: record.state === "cancelled", retry: () => install(entry, null) });
        reload().then(() => {
          if (!focused) return;
          const again = spotFocus({ entry: entry.id, task: false });
          if (again) again.focus();
        });
      });
      return host;
    }

    function paintRecommended() {
      const rec = data.recommend;
      const body = recPanel.body;
      body.replaceChildren();
      const label = (rec && rec.hardware_label) || (AW.state.hardware && AW.state.hardware.label) || "";
      if (label) body.append(el("p", { class: "muted", text: label }));
      const pick = rec && rec.pick;
      if (!pick) {
        body.append(
          ui.empty((rec && rec.reason) || "No model in the catalog fits this computer.", [
            { label: "Attach a running server…", run: () => AW.attach.open({}) },
            { label: "Add a hosted API…", run: () => AW.router.go("accounts", { add: "hosted", returnTo: "advanced" }) },
          ]),
        );
        return;
      }
      const head = el("div", { class: "actions", data: { entry: pick.id } }, el("strong", { text: pick.label }), el("span", { class: "muted", text: facts(pick) }), AW.models.chip(pick));
      const action = inUse(pick) ? ui.chip("In use", "accent") : installButton(pick, ctx, hooks, { primary: true });
      action.style.marginLeft = "auto";
      head.append(action);
      const card = el("div", { class: "stack" }, head);
      card.style.gap = "var(--space-4)";
      if (onDisk(pick)) card.append(el("p", { class: "hint", text: "Already downloaded." }));
      if (typeof rec.reason === "string" && rec.reason) card.append(el("p", { class: "wrap", text: rec.reason }));
      const sizing = pick.sizing && typeof pick.sizing.arithmetic === "string" ? pick.sizing.arithmetic : "";
      if (sizing) card.append(el("details", null, el("summary", { text: "How this was sized" }), el("p", { class: "hint mono wrap", text: sizing })));
      for (const warning of Array.isArray(rec.warnings) ? rec.warnings : [])
        if (typeof warning === "string" && warning) {
          const line = el("p", { class: "wrap" }, ui.mark("warn"), " ", warning);
          line.style.fontSize = "var(--text-md)";
          card.append(line);
        }
      if (!onDisk(pick) && !AW.jobs.find(keyOf(pick))) {
        const engine = AW.state.engine;
        const engineBytes = engine && engine.engine_ready === false && engine.supported !== false ? AW.fmt.bytes(engine.download_bytes) : "";
        const folder = data.info && data.info.config && data.info.config.library;
        const what = "Downloads: the model (" + AW.fmt.bytes(pick.download_bytes) + ")" + (engineBytes ? " and the engine that runs it (" + engineBytes + ")" : "");
        card.append(el("p", { class: "hint wrap", text: what + (folder ? ", into " + AW.fmt.path(folder) : "") + ". You can leave this page while it downloads." }));
      }
      const engineHost = el("div", { hidden: true });
      jobs.watch("engine", engineHost, () => reload());
      card.append(engineHost, hostFor(pick));
      body.append(card);
    }

    function paintCatalog() {
      const catalog = data.catalog;
      const body = catPanel.body;
      body.replaceChildren();
      const entries = catalog && Array.isArray(catalog.entries) ? catalog.entries.filter((entry) => entry && typeof entry.id === "string") : [];
      catPanel.setCount(entries.length || null);
      if (!entries.length) {
        body.append(ui.empty("The catalog has no model for this computer's engine. Attach a running server or add a hosted API instead.", [{ label: "Attach a running server…", run: () => AW.attach.open({}) }]));
        return;
      }
      if (!entries.some(fits))
        body.append(
          ui.empty("No model in the catalog fits this computer.", [
            { label: "Attach a running server…", run: () => AW.attach.open({}) },
            { label: "Add a hosted API…", run: () => AW.router.go("accounts", { add: "hosted", returnTo: "advanced" }) },
          ]),
        );
      const rows = el("tbody");
      for (const entry of entries) {
        const fit = entry.sizing && entry.sizing.fit;
        const name = el("td", null, el("span", { text: entry.label }));
        if (!fits(entry)) name.append(el("p", { class: "hint wrap", text: sizingSentence(entry) }));
        else if (typeof entry.licence_note === "string" && entry.licence_note && !onDisk(entry)) name.append(el("p", { class: "hint wrap", text: entry.licence_note }));
        const disk = el("td", { text: onDisk(entry) ? "Yes" : partial(entry) ? "Partly" : "No" });
        if (partial(entry)) disk.append(el("span", { class: "hint", text: AW.fmt.bytes(entry.installed.have_bytes) + " so far" }));
        const action = el("td", { data: { entry: entry.id } });
        action.style.textAlign = "right";
        if (inUse(entry)) action.append(ui.chip("In use", "accent"));
        else if (onDisk(entry) || fits(entry)) action.append(installButton(entry, ctx, hooks));
        const row = el(
          "tr",
          { class: fits(entry) ? "" : "stale" },
          name,
          el("td", { text: AW.fmt.bytes(entry.download_bytes) }),
          el("td", { text: FIT_WORDS[fit] || "Unknown" }),
          el("td", null, AW.models.chip(entry)),
          disk,
          action,
        );
        for (const cell of [...row.children].slice(1, 5)) cell.style.whiteSpace = "nowrap";
        rows.append(row);
        const host = hostFor(entry);
        const cell = el("td", { attrs: { colspan: "6" } }, host);
        const extra = el("tr", { hidden: host.hidden }, cell);
        new MutationObserver(() => {
          extra.hidden = !host.childElementCount;
          host.hidden = !host.childElementCount;
        }).observe(host, { childList: true });
        rows.append(extra);
      }
      const head = el(
        "tr",
        null,
        ["Model", "Size", "Fits", "Status", "On disk"].map((text) => {
          const cell = el("th", { attrs: { scope: "col" }, text });
          cell.style.whiteSpace = "nowrap";
          return cell;
        }),
        el("th", { attrs: { scope: "col" } }, el("span", { class: "sr-only", text: "Action" })),
      );
      const wrap = el("div", null, el("table", { class: "table" }, el("thead", null, head), rows));
      wrap.style.overflowX = "auto";
      body.append(wrap);
    }

    function spotFocus(spot, scope = document) {
      const holders = [...scope.querySelectorAll('[data-entry="' + CSS.escape(String(spot.entry)) + '"]')];
      const cancel = spot.task ? holders.map((node) => [...node.querySelectorAll(".task button")].find((button) => !button.hidden)).find(Boolean) : null;
      return cancel || holders.map((node) => node.querySelector("button[aria-label]")).find(Boolean) || null;
    }

    function focusSpot() {
      const active = document.activeElement;
      const panel = [recPanel.el, catPanel.el].find((node) => active && node.contains(active));
      const holder = panel && active.closest("[data-entry]");
      return holder ? { panel, entry: holder.dataset.entry, task: Boolean(active.closest(".task")) } : null;
    }

    async function reload() {
      const mine = ticket.next();
      const recommend = AW.state.recommend && AW.store.age("recommend") < FRESH_MS && !AW.store.error("recommend") ? Promise.resolve(AW.state.recommend) : AW.store.refresh("recommend");
      const engine = AW.state.engine && AW.store.age("engine") < FRESH_MS ? Promise.resolve(AW.state.engine) : AW.store.refresh("engine").catch(() => null);
      const [catalog, rec, info] = await Promise.allSettled([AW.request("model-catalog"), recommend, AW.advanced.info(), engine]);
      if (!ctx.alive() || !ticket.current(mine)) return;
      const spot = focusSpot();
      jobs.reset();
      data.info = info.status === "fulfilled" ? info.value : null;
      if (rec.status === "fulfilled") {
        data.recommend = rec.value;
        paintRecommended();
      } else recPanel.body.replaceChildren(ui.errorNotice(rec.reason, { retry: () => AW.store.refresh("recommend", { force: true }).then(reload, reload) }));
      if (catalog.status === "fulfilled") {
        data.catalog = catalog.value;
        paintCatalog();
      } else catPanel.body.replaceChildren(ui.errorNotice(catalog.reason, { retry: reload }));
      jobs.sweep();
      const active = document.activeElement;
      if (spot && (!active || active === document.body || !active.isConnected)) {
        const again = spotFocus(spot, spot.panel);
        if (again) again.focus({ preventScroll: true });
      }
    }

    return { panels: [recPanel.el, catPanel.el], load: reload };
  }

  function folderPanel(ctx, hooks) {
    const panel = ui.panel("Your model folder");
    const body = panel.body;
    body.append(ui.skeleton(3, "your model folder"));
    let snapshot = "";
    let read = null;
    let unwatchImport = null;

    function dirty() {
      return Boolean(read) && JSON.stringify(read()) !== snapshot;
    }
    ctx.guard(dirty);

    function select(id, options, value) {
      const node = el("select", { id }, options.map(([v, label]) => el("option", { value: v, text: label })));
      node.value = value;
      return node;
    }

    function build(info) {
      const config = (info && info.config) || {};
      const engines = Array.isArray(info && info.engines) ? info.engines.map(String) : ["Auto"];
      const presets = (AW.state.config && AW.state.config.presets) || {};
      let library = String(config.library || "");
      const status = el("p", { class: "hint", attrs: { role: "status" } });
      const errors = el("div");

      const where = el("p", { class: "mono wrap", text: AW.fmt.path(library) });
      where.style.fontSize = "var(--text-md)";
      const moved = el("p", { class: "hint", hidden: true, text: "Not saved yet. Choose Save settings to use this folder." });
      const weights = el("select", { id: "adv-weights" });
      const usable = () => {
        const budget = AW.state.recommend && AW.state.recommend.budget;
        return budget && Number(budget.usable_gib) > 0 ? Number(budget.usable_gib) : 0;
      };
      const weightsField = ui.field("Weights", weights, { hint: "The model file the local model loads." });

      async function scan() {
        const keep = weights.value || String(config.model || "");
        let items;
        try {
          items = await AW.request("studio-library", { library });
        } catch (error) {
          if (ctx.alive()) ui.showError(errors, error, { retry: scan });
          return;
        }
        if (!ctx.alive()) return;
        const room = usable();
        const rows = (Array.isArray(items) ? items : []).filter((item) => item && typeof item.path === "string");
        const options = [el("option", { value: "", text: rows.length ? "Choose a model file" : "No model files in this folder" })];
        for (const item of rows) {
          const size = Number(item.gib);
          const verdict = room && Number.isFinite(size) ? (size <= room ? " · fits" : " · may not fit") : "";
          options.push(el("option", { value: item.path, text: base(item.path) + " · " + AW.fmt.gib(size) + verdict }));
        }
        if (keep && !rows.some((item) => item.path === keep)) options.push(el("option", { value: keep, text: base(keep) + " · not found in this folder" }));
        weights.replaceChildren(...options);
        weights.value = keep;
        weightsField.setError(keep && !rows.some((item) => item.path === keep) ? "This file is not in the folder any more. Choose another one, or rescan after you move it back." : null);
        paint();
      }

      const browse = ui.button("Browse folders…", {
        icon: "folder",
        async onClick() {
          const dir = await AW.request("folder-pick");
          if (!dir || !ctx.alive()) return;
          library = String(dir);
          where.textContent = AW.fmt.path(library);
          await scan();
        },
      });
      const rescan = ui.button("Rescan", { icon: "refresh", pending: "Scanning…", onClick: () => scan() });
      const dropHint = el("p", { class: "hint", text: "Drop a model file or folder here to add it." });

      const engine = select("adv-engine", engines.map((name) => [name, name]), engines.includes(config.engine) ? config.engine : engines[0]);
      const context = el("input", { type: "number", id: "adv-context", value: String(config.context ?? 32768), attrs: { min: "1024", max: "1048576", step: "1024", inputmode: "numeric" } });
      const contextField = ui.field("Context", context, { hint: "Tokens the model keeps in view." });
      const engineField = ui.field("Engine", engine);
      const pair = el("div", { class: "actions" }, engineField.el, contextField.el);
      pair.style.alignItems = "start";
      for (const node of [engineField.el, contextField.el]) {
        node.style.flex = "1 1 200px";
        node.style.marginBottom = "0";
      }

      const kv = select("adv-kv", [["auto", "Automatic"], ["fp8", "8-bit (fp8)"]], config.kv === "fp8" ? "fp8" : "auto");
      const channel = select("adv-channel", [["Tested", "Tested"], ["Updated", "Updated"]], config.channel === "Updated" ? "Updated" : "Tested");
      const draft = ui.toggle("Speculative decoding", { description: "Uses a small draft model to answer faster when the setup has one.", checked: Boolean(config.draft) });
      const vision = ui.toggle("Vision companion", { description: "Loads the matching image model when the setup has one.", checked: Boolean(config.vision) });
      const lora = ui.toggle("Matched Qwen LoRA", { description: "Loads the LoRA adapter that matches a Qwen model when one is present.", checked: Boolean(config.lora) });
      const more = el("details", null, el("summary", { text: "More engine settings" }));
      const moreBody = el("div", { class: "stack" });
      moreBody.style.marginTop = "var(--space-5)";
      moreBody.style.gap = "var(--space-2)";
      moreBody.append(
        ui.field("KV cache", kv, { hint: "8-bit uses less memory for long contexts." }).el,
        ui.field("Engine release", channel, { hint: "Tested is the pinned build. Updated is the newest release, which has not been tested with Workbench." }).el,
        draft.el,
        vision.el,
        lora.el,
      );
      const extra = {};
      if (engines.includes("vLLM")) {
        extra.vllm_model = el("input", { type: "text", class: "mono", value: String(config.vllm_model ?? ""), attrs: { autocomplete: "off", spellcheck: "false" } });
        extra.vllm_fraction = el("input", { type: "text", value: String(config.vllm_fraction ?? ""), attrs: { inputmode: "decimal" } });
        extra.vllm_parser = el("input", { type: "text", class: "mono", value: String(config.vllm_parser ?? ""), attrs: { autocomplete: "off", spellcheck: "false" } });
        moreBody.append(
          ui.field("vLLM checkpoint", extra.vllm_model, { hint: "A Hugging Face repository or a folder with the model." }).el,
          ui.field("GPU memory fraction", extra.vllm_fraction, { hint: "The share of GPU memory vLLM may use, for example 0.80." }).el,
          ui.field("Tool parser", extra.vllm_parser, { hint: "vLLM's tool-call parser for this model. Leave it empty for the default." }).el,
        );
      }
      if (engines.includes("Custom")) {
        extra.engine_manifest = el("input", { type: "text", class: "mono", value: String(config.engine_manifest ?? ""), attrs: { autocomplete: "off", spellcheck: "false", placeholder: "/path/to/engine.json" } });
        moreBody.append(ui.field("Custom engine manifest", extra.engine_manifest, { hint: "A JSON file that says how to start your own engine. Used when Engine is Custom." }).el);
      }
      more.append(moreBody);

      const name = el("input", { type: "text", id: "adv-setup-name", attrs: { autocomplete: "off", placeholder: "Daily" } });
      const nameField = ui.field("Save as a named setup (optional)", name, { hint: "A named setup appears in Launch's model list and can get its own launcher." });

      read = () => {
        const values = {
          library,
          model: weights.value,
          engine: engine.value,
          context: context.value.trim(),
          kv: kv.value,
          channel: channel.value,
          draft: draft.input.checked,
          vision: vision.input.checked,
          lora: lora.input.checked,
        };
        for (const [key, node] of Object.entries(extra)) values[key] = node.value.trim();
        return values;
      };

      function paint() {
        moved.hidden = library === String(config.library || "");
        status.textContent = dirty() ? "You have unsaved changes." : "";
      }

      const save = ui.button("Save settings", {
        kind: "primary",
        pending: "Saving…",
        async onClick() {
          ui.clearError(errors);
          const values = read();
          const tokens = Number(values.context);
          if (!Number.isInteger(tokens) || tokens < 1024 || tokens > 1048576) {
            contextField.setError("Choose a context between 1,024 and 1,048,576.");
            context.focus();
            return;
          }
          contextField.setError(null);
          const label = name.value.trim();
          if (label && Object.hasOwn(presets, label)) {
            const ok = await ui.confirm({ text: "Replace the saved setup " + label + "?", ok: "Replace" });
            if (!ok) return;
          }
          const sent = { ...values, context: tokens };
          try {
            await AW.request("studio-save", label ? { ...sent, name: label } : sent);
          } catch (error) {
            if (ctx.alive()) ui.showError(errors, error, { retry: () => save.click() });
            return;
          }
          if (!ctx.alive()) return;
          config.library = library;
          config.model = values.model;
          snapshot = JSON.stringify(read());
          paint();
          ui.toast(label ? "Saved the settings and the setup " + label + "." : "Model settings saved.", { kind: "ok" });
          AW.advanced.info({ force: true }).catch(() => {});
          AW.store.refresh("config").catch(() => {});
          hooks.reloadModels();
        },
      });
      const launcher = ui.button("Create launcher", {
        pending: "Creating…",
        async onClick() {
          ui.clearError(errors);
          const label = name.value.trim();
          if (!label) {
            nameField.setError("Type a name and choose Save settings first; the launcher starts that setup.");
            name.focus();
            return;
          }
          nameField.setError(null);
          try {
            const path = await AW.request("studio-shortcut", { name: label });
            ui.toast("Created the launcher " + String(path) + ".", { kind: "ok" });
          } catch (error) {
            if (ctx.alive()) ui.showError(errors, error);
          }
        },
      });

      for (const node of [weights, engine, kv, channel, draft.input, vision.input, lora.input]) node.addEventListener("change", paint);
      for (const node of [context, ...Object.values(extra)]) node.addEventListener("input", paint);
      name.addEventListener("input", () => nameField.setError(null));

      async function importPath(path) {
        if (!path) return;
        const folder = WEIGHTS.test(path) ? parent(path) : path;
        const old = String(config.library || "");
        if (folder !== old) {
          const lose = dirty() ? " Unsaved changes in this panel are discarded." : "";
          const ok = await ui.confirm({ text: "Use " + folder + " as your model folder instead of " + old + "?" + lose, ok: "Use this folder" });
          if (!ok || !ctx.alive()) return;
        }
        ui.clearError(errors);
        let value;
        try {
          value = await AW.jobs.run("studio-import", { path }, { title: "Scanning " + (base(folder) || folder), origin: rescan, key: "import" });
        } catch (error) {
          if (ctx.alive()) ui.showError(errors, error);
          return;
        }
        const count = Number(value && value.models) || 0;
        const files = count === 1 ? "1 model file" : count + " model files";
        ui.toast(value && value.changed ? "Your model folder is now " + value.library + ". Found " + files + "." : "Found " + files + " in " + (value ? value.library : folder) + ".", { kind: "ok" });
        AW.store.refresh("config").catch(() => {});
        hooks.reloadModels();
        if (ctx.alive()) load(true);
      }

      const zone = panel.el;
      const hasFiles = (event) => Boolean(event.dataTransfer && [...(event.dataTransfer.types || [])].includes("Files"));
      const glow = (on) => {
        zone.style.outline = on ? "2px dashed var(--accent)" : "";
        zone.style.outlineOffset = on ? "-6px" : "";
      };
      zone.addEventListener("dragover", (event) => {
        if (!hasFiles(event)) return;
        event.preventDefault();
        event.dataTransfer.dropEffect = "copy";
        glow(true);
      });
      zone.addEventListener("dragleave", (event) => {
        if (!zone.contains(event.relatedTarget)) glow(false);
      });
      zone.addEventListener("drop", (event) => {
        event.preventDefault();
        glow(false);
        const file = event.dataTransfer && event.dataTransfer.files && event.dataTransfer.files[0];
        if (!file || !window.workbench || typeof window.workbench.filePath !== "function") return;
        let path = "";
        try {
          path = window.workbench.filePath(file);
        } catch {
          path = "";
        }
        if (!path) {
          ui.showError(errors, { message: "Agent Workbench could not read where that item is. Use Browse folders… instead." });
          return;
        }
        importPath(path);
      });
      dropZone = zone;
      ctx.cleanup(() => {
        if (dropZone === zone) dropZone = null;
      });

      const actions = el("div", { class: "actions" }, browse, rescan);
      const saveRow = el("div", { class: "actions" }, save, launcher, status);
      const importHost = el("div", { hidden: true });
      body.replaceChildren(
        el("div", null, where, moved),
        el("div", { class: "actions" }, actions, dropHint),
        importHost,
        weightsField.el,
        pair,
        more,
        nameField.el,
        saveRow,
        errors,
      );
      for (const node of [where.parentNode, actions.parentNode, weightsField.el, pair, more, nameField.el])
        node.style.marginBottom = "var(--space-6)";
      if (unwatchImport) unwatchImport();
      unwatchImport = hooks.watch("import", importHost);
      hooks.sweep();
      snapshot = "";
      scan().then(() => {
        if (!ctx.alive()) return;
        snapshot = JSON.stringify(read());
        paint();
      });
      snapshot = JSON.stringify(read());
    }

    async function load(force) {
      let info;
      try {
        info = await AW.advanced.info({ force: Boolean(force) });
      } catch (error) {
        if (!ctx.alive()) return;
        body.replaceChildren(ui.errorNotice(error, { retry: () => load(true) }));
        return;
      }
      if (!ctx.alive()) return;
      build(info);
    }

    return { el: panel.el, load };
  }

  function enginePanel(ctx, hooks) {
    const panel = ui.panel("Engine");
    panel.body.append(ui.skeleton(2, "the engine"));
    const tasks = el("div", { hidden: true });
    const errors = el("div");

    function paint(info) {
      const body = panel.body;
      const lines = [];
      const actions = [];
      if (info.supported === false) lines.push(el("p", { class: "wrap", text: info.reason || "The engine for local models cannot run on this computer." }));
      else if (info.engine_ready) {
        const parts = ["llama.cpp" + (info.strata ? " and Strata" : ""), BACKENDS[info.installed_backend || info.backend] || info.installed_backend || info.backend, info.tag ? "build " + info.tag : "", "installed"];
        lines.push(el("p", null, ui.mark("ok"), " ", parts.filter(Boolean).join(" · ")));
        actions.push(ui.button("Update engine", { small: true, onClick: (event) => run(true, event.currentTarget) }));
      } else {
        const size = AW.fmt.bytes(info.download_bytes);
        lines.push(el("p", null, ui.mark("idle"), " ", "Not installed yet" + (size ? " (" + size + ")" : "") + ". Only needed to run a model on this computer."));
        if (info.reason) lines.push(el("p", { class: "hint wrap", text: info.reason }));
        actions.push(ui.button("Install engine", { small: true, kind: "primary", onClick: (event) => run(false, event.currentTarget) }));
      }
      for (const note of Array.isArray(info.notes) ? info.notes : []) if (typeof note === "string" && note) lines.push(el("p", { class: "hint wrap", text: note }));
      const head = el("div", { class: "actions" }, el("div", { class: "stack" }, lines));
      head.firstChild.style.gap = "var(--space-2)";
      head.firstChild.style.flex = "1 1 280px";
      if (actions.length) head.append(el("div", { class: "actions" }, actions));
      head.style.alignItems = "start";
      if (AW.jobs.find("engine")) for (const button of actions) button.setAttribute("aria-disabled", "true");
      body.replaceChildren(head, tasks, errors);
      hooks.sweep();
    }

    function run(update, button) {
      ui.clearError(errors);
      return AW.models.engine({ origin: button, update }).then(
        () => load(true),
        (error) => {
          if (!ctx.alive()) return;
          load(true).then(() => ui.showError(errors, error, { retry: () => run(update, button) }));
        },
      );
    }

    async function load(force) {
      let info;
      try {
        info = !force && AW.state.engine && AW.store.age("engine") < FRESH_MS ? AW.state.engine : await AW.store.refresh("engine", { force: Boolean(force) });
      } catch (error) {
        if (!ctx.alive()) return;
        panel.body.replaceChildren(ui.errorNotice(error, { retry: () => load(true) }));
        return;
      }
      if (!ctx.alive() || !info) return;
      paint(info);
    }

    hooks.watch("engine", tasks, () => load(true));
    return { el: panel.el, load };
  }

  AW.advanced.section("Model library", async (body, ctx) => {
    const hooks = {};
    const shared = follower(ctx);
    hooks.watch = (key, host, onDone) => shared.watch(key, host, onDone);
    hooks.sweep = () => shared.sweep();
    const lib = models(ctx, hooks);
    hooks.reloadModels = () => lib.load();
    const folder = folderPanel(ctx, hooks);
    const engine = enginePanel(ctx, hooks);
    body.append(...lib.panels, folder.el, engine.el);
    await Promise.all([lib.load(), folder.load(), engine.load()]);
  });

  if (AW.shell)
    AW.shell.register(() => {
      window.addEventListener("dragover", (event) => {
        event.preventDefault();
        if (event.dataTransfer && !(dropZone && dropZone.contains(event.target))) event.dataTransfer.dropEffect = "none";
      });
      window.addEventListener("drop", (event) => event.preventDefault());
    });
})();
