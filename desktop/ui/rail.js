// Rail: navigation, attention marks, the theme switch, the workstation label and guide mode (ui.md 2.2, 8.1, 9.7).
(function () {
  "use strict";
  const AW = (window.AW = window.AW || {});
  const el = AW.el;
  const root = document.documentElement;
  const CHOICES = ["light", "dark", "system"];
  const media = window.matchMedia("(prefers-color-scheme: dark)");
  const themeListeners = new Set();

  function resolved(choice) {
    return choice === "system" ? (media.matches ? "dark" : "light") : choice;
  }
  function paintTheme() {
    const button = AW.$("theme-switch");
    if (!button) return;
    const dark = root.dataset.theme === "dark";
    button.querySelector(".nav-label").textContent = dark ? "Light mode" : "Dark mode";
    button.querySelector("use").setAttribute("href", dark ? "#i-sun" : "#i-moon");
    titles();
  }
  AW.theme = {
    get() {
      const choice = CHOICES.includes(root.dataset.themeChoice) ? root.dataset.themeChoice : "light";
      return { choice, resolved: root.dataset.theme === "dark" ? "dark" : "light" };
    },
    set(choice) {
      const next = CHOICES.includes(choice) ? choice : "light";
      AW.storage.set("appearance", next);
      root.dataset.themeChoice = next;
      root.dataset.theme = resolved(next);
      paintTheme();
      for (const fn of [...themeListeners]) fn(AW.theme.get());
    },
    on(fn) {
      themeListeners.add(fn);
      return () => themeListeners.delete(fn);
    },
  };
  media.addEventListener("change", () => {
    if (root.dataset.themeChoice === "system") AW.theme.set("system");
  });

  const narrow = window.matchMedia("(max-width: 1030px)");
  function titles() {
    for (const nav of document.querySelectorAll("#rail .nav")) {
      const label = nav.querySelector(".nav-label");
      if (narrow.matches && label) nav.title = label.textContent;
      else nav.removeAttribute("title");
    }
  }

  const marks = new Map();
  function mark(navId, kind) {
    const nav = AW.$(navId);
    if (!nav) return;
    const next = kind === "warn" || kind === "down" ? kind : null;
    if ((marks.get(navId) || null) === next) return;
    marks.set(navId, next);
    for (const old of nav.querySelectorAll(".nav-flag, .nav-flag + .sr-only")) old.remove();
    if (next) nav.append(el("span", { class: "nav-flag " + next }), el("span", { class: "sr-only", text: ", needs attention" }));
  }

  function paintMarks() {
    const state = AW.state;
    const ready = AW.readiness.current();
    mark("nav-launch", state.config && !ready.ok ? "warn" : null);
    mark("nav-overview", (state.exits || []).some((exit) => exit && exit.code !== 0) ? "warn" : null);
    const consent = state.consent;
    const warnings = state.resolve && Array.isArray(state.resolve.warnings) ? state.resolve.warnings : [];
    mark("nav-project", (consent && consent.needed && !consent.granted) || warnings.length ? "warn" : null);
    mark("nav-accounts", (state.accounts || []).some((row) => row && row.ready === false) ? "warn" : null);
    mark("nav-machines", (state.forwards || []).some((row) => row && row.alive === false) ? "down" : null);
  }

  function paintMachine() {
    const config = AW.state.config;
    const hardware = AW.state.hardware;
    AW.$("machine-label").textContent = AW.storage.get("machineLabel", "") || "Your workstation";
    const gpu = hardware && Array.isArray(hardware.gpus) ? hardware.gpus[hardware.primary] : null;
    const name = AW.$("machine-name");
    name.textContent = (gpu && gpu.name) || (hardware && (hardware.cpu || hardware.label)) || (config && config.gpu) || (AW.store.error("config") ? "Hardware not detected" : "Detecting hardware…");
    name.title = (hardware && hardware.label) || "";
  }

  function paintGuide() {
    const progress = AW.state.progress;
    const open = Boolean(progress && (progress.open ?? (progress.progress && progress.progress.open)));
    AW.$("nav-guide").querySelector(".nav-label").textContent = open ? "Continue setup" : "Setup guide";
    titles();
  }

  function addGallery() {
    if (!AW.router.views().some((view) => view.id === "dev") || AW.$("nav-dev")) return;
    const nav = el("button", { class: "nav", type: "button", id: "nav-dev", data: { route: "dev" } }, AW.ui.icon("more"), el("span", { class: "nav-label", text: "Components" }));
    AW.$("rail-nav").append(nav);
  }

  AW.rail = { mark, refresh: paintMachine };

  AW.shell.register(() => {
    addGallery();
    AW.$("rail-nav").addEventListener("click", (event) => {
      const nav = event.target.closest(".nav[data-route]");
      if (!nav || nav.getAttribute("aria-disabled") === "true") return;
      AW.router.go(nav.dataset.route);
    });
    AW.$("nav-guide").addEventListener("click", () => AW.guide.open());
    AW.$("terminal-toggle").addEventListener("click", () => AW.terminal.toggle());
    AW.$("theme-switch").addEventListener("click", () => AW.theme.set(AW.theme.get().resolved === "dark" ? "light" : "dark"));
    new MutationObserver(paintTheme).observe(root, { attributes: true, attributeFilter: ["data-theme"] });
    narrow.addEventListener("change", titles);
    AW.router.on(({ takeover }) => {
      AW.$("guide-rail").hidden = !takeover;
      addGallery();
    });
    AW.store.on(["config", "hardware"], paintMachine);
    AW.store.on("progress", paintGuide);
    AW.store.on(["config", "exits", "consent", "resolve", "accounts", "forwards"], paintMarks);
    AW.readiness.on(paintMarks);
    paintTheme();
    paintMachine();
    paintGuide();
  });

  const routes = ["launch", "overview", "project", "accounts", "machines", "advanced"];
  routes.forEach((route, index) => AW.keys.register({ combo: "mod+" + (index + 1), inTerminal: "mac", run: () => (AW.$("nav-" + route).getAttribute("aria-disabled") === "true" ? false : AW.router.go(route)) }));
  AW.commands.register({ id: "theme.dark", title: "Switch to dark theme", group: "Actions", when: () => AW.theme.get().resolved !== "dark", run: () => AW.theme.set("dark") });
  AW.commands.register({ id: "theme.light", title: "Switch to light theme", group: "Actions", when: () => AW.theme.get().resolved !== "light", run: () => AW.theme.set("light") });
  AW.commands.register({ id: "guide.open", title: "Open setup guide", group: "Actions", run: () => AW.guide.open() });
})();
