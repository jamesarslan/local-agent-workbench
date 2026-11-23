// Project switcher: the popover under #project-switch and the recent-folder list it shares with the setup guide (ui.md 5.8).
(function () {
  "use strict";
  const AW = (window.AW = window.AW || {});
  const el = AW.el;
  let pop = null;
  let rows = [];
  let active = -1;
  const ticket = AW.ticket();

  const absolute = (text) => /^(\/|~[\\/]|[A-Za-z]:[\\/]|\\\\)/.test(text);
  const same = (a, b) => Boolean(a) && Boolean(b) && String(a) === String(b);

  const SOURCE_WORDS = { "Managed local": "Local model", "Native login": "Agent account" };
  function summary(row) {
    return [row.harness, SOURCE_WORDS[row.provider] || row.provider, row.model_label].filter(Boolean).join(" · ");
  }
  function runningIn(row) {
    return (AW.state.sessions || []).filter((session) => ["embedded", "native", "remote"].includes(session.kind) && (same(session.workspace, row.path) || same(session.workspace, row.key))).length;
  }

  function option(row, index, onChoose) {
    const current = same(row.path, AW.project.current()) || same(row.key, AW.project.current());
    const running = runningIn(row);
    const node = el(
      "div",
      { class: "switch-option" + (current ? " current" : ""), role: "option", id: "project-option-" + index, attrs: { "aria-selected": "false" } },
      el("span", { class: "switch-name", text: AW.fmt.folder(row.path) || row.path }),
      el("span", { class: "switch-choices", text: row.exists === false ? "Folder not found" : summary(row) }),
      running ? el("span", { class: "switch-running" }, AW.ui.mark("ok"), running + " running") : el("span"),
      el("span", { class: "switch-path", title: AW.fmt.path(row.path), text: AW.fmt.middle(AW.fmt.path(row.path), 68) }),
    );
    if (current) node.append(el("span", { class: "sr-only", text: ", current project" }));
    node.addEventListener("click", () => onChoose(row));
    return node;
  }

  function forgetButton(row, after) {
    return AW.ui.button("Forget", {
      small: true,
      pending: "Forgetting…",
      onClick: () => AW.request("folder-forget", { workspace: row.key || row.path }).then(after),
    });
  }

  function renderList(host, list, { onChoose, onForget, emptyText }) {
    host.replaceChildren();
    if (!list.length) {
      host.append(el("p", { class: "muted switch-empty", text: emptyText }));
      return;
    }
    list.forEach((row, index) => {
      const line = el("div", { class: "switch-row", role: "presentation" }, option(row, index, onChoose));
      if (row.exists === false) line.append(forgetButton(row, onForget));
      host.append(line);
    });
  }

  function failure(host, error, retry) {
    host.replaceChildren(AW.ui.errorNotice(error, { retry }));
  }

  function setError(text) {
    const node = AW.$("project-error");
    node.hidden = !text;
    node.textContent = text ? AW.fmt.paths(text) : "";
    if (text) AW.$("workspace").setAttribute("aria-invalid", "true");
    else AW.$("workspace").removeAttribute("aria-invalid");
  }

  function setActive(index) {
    const options = [...AW.$("project-list").querySelectorAll('[role="option"]')];
    active = options.length ? Math.max(0, Math.min(index, options.length - 1)) : -1;
    options.forEach((node, at) => node.setAttribute("aria-selected", String(at === active)));
    const field = AW.$("workspace");
    if (active >= 0) {
      field.setAttribute("aria-activedescendant", options[active].id);
      options[active].scrollIntoView({ block: "nearest" });
    } else field.removeAttribute("aria-activedescendant");
  }

  function filtered() {
    const query = AW.$("workspace").value.trim().toLowerCase();
    if (!query) return rows;
    return rows.filter((row) => [row.path, AW.fmt.path(row.path), summary(row)].some((value) => String(value).toLowerCase().includes(query)));
  }

  function paint() {
    const list = filtered();
    const typed = AW.$("workspace").value.trim();
    renderList(AW.$("project-list"), list, {
      onChoose: choose,
      onForget: load,
      emptyText: absolute(typed) ? "Press Enter to open this folder." : rows.length ? "No folder matches. Paste a full path or choose Browse." : "No folders yet. Choose Browse to pick a project folder.",
    });
    setActive(absolute(typed) ? -1 : 0);
  }

  async function load() {
    const mine = ticket.next();
    const host = AW.$("project-list");
    if (!rows.length) host.replaceChildren(AW.ui.skeleton(3, "recent folders"));
    try {
      await AW.store.refresh("folders");
      if (!ticket.current(mine)) return;
      rows = AW.project.known();
      paint();
    } catch (thrown) {
      if (ticket.current(mine)) failure(host, thrown, load);
    }
    if (pop) pop.place();
  }

  async function countProfiles() {
    const link = AW.$("folder-profiles");
    link.hidden = true;
    const workspace = AW.project.current();
    if (!workspace) return;
    try {
      const names = await AW.request("project-list", { workspace });
      if (!Array.isArray(names) || !names.length || AW.project.current() !== workspace) return;
      link.textContent = names.length === 1 ? "1 saved project profile →" : names.length + " saved project profiles →";
      link.hidden = false;
      if (pop) pop.place();
    } catch {
      link.hidden = true;
    }
  }

  function close() {
    if (pop) pop.close(true);
  }

  async function choose(row) {
    if (row.exists === false) {
      setError("That folder does not exist.");
      return;
    }
    await open(row.path);
  }

  async function open(path) {
    setError(null);
    try {
      await AW.project.select(path);
      close();
    } catch (thrown) {
      setError(AW.asError(thrown).message);
    }
  }

  async function openTyped(path) {
    setError(null);
    if (path.startsWith("~")) {
      await open(path);
      return;
    }
    try {
      const state = await AW.request("onboarding-state", { workspace: path, sections: [] });
      if (!state.workspace || !state.workspace.exists) {
        setError("That folder does not exist.");
        if (!filtered().length) AW.$("project-list").replaceChildren(el("p", { class: "muted switch-empty", text: "Check the path, or choose Browse to pick the folder." }));
        return;
      }
      await open(state.workspace.path || path);
    } catch (thrown) {
      setError(AW.asError(thrown).message);
    }
  }

  function onKey(event) {
    const list = filtered();
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      if (list.length) setActive(active < 0 ? 0 : (active + (event.key === "ArrowDown" ? 1 : -1) + list.length) % list.length);
    } else if (event.key === "Enter") {
      event.preventDefault();
      const typed = AW.$("workspace").value.trim();
      if (absolute(typed)) openTyped(AW.fmt.expand(typed));
      else if (active >= 0 && list[active]) choose(list[active]);
    }
  }

  function show() {
    const button = AW.$("project-switch");
    if (AW.ui.popoverFor(button)) return;
    const field = AW.$("workspace");
    field.value = "";
    setError(null);
    pop = AW.ui.popover(button, null, { width: 560, element: AW.$("project-pop") });
    pop.onClose = () => {
      pop = null;
      ticket.next();
    };
    field.focus();
    load();
    countProfiles();
  }

  function paintButton() {
    const path = AW.project.current() || AW.form.get().workspace;
    const button = AW.$("project-switch");
    const name = path ? AW.project.name(path) : "Choose project";
    if (button.textContent !== name) button.textContent = name;
    if (path) button.title = AW.fmt.path(path);
    else button.removeAttribute("title");
  }

  AW.projectSwitch = {
    open: show,
    close,
    list(host, { onChoose, filter = () => true } = {}) {
      const mine = AW.ticket();
      let gone = false;
      host.classList.add("switch-list");
      host.setAttribute("role", "listbox");
      if (!host.hasAttribute("aria-label")) host.setAttribute("aria-label", "Recent project folders");
      async function refresh() {
        const at = mine.next();
        if (!host.children.length) host.replaceChildren(AW.ui.skeleton(3, "recent folders"));
        try {
          await AW.store.refresh("folders");
          if (gone || !mine.current(at)) return;
          renderList(host, AW.project.known().filter(filter), {
            onChoose: (row) => (onChoose ? onChoose(row) : AW.project.select(row.path)),
            onForget: refresh,
            emptyText: "No folders yet. Choose Browse to pick a project folder.",
          });
          for (const node of host.querySelectorAll('[role="option"]')) {
            node.tabIndex = 0;
            node.addEventListener("keydown", (event) => {
              if (event.key === "Enter" || event.key === " ") {
                event.preventDefault();
                node.click();
              }
            });
          }
        } catch (thrown) {
          if (!gone && mine.current(at)) failure(host, thrown, refresh);
        }
      }
      refresh();
      return {
        refresh,
        destroy() {
          gone = true;
          host.replaceChildren();
        },
      };
    },
  };

  AW.shell.register(() => {
    const field = AW.$("workspace");
    AW.$("project-switch").addEventListener("click", () => (AW.ui.popoverFor(AW.$("project-switch")) ? close() : show()));
    field.addEventListener("input", () => {
      setError(null);
      paint();
    });
    field.addEventListener("keydown", onKey);
    AW.$("browse").addEventListener("mousedown", (event) => event.preventDefault());
    AW.$("browse").addEventListener("click", async () => {
      setError(null);
      try {
        if (await AW.project.pick()) close();
      } catch (thrown) {
        setError(AW.asError(thrown).message);
      }
    });
    AW.$("folder-profiles").addEventListener("click", () => {
      close();
      AW.router.go("profiles");
    });
    AW.store.on(["folder", "config"], paintButton);
    AW.form.on(paintButton);
    paintButton();
  });

  AW.fix.register("project.pick", () => show());
  AW.keys.register({ combo: "mod+o", inTerminal: "mac", run: show });
  AW.commands.register({ id: "project.open", title: "Open another folder…", group: "Projects", hint: AW.platform.mod + " O", run: show });
  AW.commands.provide(() => {
    const out = [];
    for (const row of AW.project.known()) {
      if (row.exists === false || same(row.path, AW.project.current()) || same(row.key, AW.project.current())) continue;
      const name = AW.fmt.folder(row.path);
      const detail = summary(row);
      const label = name + (detail ? " · " + detail : "");
      out.push({ id: "project.switch:" + row.key, title: "Switch to " + label, group: "Projects", keywords: row.path, run: () => AW.project.select(row.path) });
      out.push({
        id: "project.launch:" + row.key,
        title: "Launch in " + label,
        group: "Projects",
        keywords: row.path,
        run: async () => {
          await AW.project.select(row.path);
          return AW.launch.quick(AW.$("launch-quick"));
        },
      });
    }
    return out;
  });
})();
