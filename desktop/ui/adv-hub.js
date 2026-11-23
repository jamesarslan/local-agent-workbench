// Advanced, Hugging Face: search repositories, list a repository's weight files and download one into the model folder as a task (ui.md 5.6, 7.1).
(function () {
  "use strict";
  const AW = window.AW;
  const el = AW.el;
  const ui = AW.ui;
  const EMPTY = "Nothing matched. Try a repository name such as owner/model-GGUF.";
  const memory = { query: "", vision: false, rows: null, searched: "", finished: [] };

  const fileName = (row) => String(row.name || "");
  const size = (row) => (Number(row.bytes) > 0 ? AW.fmt.bytes(row.bytes) : AW.fmt.gib(row.gib)) || "size unknown";

  AW.advanced.section("Hugging Face", async (body, ctx) => {
    const info = await AW.advanced.info().catch(() => null);
    if (!ctx.alive()) return;
    const library = info && info.config && typeof info.config.library === "string" ? info.config.library : "";

    const input = el("input", { type: "search", id: "adv-hub-query", value: memory.query, attrs: { autocomplete: "off", spellcheck: "false", placeholder: "owner/model-GGUF or a model name" } });
    const field = ui.field("Repository or search", el("div", { class: "inputrow" }, input), { hint: "A name searches Hugging Face for GGUF models. A repository name such as owner/model-GGUF lists its weight files." });
    const vision = ui.toggle("Include available vision weights", { id: "adv-hub-vision", description: "Also downloads the image-reading file a repository offers next to its model, when it has one.", checked: memory.vision, onChange: (on) => (memory.vision = on) });
    const caption = el("p", { class: "hint adv-caption", attrs: { role: "status" } });
    const list = el("div", { class: "adv-hub-list" });
    const results = el("div", { class: "adv-results" }, caption, list);
    const downloads = el("div", { class: "stack adv-downloads" });
    const tracked = new Map();
    ctx.cleanup(() => tracked.forEach((item) => item.destroy()));

    function finishedNotice(name) {
      return ui.notice("ok", "Downloaded " + name + ". Choose it under Weights in Model library.", {
        actions: [{ label: "Open Model library", run: () => AW.router.go("models") }],
        dismissible: true,
        onDismiss: () => (memory.finished = memory.finished.filter((item) => item !== name)),
      });
    }

    function follow(key, name, row) {
      const host = el("div", { class: "adv-download", data: { jobKey: key } });
      const item = AW.advanced.track(host, key, {
        onDone(record) {
          tracked.delete(key);
          if (record.state === "done") {
            host.remove();
            memory.finished = [name, ...memory.finished.filter((other) => other !== name)].slice(0, 3);
            if (downloads.isConnected) downloads.prepend(finishedNotice(name));
            AW.store.refresh("config").catch(() => {});
          } else if (record.state === "cancelled" && row) {
            const resume = ui.button("Resume download", { small: true, kind: "primary", onClick: () => (host.remove(), start(row, resume)) });
            const message = (record.error && record.error.message) || "The download was cancelled. The part already downloaded is kept.";
            host.replaceChildren(ui.notice("info", message, { actions: [resume], dismissible: true }));
          }
        },
      });
      if (!item) return;
      tracked.set(key, item);
      downloads.append(host);
    }

    function start(row, origin) {
      const name = fileName(row);
      const key = "hub-file:" + row.id;
      const promise = AW.jobs.run("studio-download", { id: row.id, vision: vision.input.checked }, { title: "Downloading " + name, origin, key });
      follow(key, name, row);
      return promise.then(
        () => {},
        () => {},
      );
    }

    async function download(row, origin) {
      const ok = await ui.confirm({ text: "Download " + fileName(row) + " (" + size(row) + ")?", ok: "Download" });
      if (ok && ctx.alive()) return start(row, origin);
    }

    function paint(rows) {
      list.replaceChildren();
      const items = Array.isArray(rows) ? rows : [];
      if (!items.length) {
        caption.textContent = "";
        list.append(ui.empty(EMPTY));
        return;
      }
      const files = items.filter((row) => row && row.id);
      if (files.length) {
        caption.textContent = files.length === 1 ? "One weight file in " + memory.searched + "." : files.length + " weight files in " + memory.searched + ".";
        const rowsRoot = el("div");
        const view = ui.list(rowsRoot, {
          key: (row) => row.id,
          render(row) {
            const handle = ui.row({ title: fileName(row), meta: size(row), actions: [] });
            const button = ui.button("Download", { small: true, onClick: () => download(row, button) });
            button.setAttribute("aria-label", "Download " + fileName(row));
            handle.el.querySelector(".row-actions").append(button);
            handle.el.querySelector(".row-title").classList.add("mono");
            return handle.el;
          },
        });
        view.set(files);
        list.append(rowsRoot);
        return;
      }
      const repos = items.filter((row) => row && typeof row.repo === "string");
      caption.textContent = repos.length === 1 ? "One repository matched. Choose it to list its files." : repos.length + " repositories matched. Choose one to list its files.";
      const rowsRoot = el("div");
      const view = ui.list(rowsRoot, {
        key: (row) => row.repo,
        render(row) {
          const handle = ui.row({ title: row.repo, actions: [] });
          const button = ui.button("Show files", { small: true, onClick: () => ((input.value = row.repo), search(button)) });
          button.setAttribute("aria-label", "Show files in " + row.repo);
          handle.el.querySelector(".row-actions").append(button);
          handle.el.querySelector(".row-title").classList.add("mono");
          return handle.el;
        },
      });
      view.set(repos);
      list.append(rowsRoot);
    }

    const pending = AW.ticket();
    async function search(origin) {
      const query = input.value.trim();
      if (!query) {
        field.setError("Enter a model name or repository.");
        input.focus();
        return;
      }
      field.setError(null);
      const mine = pending.next();
      ui.clearError(results);
      results.classList.add("stale");
      results.setAttribute("aria-busy", "true");
      const ask = AW.request("studio-hub", { query });
      if (origin) ui.pending(origin, origin === searchButton ? "Searching…" : "Loading files…", ask);
      let rows;
      try {
        rows = await ask;
      } catch (thrown) {
        if (!ctx.alive() || !pending.current(mine)) return;
        results.classList.remove("stale");
        results.removeAttribute("aria-busy");
        ui.showError(results, thrown, { retry: () => search(searchButton) });
        return;
      }
      if (!ctx.alive() || !pending.current(mine)) return;
      results.classList.remove("stale");
      results.removeAttribute("aria-busy");
      memory.query = query;
      memory.searched = query;
      memory.rows = rows;
      paint(rows);
      if (origin && origin !== searchButton) {
        const first = list.querySelector(".row-actions .btn");
        if (first) first.focus();
      }
    }

    const searchButton = ui.button("Search", { kind: "primary", onClick: () => search(searchButton) });
    field.el.querySelector(".inputrow").append(searchButton);
    input.addEventListener("keydown", (event) => {
      if (event.key === "Enter") {
        event.preventDefault();
        searchButton.click();
      }
    });
    input.addEventListener("input", () => field.setError(null));

    const panel = ui.panel("Hugging Face");
    panel.body.append(
      el("p", { class: "muted adv-text", text: "Downloads go to your model folder. A cancelled download keeps the part already fetched, so it can resume." }),
      library ? ui.kv([["Model folder", el("span", { class: "mono adv-path", title: AW.fmt.path(library), text: AW.fmt.path(library) })]]) : null,
      field.el,
      vision.el,
      downloads,
    );
    const resultsPanel = ui.panel("Results");
    resultsPanel.body.append(results);
    for (const name of memory.finished) downloads.append(finishedNotice(name));
    for (const record of AW.jobs.list()) if (record.state === "running" && record.action === "studio-download" && record.key) follow(record.key, record.title.replace(/^Downloading /, ""));
    if (memory.rows) paint(memory.rows);
    else list.append(ui.empty("Search for a model name, or enter a repository to list its files."));
    body.replaceChildren(panel.el, resultsPanel.el);
    if (ctx.focus === "query") input.focus();
  });
})();
