// View registry and routing, commands, fixes, keyboard shortcuts and the shell start list (ui.md 2.5, 2.6, 9.3).
(function () {
  "use strict";
  const AW = (window.AW = window.AW || {});
  const el = AW.el;

  const views = new Map();
  const aliases = new Map();
  const listeners = new Set();
  const history = [];
  const ticket = AW.ticket();
  let current = null;

  function resolve(idOrAlias, params) {
    const alias = aliases.get(idOrAlias);
    return alias ? { id: alias.id, params: { ...alias.params, ...(params || {}) } } : { id: idOrAlias, params: { ...(params || {}) } };
  }

  function rootOf(id) {
    let root = AW.$(id + "-view");
    if (!root) {
      root = el("section", { class: "view", id: id + "-view", hidden: true, attrs: { "aria-labelledby": id + "-title" } });
      AW.$("view").insertBefore(root, AW.$("boot-screen"));
    }
    return root;
  }

  function heading(root) {
    const h1 = root.querySelector("h1");
    if (h1 && !h1.hasAttribute("tabindex")) h1.tabIndex = -1;
    return h1;
  }

  function errorHost(root) {
    let host = root.querySelector(":scope > .view-error");
    if (!host) {
      host = el("div", { class: "view-error" });
      const h1 = heading(root);
      if (h1 && h1.parentNode === root) h1.after(host);
      else root.prepend(host);
    }
    return host;
  }

  function backLink(root, returnTo) {
    for (const old of root.querySelectorAll(":scope > .back-link")) old.remove();
    const target = returnTo && views.get(resolve(returnTo).id);
    if (!target) return;
    const link = el("button", { class: "link back-link", type: "button", text: "‹ Back to " + target.title });
    link.addEventListener("click", () => AW.router.go(returnTo));
    const h1 = heading(root);
    if (h1 && h1.parentNode === root) h1.after(link);
    else root.prepend(link);
  }

  function paintShell(id, view) {
    for (const nav of document.querySelectorAll("#rail .nav[aria-current]")) nav.removeAttribute("aria-current");
    const nav = AW.$(view.nav || "nav-" + id);
    if (nav) nav.setAttribute("aria-current", "page");
    const crumb = AW.$("crumb");
    if (crumb) crumb.textContent = view.title;
    document.title = view.title + " · Agent Workbench";
    document.body.classList.toggle("takeover", Boolean(view.takeover));
  }

  async function show(id, params, record) {
    const view = views.get(id);
    if (!view) return false;
    const mine = ticket.next();
    if (current && current.id !== id) {
      const leaving = views.get(current.id);
      if (leaving && leaving.leave) {
        let stay = false;
        try {
          stay = (await leaving.leave()) === false;
        } catch {
          stay = false;
        }
        if (stay || !ticket.current(mine)) return false;
      }
      if (leaving && leaving.hide) {
        try {
          leaving.hide();
        } catch (thrown) {
          console.error(thrown);
        }
      }
    }
    const root = rootOf(id);
    for (const other of AW.$("view").querySelectorAll(":scope > .view")) other.hidden = other !== root;
    AW.$("boot-failed").hidden = true;
    if (current && record && (current.id !== id || JSON.stringify(current.params) !== JSON.stringify(params))) {
      history.push(current);
      if (history.length > 30) history.shift();
    }
    current = { id, params };
    paintShell(id, view);
    let failure = null;
    if (!view.mounted) {
      view.mounted = true;
      try {
        if (view.mount) view.mount(root);
      } catch (thrown) {
        failure = thrown;
      }
    }
    backLink(root, params.returnTo);
    for (const fn of [...listeners]) {
      try {
        fn({ id, params, title: view.title, takeover: Boolean(view.takeover) });
      } catch (thrown) {
        console.error(thrown);
      }
    }
    if (!params.focus) {
      const h1 = heading(root);
      if (h1) h1.focus({ preventScroll: true });
      AW.$("view").scrollTop = 0;
    }
    if (!failure) {
      try {
        if (view.show) await view.show(params);
      } catch (thrown) {
        failure = thrown;
      }
    }
    if (!ticket.current(mine)) return true;
    const host = errorHost(root);
    if (failure) AW.ui.showError(host, failure, { retry: () => AW.router.go(id, params) });
    else AW.ui.clearError(host);
    const focusTarget = typeof params.focus === "string" ? AW.$(params.focus) : null;
    if (focusTarget && root.contains(focusTarget)) focusTarget.focus();
    return true;
  }

  AW.router = {
    register(id, view) {
      views.set(id, { takeover: false, ...view, mounted: false });
    },
    alias(name, id, params) {
      aliases.set(name, { id, params: params || {} });
    },
    go(idOrAlias, params) {
      const target = resolve(idOrAlias, params);
      return show(target.id, target.params, true);
    },
    back() {
      const previous = history.pop();
      if (!previous) return Promise.resolve(false);
      return show(previous.id, previous.params, false);
    },
    current: () => (current ? { id: current.id, params: current.params } : { id: "", params: {} }),
    on(fn) {
      listeners.add(fn);
      return () => listeners.delete(fn);
    },
    views: () => [...views].map(([id, view]) => ({ id, title: view.title, nav: view.nav || "nav-" + id, takeover: view.takeover })),
  };

  AW.router.alias("plugins", "project", { tab: "mcp", focus: "plugins" });
  AW.router.alias("profiles", "project", { tab: "profiles" });
  AW.router.alias("setup", "advanced", { section: "Setup" });
  AW.router.alias("doctor", "advanced", { section: "Setup", focus: "doctor" });
  AW.router.alias("models", "advanced", { section: "Model library" });

  const commands = new Map();
  const providers = new Set();
  const usable = (command) => {
    try {
      return !command.when || Boolean(command.when());
    } catch {
      return false;
    }
  };
  AW.commands = {
    register(command) {
      commands.set(command.id, command);
    },
    provide(fn) {
      providers.add(fn);
    },
    all() {
      const found = [...commands.values()];
      for (const fn of providers) {
        try {
          found.push(...(fn() || []));
        } catch (thrown) {
          console.error(thrown);
        }
      }
      return found.filter(usable).map((command) => ({ ...command, title: typeof command.title === "function" ? command.title() : command.title }));
    },
    run(id) {
      const command = AW.commands.all().find((item) => item.id === id);
      if (!command) return Promise.resolve(false);
      return Promise.resolve()
        .then(() => command.run())
        .catch((thrown) => {
          AW.ui.toast(AW.asError(thrown).message, { kind: "danger" });
          return false;
        });
    },
  };
  AW.commands.provide(() =>
    AW.router
      .views()
      .filter((view) => !view.takeover && AW.$(view.nav))
      .map((view) => ({ id: "go." + view.id, title: "Go to " + view.title, group: "Go to", hint: view.id === "launch" ? AW.platform.mod + " L" : undefined, run: () => AW.router.go(view.id) })),
  );

  const fixes = new Map();
  AW.fix = {
    register(name, fn) {
      fixes.set(name, fn);
    },
    has: (name) => fixes.has(name),
    run(name, arg, ctx) {
      const fn = fixes.get(name);
      if (!fn) return Promise.reject({ message: "This fix is not available yet." });
      return Promise.resolve().then(() => fn(arg, ctx || {}));
    },
  };
  const from = () => ({ returnTo: AW.router.current().id });
  AW.fix.register("go.accounts", (arg) => AW.router.go("accounts", { ...from(), select: arg }));
  AW.fix.register("go.machines", (arg) => AW.router.go("machines", { ...from(), select: arg, host: arg }));
  AW.fix.register("go.models", () => AW.router.go("models", from()));
  AW.fix.register("go.doctor", () => AW.router.go("doctor", from()));
  AW.fix.register("go.setup", (arg) => AW.router.go("setup", { ...from(), tool: arg }));
  AW.fix.register("go.project", () => AW.router.go("project", from()));
  AW.fix.register("go.mcp", () => AW.router.go("project", { ...from(), tab: "mcp" }));
  AW.fix.register("go.kit", () => AW.router.go("project", { ...from(), tab: "kit" }));
  AW.fix.register("go.instructions", () => AW.router.go("project", { ...from(), tab: "instructions" }));
  AW.fix.register("agent.use", (arg) => AW.form.set({ harness: arg }));
  AW.fix.register("source.use", (arg) => {
    if (arg === "Native login" || arg === "Managed local") return AW.form.set({ provider: arg, connection_id: "" });
    const row = (AW.state.accounts || []).find((account) => account.connection_id === arg);
    return AW.form.set(row ? { provider: row.provider, connection_id: row.connection_id } : { connection_id: arg });
  });
  AW.fix.register("tool.off", (arg) => AW.form.set({ [arg]: false }));
  AW.fix.register("focus.source", () => AW.router.go("launch", { focus: "source" }));

  const keys = [];
  function comboOf(event) {
    const mod = AW.platform.mac ? event.metaKey : event.ctrlKey;
    const other = AW.platform.mac ? event.ctrlKey : event.metaKey;
    if (other) return "";
    let key = event.key.length === 1 ? event.key.toLowerCase() : event.key.toLowerCase().replace(/^arrow/, "");
    if (event.code && /^Digit\d$/.test(event.code)) key = event.code.slice(5);
    if (event.code && /^Key[A-Z]$/.test(event.code) && (mod || event.altKey)) key = event.code.slice(3).toLowerCase();
    if (["meta", "control", "shift", "alt"].includes(key)) return "";
    return (mod ? "mod+" : "") + (event.altKey ? "alt+" : "") + (event.shiftKey ? "shift+" : "") + key;
  }
  const order = (combo) => {
    const parts = combo.toLowerCase().split("+");
    const key = parts.pop();
    return ["mod", "alt", "shift"].filter((part) => parts.includes(part)).map((part) => part + "+").join("") + key;
  };
  const TEXT_FIELD = 'textarea, [contenteditable]:not([contenteditable="false"]), input:not([type="checkbox"]):not([type="radio"]):not([type="button"]):not([type="range"])';
  AW.keys = {
    // inDialog: true keeps the binding live while a modal dialog is open (the command palette's own key); every other binding waits for the dialog to close.
    register(binding) {
      keys.push({ inTerminal: false, inDialog: false, ...binding, combo: order(binding.combo) });
    },
    list: () => keys.map((binding) => ({ combo: binding.combo, inTerminal: binding.inTerminal })),
  };
  window.addEventListener(
    "keydown",
    (event) => {
      const combo = comboOf(event);
      if (!combo) return;
      const target = event.target && event.target.closest ? event.target : null;
      const terminal = Boolean(target && target.closest("#term-screen"));
      if (AW.platform.mac && combo.startsWith("alt+") && target && target.matches(TEXT_FIELD)) return;
      const modal = Boolean(document.querySelector("dialog:modal"));
      for (const binding of keys) {
        if (binding.combo !== combo) continue;
        if (modal && !binding.inDialog) continue;
        if (terminal && !(binding.inTerminal === true || (binding.inTerminal === "mac" && AW.platform.mac))) continue;
        event.preventDefault();
        event.stopPropagation();
        Promise.resolve()
          .then(() => binding.run(event))
          .catch((thrown) => AW.ui.toast(AW.asError(thrown).message, { kind: "danger" }));
        return;
      }
    },
    true,
  );
  AW.keys.register({ combo: "f6", inTerminal: true, run: () => AW.focus.region(1) });
  AW.keys.register({ combo: "shift+f6", inTerminal: true, run: () => AW.focus.region(-1) });
  AW.keys.register({ combo: "alt+left", inTerminal: false, run: () => AW.router.back() });
  if (AW.platform.mac) AW.keys.register({ combo: "mod+[", inTerminal: "mac", run: () => AW.router.back() });

  const starters = [];
  let started = false;
  AW.shell = {
    register(fn) {
      if (started) fn();
      else starters.push(fn);
    },
    init() {
      if (started) return;
      started = true;
      for (const fn of starters.splice(0)) {
        try {
          fn();
        } catch (thrown) {
          console.error(thrown);
        }
      }
    },
    busy(on) {
      const view = AW.$("view");
      if (on) view.setAttribute("aria-busy", "true");
      else view.removeAttribute("aria-busy");
      AW.shell.lock(on);
    },
    lock(on) {
      for (const node of document.querySelectorAll("#rail-nav .nav, #launch-quick, #launch, #save")) {
        if (on && node.getAttribute("aria-disabled") !== "true") {
          node.setAttribute("aria-disabled", "true");
          node.dataset.starting = "true";
        } else if (!on && node.dataset.starting) {
          node.removeAttribute("aria-disabled");
          delete node.dataset.starting;
        }
      }
    },
  };
  AW.shell.register(() => {
    AW.$("skip-link").addEventListener("click", () => AW.focus.enter(AW.$("view")));
  });
  document.addEventListener("DOMContentLoaded", () => AW.shell.init());
})();
