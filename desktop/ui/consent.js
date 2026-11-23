// Review this folder's programs (ui.md 5.3, 6.1 setup.consent): the files a project folder carries that make an agent run programs,
// read before the first launch, and the grant that remembers the review for the folder.
// AW.consent.card(workspace, harness, { onChange }) -> Promise<HTMLElement | null>   inline on the MCP tools tab; null when the folder defines nothing
// AW.consent.dialog(workspace, harness, { launch }) -> Promise<boolean>              true when allowed now or no review is needed
// AW.consent.grantedSentence(isoTime) -> the line shown once the folder's programs are allowed
// Fix consent.open(agent): the dialog for the current folder.
(function () {
  "use strict";
  const AW = window.AW;
  const el = AW.el;
  const ui = AW.ui;
  const ID = "consent-dialog";
  const text = (value) => (typeof value === "string" ? value : "");
  let cards = 0;

  function showError(host, thrown, options) {
    host.hidden = false;
    return ui.showError(host, thrown, options);
  }

  function clearError(host) {
    ui.clearError(host);
    host.hidden = true;
  }

  const relevant = (status) => (status && Array.isArray(status.items) ? status.items : []).filter((item) => item && item.relevant);

  const fetchStatus = (workspace, harness) => AW.request("project-setup-consent", harness ? { workspace, harness } : { workspace });
  const heading = (harness) => "This folder defines programs that " + (harness || "your agent") + " will run";

  async function refreshStore(workspace) {
    const form = AW.form.get();
    if (workspace !== AW.project.current() && workspace !== form.workspace) return;
    await Promise.all([AW.store.refresh("consent", { force: true }), AW.store.refresh("folder", { force: true })].map((call) => call.catch(() => {})));
  }

  // "You allowed this folder's programs on 20 Nov." from folder-get's consent_granted; without a time the sentence has no date.
  function grantedSentence(stamp, now) {
    const when = stamp ? AW.fmt.ago(stamp, now) : "";
    const at = !when ? "" : /^\d{1,2} [A-Z]/.test(when) ? " on " + when : " " + when;
    return "You allowed this folder's programs" + at + ". Workbench asks again when they change.";
  }

  function knownGrant(workspace) {
    const folder = AW.state.folder;
    return folder && folder.path === workspace ? text(folder.consent_granted) : "";
  }

  function itemList(status) {
    const list = el("ul", { class: "stack", attrs: { "aria-label": "Files that start programs" } });
    list.style.gap = "0";
    for (const item of relevant(status)) {
      const readers = Array.isArray(item.read_by) ? item.read_by.filter((name) => typeof name === "string") : [];
      const top = el("div", { class: "actions" }, el("span", { class: "plan-path", text: AW.fmt.path(text(item.path)) }), readers.length ? el("small", { class: "muted", text: "read by " + AW.fmt.list(readers) }) : null);
      top.style.justifyContent = "space-between";
      top.style.gap = "var(--space-2) var(--space-5)";
      const detail = el("p", { class: "muted wrap", text: text(item.detail) });
      detail.style.whiteSpace = "pre-line";
      detail.style.fontSize = "var(--text-md)";
      const row = el("li", { class: "stack" }, top, text(item.detail) ? detail : null);
      row.style.gap = "var(--space-2)";
      row.style.padding = "var(--space-4) 0";
      row.style.borderTop = "1px solid var(--border)";
      list.append(row);
    }
    return list;
  }

  // One review: the item list, an error place and the Allow button. allow() resolves true once the grant is stored.
  function review(workspace, harness, status, { launch = false, onGranted } = {}) {
    let current = status;
    const changed = el("div", { class: "stack", hidden: true });
    const listHost = el("div", null, itemList(current));
    const errors = el("div", { class: "stack", hidden: true });
    const intro = el("p", { class: "wrap", text: "Workbench did not write these files. Read what they start before you allow them." });
    const allowButton = ui.button(launch ? "Allow for this folder and launch" : "Allow for this folder", { kind: "primary", onClick: () => allow() });

    async function allow() {
      clearError(errors);
      const granting = AW.request("project-setup-consent-grant", { workspace, digest: text(current.digest) });
      ui.pending(allowButton, "Allowing…", granting).catch(() => {});
      try {
        await granting;
      } catch (thrown) {
        const error = AW.asError(thrown);
        const fresh = error.code === "cancelled" || error.code === "busy" ? null : await fetchStatus(workspace, harness).catch(() => null);
        if (fresh && fresh.digest !== current.digest) {
          current = fresh;
          if (!fresh.needed) {
            await refreshStore(workspace);
            onGranted && onGranted(fresh);
            return true;
          }
          listHost.replaceChildren(itemList(current));
          changed.replaceChildren(ui.notice("warn", error.message));
          changed.hidden = false;
          ui.announce(error.message, { assertive: true });
          return false;
        }
        showError(errors, error, { retry: () => allow() });
        return false;
      }
      changed.replaceChildren();
      changed.hidden = true;
      await refreshStore(workspace);
      const fresh = await fetchStatus(workspace, harness).catch(() => ({ ...current, needed: false, granted: true }));
      ui.announce("Allowed for this folder.");
      onGranted && onGranted(fresh);
      return true;
    }

    return { intro, changed, listHost, errors, allowButton };
  }

  function grantedLine(workspace, harness, host, onChange) {
    const errors = el("div", { class: "stack", hidden: true });
    const revoke = ui.button("Revoke", {
      small: true,
      pending: "Revoking…",
      async onClick() {
        clearError(errors);
        try {
          await AW.request("project-setup-consent-revoke", { workspace });
        } catch (thrown) {
          showError(errors, thrown, { retry: () => revoke.click() });
          return;
        }
        await refreshStore(workspace);
        const fresh = await fetchStatus(workspace, harness).catch((thrown) => {
          showError(errors, thrown);
          return null;
        });
        if (!fresh) return;
        paintCard(host, workspace, harness, fresh, onChange);
        ui.announce("Workbench asks again before the next launch in this folder.");
        onChange && onChange(fresh);
        AW.focus.first(host);
      },
    });
    const words = el("span", { class: "muted wrap", text: grantedSentence(knownGrant(workspace)) });
    AW.request("folder-get", { workspace }).then(
      (view) => (words.textContent = grantedSentence(view && text(view.consent_granted))),
      () => {},
    );
    const line = el("div", { class: "actions" }, words, revoke);
    return el("div", { class: "stack" }, line, errors);
  }

  function paintCard(host, workspace, harness, status, onChange) {
    if (status.needed) {
      const parts = review(workspace, harness, status, {
        onGranted(fresh) {
          paintCard(host, workspace, harness, fresh, onChange);
          onChange && onChange(fresh);
          AW.focus.first(host);
        },
      });
      const title = el("h3", { class: "wrap", id: "consent-card-title-" + ++cards }, ui.mark("warn"), " ", heading(harness));
      const card = ui.notice("warn", el("div", { class: "stack" }, title, parts.intro, parts.changed, parts.listHost, parts.errors), { actions: [parts.allowButton] });
      card.setAttribute("role", "region");
      card.setAttribute("aria-labelledby", title.id);
      host.replaceChildren(card);
      host.hidden = false;
    } else if (status.granted) {
      host.replaceChildren(grantedLine(workspace, harness, host, onChange));
      host.hidden = false;
    } else {
      host.replaceChildren();
      host.hidden = true;
    }
  }

  async function card(workspace, harness, { onChange } = {}) {
    if (!workspace) return null;
    const host = el("div", { class: "consent-card" });
    let status;
    try {
      status = await fetchStatus(workspace, harness);
    } catch (thrown) {
      const retry = async () => {
        try {
          const fresh = await fetchStatus(workspace, harness);
          paintCard(host, workspace, harness, fresh, onChange);
        } catch (again) {
          host.replaceChildren(ui.errorNotice(again, { retry }));
        }
      };
      host.replaceChildren(ui.errorNotice(thrown, { retry }));
      return host;
    }
    if (!status || (!status.needed && !status.granted)) return null;
    paintCard(host, workspace, harness, status, onChange);
    return host;
  }

  let session = null;
  let handle = null;
  function consentDialog() {
    if (handle) return handle;
    handle = ui.dialog(ID, {
      onClose(value) {
        const ended = session;
        session = null;
        if (ended) ended.resolve(value === true || ended.granted);
      },
    });
    return handle;
  }

  async function dialog(workspace, harness, { launch = false } = {}) {
    if (!workspace) return false;
    const box = consentDialog();
    if (session) {
      const ended = session;
      session = null;
      ended.resolve(ended.granted);
    }
    let resolve;
    const answer = new Promise((done) => (resolve = done));
    const state = { resolve, granted: false };
    session = state;
    const body = AW.$(ID + "-body");
    const title = AW.$(ID + "-title");
    const live = () => session === state;

    const notNow = ui.button("Not now", { onClick: () => box.close(false) });
    const toSetup = ui.button("Open Project setup", {
      onClick() {
        box.close(false);
        return AW.router.go("project", { tab: "mcp" });
      },
    });
    toSetup.style.marginRight = "auto";
    toSetup.hidden = AW.router.current().id === "project";

    function show(status) {
      title.textContent = heading(harness);
      const parts = review(workspace, harness, status, {
        launch,
        onGranted() {
          state.granted = true;
          if (live()) box.close(true);
        },
      });
      body.replaceChildren(el("div", { class: "stack" }, parts.intro, parts.changed, parts.listHost, parts.errors), el("div", { class: "dialog-actions" }, toSetup, notNow, parts.allowButton));
    }

    function failed(thrown) {
      title.textContent = "Review this folder's programs";
      const errors = el("div", { class: "stack", hidden: true });
      const intro = el("p", { class: "wrap", text: "Workbench could not read which programs this folder defines, so it cannot ask you to allow them yet." });
      body.replaceChildren(el("div", { class: "stack" }, intro, errors), el("div", { class: "dialog-actions" }, toSetup, notNow));
      showError(errors, thrown, { retry: () => load(true) });
    }

    async function load(shown) {
      let status;
      try {
        status = await fetchStatus(workspace, harness);
      } catch (thrown) {
        if (!live()) return;
        if (!shown) box.open();
        failed(thrown);
        return;
      }
      if (!live()) return;
      if (!status || !status.needed || !relevant(status).length) {
        state.granted = true;
        if (shown) box.close(true);
        else {
          session = null;
          resolve(true);
        }
        return;
      }
      show(status);
      if (!shown) box.open();
    }

    await load(false);
    return answer;
  }

  AW.consent = { card, dialog, grantedSentence };
  AW.fix.register("consent.open", (agent) => {
    const form = AW.form.get();
    return dialog(form.workspace, agent || form.harness);
  });
})();
