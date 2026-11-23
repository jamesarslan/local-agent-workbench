// Choose a model dialog (AW.modelPicker, ui.md 5.4): the #model-picker skeleton in index.html, filled from account-models.
// Beyond ui.md 9.8: open() also takes `current` (the model to mark), and AW.modelPicker.clear() empties the five-minute cache.
(function () {
  "use strict";
  const AW = window.AW;
  const el = AW.el;
  const TTL = 300000;
  const cache = new Map();
  const ticket = AW.ticket();
  let parts = null;
  let session = null;

  function wire() {
    if (parts) return parts;
    const dialog = AW.$("model-picker");
    const filter = AW.$("model-filter");
    const count = AW.$("picker-count");
    const list = AW.$("picker-list");
    list.removeAttribute("role");
    list.classList.add("accounts-picker-list");
    const refresh = AW.ui.button("Refresh list", { small: true, onClick: () => session && session.load(true) });
    const search = el("div", { class: "inputrow accounts-picker-search" });
    filter.before(search);
    search.append(filter, refresh);
    const status = el("div", { class: "accounts-picker-status" });
    count.after(status);
    filter.addEventListener("input", () => session && session.render());
    filter.addEventListener("keydown", (event) => {
      const first = list.querySelector("button");
      if (event.key === "ArrowDown" && first) {
        event.preventDefault();
        first.focus();
      } else if (event.key === "Enter" && first) {
        event.preventDefault();
        first.click();
      }
    });
    list.addEventListener("keydown", (event) => {
      const buttons = [...list.querySelectorAll("button")];
      const at = buttons.indexOf(document.activeElement);
      if (at < 0) return;
      let next = null;
      if (event.key === "ArrowDown") next = buttons[at + 1] || null;
      else if (event.key === "ArrowUp") next = at === 0 ? filter : buttons[at - 1];
      else if (event.key === "Home") next = buttons[0];
      else if (event.key === "End") next = buttons[buttons.length - 1];
      if (!next) return;
      event.preventDefault();
      next.focus();
    });
    parts = { dialog, filter, count, list, refresh, status, api: AW.ui.dialog(dialog) };
    return parts;
  }

  function details(options) {
    const scan = AW.state.scan;
    const row = (AW.state.accounts || []).find((item) => item.connection_id === options.connection_id) || null;
    const base = (row && row.endpoint) || options.endpoint || "";
    const found = ((scan && scan.endpoints) || []).find((item) => (options.connection_id && item.connection_id === options.connection_id) || (base && item.base === base));
    const map = new Map();
    for (const m of (found && found.models) || []) if (m && typeof m.id === "string") map.set(m.id, m);
    return map;
  }

  function meta(id, info) {
    const words = [];
    if (id === "openrouter/auto") words.push("automatic routing");
    const context = info ? AW.fmt.context(info.context) : "";
    if (context) words.push(context + " context");
    if (info && info.loaded === true) words.push("loaded");
    return words.join(" · ");
  }

  function open(options = {}) {
    const provider = options.provider || "";
    if (provider === AW.sources.LOCAL) {
      AW.router.go("models");
      return Promise.resolve(null);
    }
    const p = wire();
    const name = options.name || AW.sources.label(provider) || "this connection";
    const key = JSON.stringify([options.connection_id || "", provider, options.endpoint || ""]);
    const data = { provider };
    if (options.connection_id) data.connection_id = options.connection_id;
    if (options.endpoint) data.endpoint = options.endpoint;
    let models = null;
    let info = details(options);

    function show(rows) {
      models = rows;
      p.status.replaceChildren();
      p.refresh.removeAttribute("aria-disabled");
      render();
    }

    function render() {
      if (!models) return;
      const query = p.filter.value.trim().toLowerCase();
      const matches = query ? models.filter((id) => id.toLowerCase().includes(query)) : models;
      p.count.textContent = !models.length ? "" : query ? matches.length + " of " + AW.sources.countText(models.length, "model", "models") : AW.sources.countText(models.length, "model", "models");
      p.list.replaceChildren();
      if (!models.length) {
        p.list.append(AW.ui.empty("No models were returned by " + name + ".", [{ label: "Try again", run: () => load(true) }]));
        return;
      }
      if (!matches.length) {
        p.list.append(el("p", { class: "muted accounts-picker-none", text: "No model matches “" + p.filter.value.trim() + "”." }));
        return;
      }
      for (const id of matches) {
        const words = meta(id, info.get(id));
        const current = id === options.current;
        const button = el(
          "button",
          { type: "button", class: "accounts-pick", attrs: { "aria-current": current ? "true" : null } },
          el("span", { class: "accounts-pick-id", text: id }),
          words || current ? el("span", { class: "accounts-pick-meta", text: [words, current ? "current" : ""].filter(Boolean).join(" · ") }) : null,
        );
        button.addEventListener("click", () => p.api.close(id));
        p.list.append(button);
      }
    }

    async function load(force) {
      const held = cache.get(key);
      if (!force && held && Date.now() - held.at < TTL) {
        show(held.list);
        return;
      }
      if (force) {
        cache.delete(key);
        if (AW.store.age("scan") > 10000) AW.store.refresh("scan").then(() => (info = details(options)), () => {});
      }
      const mine = ticket.next();
      models = null;
      p.count.textContent = "Loading " + name + " models…";
      p.refresh.setAttribute("aria-disabled", "true");
      p.status.replaceChildren();
      p.list.replaceChildren(AW.ui.skeleton(4, name + " models"));
      try {
        const rows = await AW.request("account-models", data);
        if (!ticket.current(mine)) return;
        const clean = (Array.isArray(rows) ? rows : []).filter((id) => typeof id === "string" && id);
        cache.set(key, { list: clean, at: Date.now() });
        show(clean);
      } catch (thrown) {
        if (!ticket.current(mine)) return;
        p.count.textContent = "";
        p.list.replaceChildren();
        p.refresh.removeAttribute("aria-disabled");
        AW.ui.showError(p.status, thrown, { retry: () => load(true) });
      }
    }

    session = { load, render };
    p.filter.value = "";
    const answer = p.api.opened().then((value) => {
      ticket.next();
      session = null;
      return typeof value === "string" && value ? value : null;
    });
    p.api.open();
    load(false);
    p.filter.focus();
    return answer;
  }

  AW.modelPicker = {
    open,
    clear: () => cache.clear(),
  };
})();
