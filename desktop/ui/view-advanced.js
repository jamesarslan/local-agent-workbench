// Advanced view: the section list, the section select below 900 px and the section registry (ui.md 5.6).
// AW.advanced.section(name, render) registers a section. render(body, ctx) fills #advanced-body and may return a promise, a cleanup function
// or an object with destroy(). ctx carries the route params (ctx.tool, ctx.focus, … and ctx.params), ctx.alive() (false once another
// render started or the view was left; check it after every await), ctx.cleanup(fn), ctx.guard(fn) (fn() -> true while the section holds
// unsaved changes) and ctx.rerender(). AW.advanced.info() shares one studio-info answer between sections for a few seconds.
(function () {
  "use strict";
  const AW = window.AW;
  const el = AW.el;
  const ui = AW.ui;
  const ORDER = ["Setup", "Model library", "Agent settings", "Hugging Face", "Benchmarks", "Trace", "Sandbox", "Appearance"];
  const INFO_MS = 5000;

  const renders = new Map();
  const tabs = new Map();
  let live = null;
  let chosen = ORDER[0];
  let parts = null;
  let info = null;

  const slug = (name) => String(name).toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
  const names = () => [...ORDER, ...[...renders.keys()].filter((name) => !ORDER.includes(name))];
  const known = (name) => typeof name === "string" && names().includes(name);

  function end(record) {
    if (!record || !record.active) return;
    record.active = false;
    if (record.observer) record.observer.disconnect();
    for (const fn of record.cleanups.splice(0)) {
      try {
        fn();
      } catch (thrown) {
        console.error(thrown);
      }
    }
  }

  function dirty(record) {
    if (!record || !record.active) return false;
    return record.guards.some((fn) => {
      try {
        return Boolean(fn());
      } catch {
        return false;
      }
    });
  }

  async function mayLeave(record) {
    if (!dirty(record)) return true;
    return ui.confirm({ text: "Discard your unsaved changes in " + record.name + "?", ok: "Discard", cancel: "Keep editing", danger: true });
  }

  function keep(record, value) {
    if (!value) return;
    if (typeof value === "function") record.cleanups.push(value);
    else if (typeof value.destroy === "function") record.cleanups.push(() => value.destroy());
  }

  function paintChoice(name) {
    for (const [key, tab] of tabs) {
      const on = key === name;
      tab.setAttribute("aria-selected", String(on));
      tab.tabIndex = on ? 0 : -1;
    }
    if (parts.select.value !== name) parts.select.value = name;
    const tab = tabs.get(name);
    if (tab) parts.body.setAttribute("aria-labelledby", tab.id);
  }

  function render(name, params) {
    end(live);
    chosen = name;
    paintChoice(name);
    const body = parts.body;
    const record = { name, active: true, cleanups: [], guards: [], observer: null };
    live = record;
    body.replaceChildren();
    body.removeAttribute("aria-busy");
    const fn = renders.get(name);
    if (!fn) {
      body.append(ui.empty("This section is being rebuilt."));
      return Promise.resolve();
    }
    const skeleton = ui.skeleton(3, name);
    body.append(skeleton);
    body.setAttribute("aria-busy", "true");
    const settle = () => {
      if (record.observer) record.observer.disconnect();
      record.observer = null;
      if (skeleton.isConnected) skeleton.remove();
      if (live === record) body.removeAttribute("aria-busy");
    };
    record.observer = new MutationObserver(() => {
      if ([...body.children].some((child) => child !== skeleton)) settle();
    });
    record.observer.observe(body, { childList: true });
    const ctx = {
      ...(params || {}),
      params: { ...(params || {}) },
      section: name,
      body,
      alive: () => record.active && live === record,
      cleanup: (cleanupFn) => (record.active ? record.cleanups.push(cleanupFn) : cleanupFn()),
      guard: (guardFn) => record.guards.push(guardFn),
      rerender: () => (live === record ? render(name, params) : Promise.resolve()),
    };
    const failed = (thrown) => {
      if (!ctx.alive()) return;
      console.error(thrown);
      settle();
      body.replaceChildren(ui.errorNotice(thrown, { retry: () => render(name, params) }));
      ui.announce(AW.asError(thrown).message, { assertive: true });
    };
    let result;
    try {
      result = fn(body, ctx);
    } catch (thrown) {
      failed(thrown);
      return Promise.resolve();
    }
    if (!result || typeof result.then !== "function") {
      keep(record, result);
      settle();
      return Promise.resolve();
    }
    return result.then(
      (value) => {
        if (record.active) keep(record, value);
        else if (value) keep({ cleanups: [] }, value);
        if (live === record) settle();
      },
      failed,
    );
  }

  async function choose(name, { focusTab = false } = {}) {
    if (!known(name)) return;
    if (live && live.active && live.name === name) return;
    if (!(await mayLeave(live))) {
      paintChoice(live.name);
      return;
    }
    if (focusTab && tabs.get(name)) tabs.get(name).focus();
    await render(name, {});
  }

  function syncList() {
    if (!parts) return;
    for (const name of names()) {
      if (tabs.has(name)) continue;
      const tab = el("button", {
        class: "tab adv-tab",
        type: "button",
        id: "adv-tab-" + slug(name),
        role: "tab",
        tabindex: -1,
        text: name,
        attrs: { "aria-selected": "false", "aria-controls": "advanced-body" },
        on: { click: () => choose(name) },
      });
      tabs.set(name, tab);
      parts.list.append(tab);
      parts.select.append(el("option", { value: name, text: name }));
    }
    paintChoice(live ? live.name : chosen);
  }

  function onListKey(event) {
    const order = names();
    const at = order.indexOf(live ? live.name : chosen);
    let next = null;
    if (event.key === "ArrowDown") next = order[(at + 1) % order.length];
    else if (event.key === "ArrowUp") next = order[(at - 1 + order.length) % order.length];
    else if (event.key === "Home") next = order[0];
    else if (event.key === "End") next = order[order.length - 1];
    if (next === null) return;
    event.preventDefault();
    choose(next, { focusTab: true });
  }

  function mount(root) {
    const select = AW.$("advanced-section") || el("select", { id: "advanced-section" });
    const body = AW.$("advanced-body") || el("div", { id: "advanced-body" });
    select.hidden = false;
    select.removeAttribute("aria-label");
    body.setAttribute("role", "tabpanel");
    body.classList.add("adv-body");
    const list = el("div", { class: "tabs vertical adv-list", role: "tablist", attrs: { "aria-orientation": "vertical", "aria-label": "Advanced sections" } });
    list.addEventListener("keydown", onListKey);
    select.addEventListener("change", () => choose(select.value));
    const pick = el("div", { class: "field adv-pick" }, el("label", { class: "field-label", for: "advanced-section", text: "Section" }), select);
    root.replaceChildren(
      el("h1", { class: "title", id: "advanced-title", tabindex: -1, text: "Advanced" }),
      el("p", { class: "lede", text: "Tools, the model library, agent settings and test runs." }),
      el("div", { class: "adv-layout" }, el("nav", { class: "adv-nav", attrs: { "aria-label": "Advanced sections" } }, list), el("div", { class: "adv-main" }, pick, body)),
    );
    parts = { list, select, body };
    syncList();
  }

  AW.advanced = {
    section(name, renderFn) {
      if (typeof name !== "string" || !name || typeof renderFn !== "function") return;
      renders.set(name, renderFn);
      syncList();
      if (parts && live && live.active && live.name === name && !parts.body.querySelector(":scope > :not(.empty)")) render(name, {});
    },
    sections: names,
    current: () => (live && live.active ? live.name : chosen),
    open: (name, params) => AW.router.go("advanced", { ...(params || {}), section: name }),
    track(host, key, { onDone } = {}) {
      const record = key ? AW.jobs.find(key) : null;
      if (!record || !host) return null;
      ui.clearError(host);
      for (const old of host.querySelectorAll(":scope > .task")) old.remove();
      const row = ui.taskRow(record.handle);
      host.append(row.el);
      let timer = null;
      const off = AW.jobs.on((rec, kind) => {
        if (rec.handle !== record.handle || kind === "started" || kind === "progress") return;
        off();
        row.destroy();
        if (host.isConnected) {
          if (rec.state === "cancelled") {
            const notice = ui.showError(host, rec.error || { message: "Cancelled.", code: "cancelled" });
            timer = setTimeout(() => notice.remove(), 4000);
          } else if (rec.state === "failed" || rec.state === "interrupted") {
            const value = rec.value && typeof rec.value.message === "string" ? { message: rec.value.message } : null;
            ui.showError(host, rec.error || value || { message: rec.title + " failed." });
          }
        }
        if (onDone) onDone(rec);
      });
      return {
        handle: record.handle,
        destroy() {
          off();
          clearTimeout(timer);
          row.destroy();
        },
      };
    },
    info({ force = false } = {}) {
      if (!force && info && (info.pending || Date.now() - info.at < INFO_MS)) return info.promise;
      const entry = { at: Date.now(), pending: true, promise: null };
      entry.promise = AW.request("studio-info").then(
        (value) => {
          entry.pending = false;
          entry.at = Date.now();
          return value;
        },
        (thrown) => {
          if (info === entry) info = null;
          throw thrown;
        },
      );
      info = entry;
      return entry.promise;
    },
  };

  AW.router.register("advanced", {
    title: "Advanced",
    mount,
    async show(params) {
      const asked = params && known(params.section) ? params.section : null;
      const name = asked || chosen;
      if (live && live.active) {
        const deep = params && (params.tool || params.focus || params.select);
        if (live.name === name && !deep && dirty(live)) return;
        if (live.name !== name && !(await mayLeave(live))) {
          paintChoice(live.name);
          return;
        }
      }
      await render(name, params || {});
    },
    hide() {
      end(live);
    },
    leave() {
      return mayLeave(live);
    },
  });

  AW.commands.provide(() =>
    names().map((name) => ({
      id: "advanced." + slug(name),
      title: "Advanced: " + name,
      group: "Go to",
      keywords: "advanced settings " + name.toLowerCase(),
      run: () => AW.router.go("advanced", { section: name }),
    })),
  );
})();
