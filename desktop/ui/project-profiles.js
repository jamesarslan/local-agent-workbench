// Project setup, Project profiles tab: what this computer remembers for the folder, the named profiles stored in the folder
// with the folder default, where each of these settings lives, and the Apply profile dialog with every change and the
// prompt in full (ui.md 5.3). AW.profiles.review opens that dialog from Launch too.
(function () {
  "use strict";
  const AW = window.AW;
  const el = AW.el;
  const P = AW.projectSetup;
  const SOURCE_WORDS = { "Managed local": "Local model", "Native login": "Agent account" };

  let ctx = P.context();
  let parts = null;
  let folder = null;
  let folderError = null;
  let names = null;
  let namesError = null;
  let defaultName = "";
  let problem = "";
  let renaming = "";
  let places = null;
  let placesError = null;
  const folderTicket = AW.ticket();
  const namesTicket = AW.ticket();
  const placesTicket = AW.ticket();

  function sourceLabel(recipe) {
    if (!recipe) return "";
    if (SOURCE_WORDS[recipe.provider]) return SOURCE_WORDS[recipe.provider];
    const row = (AW.state.accounts || []).find((account) => account.connection_id === recipe.connection_id);
    return (row && (row.name || row.provider)) || recipe.provider || "";
  }

  function paintFolder() {
    const host = parts.folderBody;
    if (folderError && !folder) {
      P.loaded(host);
      P.swap(host, AW.ui.errorNotice(folderError, { retry: () => loadFolder() }));
      return;
    }
    if (!folder) {
      P.loading(host, "what this computer remembers");
      return;
    }
    P.loaded(host);
    if (!folder.known) {
      P.swap(host, AW.ui.empty("This computer remembers nothing for this folder yet. Launch an agent here, or choose Save for this folder on Launch."));
      return;
    }
    const recipe = folder.recipe || {};
    const updated = folder.updated ? "updated " + AW.fmt.ago(folder.updated) : "";
    const summary = recipe.harness
      ? [recipe.harness, sourceLabel(recipe), folder.model_label || recipe.remote_model, updated].filter(Boolean).join(" · ")
      : "No launch choices are saved for this folder yet" + (updated ? " · " + updated : "") + ".";
    const nodes = [
      el(
        "div",
        { class: "project-remembered" },
        el("span", { class: "project-remembered-text", text: summary }),
        P.keyed(AW.ui.button("Forget…", { small: true, onClick: forgetFolder }), "forget"),
      ),
    ];
    if (folder.kit) {
      const kitLine = el("p", { class: "project-caption", text: "Starter kit applied here: " + folder.kit + "." });
      P.kits()
        .then((list) => {
          const kit = (Array.isArray(list) ? list : []).find((row) => row.id === folder.kit);
          if (kit) kitLine.textContent = "Starter kit applied here: " + kit.label + ".";
        })
        .catch(() => {});
      nodes.push(kitLine);
    }
    if (Array.isArray(folder.stale) && folder.stale.length) nodes.push(AW.ui.notice("warn", "Some remembered choices no longer exist: " + folder.stale.join(", ") + "."));
    if (folder.prompt && typeof folder.prompt.text === "string") {
      nodes.push(
        el(
          "div",
          { class: "project-prompt" },
          el(
            "div",
            { class: "project-prompt-head" },
            el("span", { text: folder.prompt.source ? "Prompt for this folder, from the profile " + folder.prompt.source + ":" : "Prompt for this folder:" }),
            P.keyed(AW.ui.button("Clear prompt…", { small: true, onClick: clearPrompt }), "clear-prompt"),
          ),
          el("p", { class: "project-prompt-text", text: folder.prompt.text }),
        ),
      );
    }
    P.swap(host, nodes);
  }

  function profileRow(name) {
    const row = el("div", { class: "project-profile", data: { name } });
    const errors = el("div", { class: "project-notices" });
    if (renaming === name) {
      const input = el("input", { type: "text", value: name, attrs: { "aria-label": "New name for the profile " + name, maxlength: "80", autocomplete: "off" } });
      const save = AW.ui.button("Save name", {
        small: true,
        kind: "primary",
        pending: "Saving…",
        onClick: () => rename(name, input.value, errors),
      });
      const cancel = AW.ui.button("Cancel", {
        small: true,
        onClick() {
          renaming = "";
          paintNames();
          focusRow(name, "rename");
        },
      });
      input.addEventListener("keydown", (event) => {
        if (event.key === "Enter") save.click();
        if (event.key === "Escape") {
          event.preventDefault();
          event.stopPropagation();
          cancel.click();
        }
      });
      row.append(el("div", { class: "project-profile-rename" }, input), el("div", { class: "project-row-actions" }, save, cancel), errors);
      setTimeout(() => {
        input.focus();
        input.select();
      });
      return row;
    }
    const review = AW.ui.button("Review and apply…", { small: true, onClick: () => openApply(name, review) });
    review.dataset.action = "review";
    const renameButton = AW.ui.button("Rename", {
      small: true,
      onClick() {
        renaming = name;
        paintNames();
      },
    });
    renameButton.dataset.action = "rename";
    const remove = AW.ui.button("Remove…", { small: true, onClick: () => removeProfile(name, errors) });
    remove.dataset.action = "remove";
    const isDefault = name === defaultName;
    const mark = isDefault
      ? AW.ui.button("Clear default", { small: true, pending: "Saving…", onClick: () => markDefault(name, false, errors) })
      : AW.ui.button("Make default", { small: true, pending: "Saving…", onClick: () => markDefault(name, true, errors) });
    mark.dataset.action = "default";
    const label = el("span", { class: "project-profile-name" }, el("span", { text: name }), isDefault ? AW.ui.chip("Folder default", "ok") : null);
    row.append(label, el("div", { class: "project-row-actions" }, P.keyed(review, "review:" + name), P.keyed(mark, "default:" + name), P.keyed(renameButton, "rename:" + name), P.keyed(remove, "remove:" + name)), errors);
    return row;
  }

  function focusRow(name, action) {
    const row = [...parts.list.children].find((node) => node.dataset && node.dataset.name === name);
    const button = row && row.querySelector('[data-action="' + action + '"]');
    if (button) button.focus();
  }

  function paintNames() {
    const host = parts.list;
    if (namesError && !names) {
      P.loaded(host);
      P.swap(host, AW.ui.errorNotice(namesError, { retry: () => loadNames() }));
      return;
    }
    if (!names) {
      P.loading(host, "profiles");
      return;
    }
    P.loaded(host);
    parts.count.textContent = names.length ? String(names.length) : "";
    if (!names.length) {
      P.swap(host, AW.ui.empty("No profiles in this folder yet. A profile is a named set of launch choices that travels with the folder."));
      return;
    }
    P.swap(host, problem ? AW.ui.notice("warn", problem) : null, names.map(profileRow));
  }

  function placeRow(row) {
    const node = el("div", { class: "project-place", data: { place: row.id } });
    const head = el("div", { class: "project-place-head" }, el("span", { class: "project-place-label", text: row.label }));
    if (row.path) head.append(P.keyed(AW.ui.button("Show in folder", { small: true, onClick: () => reveal(row.id) }), "reveal:" + row.id));
    node.append(head);
    if (row.path) node.append(el("p", { class: "project-row-path mono", title: AW.fmt.path(row.path), text: AW.fmt.path(row.path) + (row.exists ? "" : " · not created yet") }));
    node.append(el("p", { class: "project-caption", text: row.note }));
    return node;
  }

  function paintPlaces() {
    const host = parts.places;
    if (placesError && !places) {
      P.loaded(host);
      P.swap(host, AW.ui.errorNotice(placesError, { retry: () => loadPlaces() }));
      return;
    }
    if (!places) {
      P.loading(host, "where the settings live");
      return;
    }
    P.loaded(host);
    const rows = Array.isArray(places) ? places : [];
    const group = (travels, label) => {
      const listed = rows.filter((row) => Boolean(row.travels) === travels);
      return listed.length ? [el("p", { class: "group-label", text: label }), ...listed.map(placeRow)] : [];
    };
    P.swap(host, group(true, "Travels with the folder"), group(false, "Stays on this computer"));
  }

  function paint() {
    if (!parts) return;
    paintFolder();
    paintNames();
    paintPlaces();
  }

  async function loadFolder() {
    if (!ctx.workspace) return;
    const mine = folderTicket.next();
    const workspace = ctx.workspace;
    folderError = null;
    paintFolder();
    try {
      const value = await AW.request("folder-get", { workspace });
      if (!folderTicket.current(mine)) return;
      folder = value;
    } catch (thrown) {
      if (!folderTicket.current(mine)) return;
      folderError = AW.asError(thrown);
    }
    paintFolder();
  }

  async function loadNames() {
    if (!ctx.workspace) return;
    const mine = namesTicket.next();
    namesError = null;
    paintNames();
    let list = null;
    try {
      list = await AW.request("project-list", { workspace: ctx.workspace, detail: true });
    } catch (thrown) {
      if (!namesTicket.current(mine)) return;
      namesError = AW.asError(thrown);
      paintNames();
      return;
    }
    if (!namesTicket.current(mine)) return;
    showNames(list);
    P.setCount("profiles", names.length);
  }

  function showNames(list) {
    const detail = list && !Array.isArray(list) ? list : { names: list };
    names = Array.isArray(detail.names) ? detail.names : [];
    defaultName = typeof detail.default === "string" ? detail.default : "";
    problem = typeof detail.problem === "string" ? detail.problem : "";
    paintNames();
  }

  async function loadPlaces() {
    if (!ctx.workspace) return;
    const mine = placesTicket.next();
    placesError = null;
    paintPlaces();
    try {
      const value = await AW.request("folder-places", { workspace: ctx.workspace });
      if (!placesTicket.current(mine)) return;
      places = value;
    } catch (thrown) {
      if (!placesTicket.current(mine)) return;
      placesError = AW.asError(thrown);
    }
    paintPlaces();
  }

  async function reveal(place) {
    AW.ui.clearError(parts.placesErrors);
    try {
      await AW.request("folder-reveal", { workspace: ctx.workspace, place });
    } catch (thrown) {
      AW.ui.showError(parts.placesErrors, thrown);
    }
  }

  async function markDefault(name, on, errors) {
    try {
      const detail = on ? await AW.request("project-default-set", { workspace: ctx.workspace, name }) : await AW.request("project-default-clear", { workspace: ctx.workspace });
      AW.ui.toast(on ? name + " is now the folder default." : "This folder has no default profile now.", { kind: "ok" });
      showNames(detail);
      focusRow(name, "default");
      AW.store.refresh("folder").catch(() => {});
    } catch (thrown) {
      AW.ui.showError(errors, thrown);
    }
  }

  async function forgetFolder() {
    const yes = await AW.ui.confirm({ text: "Forget what this computer remembers for " + (ctx.name || "this folder") + "?", ok: "Forget", danger: true });
    if (!yes) return;
    try {
      await AW.request("folder-forget", { workspace: ctx.workspace });
      AW.ui.toast("Forgot the choices for " + (ctx.name || "this folder") + ".", { kind: "ok" });
      for (const key of ["folder", "folders"]) AW.store.refresh(key).catch(() => {});
      await loadFolder();
    } catch (thrown) {
      AW.ui.showError(parts.folderErrors, thrown);
    }
  }

  async function clearPrompt() {
    const yes = await AW.ui.confirm({ text: "Clear the prompt for " + (ctx.name || "this folder") + "? Agents launched here go back to the prompt in your settings.", ok: "Clear prompt", danger: true });
    if (!yes) return;
    try {
      folder = await AW.request("folder-clear-prompt", { workspace: ctx.workspace });
      AW.ui.toast("Cleared the prompt for this folder.", { kind: "ok" });
      AW.store.refresh("folder").catch(() => {});
      paintFolder();
    } catch (thrown) {
      AW.ui.showError(parts.folderErrors, thrown);
    }
  }

  async function rename(name, next, errors) {
    const label = String(next || "").trim();
    if (!label || label === name) {
      renaming = "";
      paintNames();
      focusRow(name, "rename");
      return;
    }
    try {
      await AW.request("studio-profile-edit", { workspace: ctx.workspace, name, op: "rename", new_name: label });
      renaming = "";
      await loadNames();
      focusRow(label, "rename");
      AW.ui.toast("Renamed the profile to " + label + ".", { kind: "ok" });
    } catch (thrown) {
      AW.ui.showError(errors, thrown);
    }
  }

  async function removeProfile(name, errors) {
    const yes = await AW.ui.confirm({ text: "Remove the profile " + name + " from this folder?", ok: "Remove", danger: true });
    if (!yes) return;
    try {
      await AW.request("studio-profile-edit", { workspace: ctx.workspace, name, op: "remove" });
      AW.ui.toast("Removed the profile " + name + ".", { kind: "ok" });
      await loadNames();
      AW.focus.first(parts.saveRow);
    } catch (thrown) {
      AW.ui.showError(errors, thrown);
    }
  }

  async function saveProfile() {
    const label = parts.name.value.trim();
    parts.nameField.setError(null);
    AW.ui.clearError(parts.saveErrors);
    if (!label) {
      parts.nameField.setError("Give this profile a name of 1–80 characters.");
      parts.name.focus();
      return;
    }
    if ((names || []).includes(label)) {
      const yes = await AW.ui.confirm({ text: "Replace the profile " + label + " in this folder with the current choices?", ok: "Replace" });
      if (!yes) return;
    }
    const asDefault = parts.asDefault.checked;
    try {
      await AW.request("project-save", { ...AW.form.get(), workspace: ctx.workspace, name: label, default: asDefault });
      parts.name.value = "";
      parts.asDefault.checked = false;
      AW.ui.toast("Saved the profile " + label + (asDefault ? " as the folder default." : "."), { kind: "ok" });
      if (asDefault) AW.store.refresh("folder").catch(() => {});
      await loadNames();
    } catch (thrown) {
      AW.ui.showError(parts.saveErrors, thrown);
    }
  }

  function changeText(value) {
    if (value === null || value === undefined || value === "") return "not set";
    if (typeof value === "boolean") return value ? "on" : "off";
    return String(value);
  }

  function openApply(name, origin, workspace = ctx.workspace) {
    const dialog = AW.ui.dialog("profile-dialog");
    const body = AW.$("profile-dialog-body");
    AW.$("profile-dialog-title").textContent = "Apply profile: " + name;

    async function load() {
      body.replaceChildren(el("p", { class: "project-dialog-intro", text: "Preparing the changes…" }), AW.ui.skeleton(3, "the profile's changes"));
      let preview;
      try {
        preview = await AW.request("project-preview", { ...AW.form.get(), workspace, name });
      } catch (thrown) {
        body.replaceChildren(
          AW.ui.errorNotice(thrown, { retry: load }),
          el("div", { class: "dialog-actions" }, AW.ui.button("Close", { onClick: () => dialog.close(null) })),
        );
        return;
      }
      render(preview);
    }

    function render(preview) {
      const changes = Array.isArray(preview.changes) ? preview.changes : [];
      const prompt = changes.find((row) => row.field === "prompt");
      const rest = changes.filter((row) => row.field !== "prompt");
      const nodes = [el("p", { class: "project-dialog-intro", text: "From .agent-workbench/profiles.json in this folder. " + (changes.length ? "It changes:" : "It changes nothing: your current choices already match it.") })];
      if (rest.length) {
        const table = el("dl", { class: "project-changes" });
        for (const row of rest)
          table.append(
            el("dt", { text: row.label || row.field }),
            el("dd", null, el("span", { class: "project-change-from", text: changeText(row.from) }), " → ", el("strong", { text: changeText(row.to) }), row.scope === "folder" ? el("span", { class: "muted", text: " (this folder only)" }) : null),
          );
        nodes.push(table);
      }
      let read = null;
      if (prompt) {
        const text = typeof prompt.to === "string" ? prompt.to : "";
        nodes.push(el("p", { class: "project-prompt-label", text: (prompt.label || "Prompt for this folder") + " (this folder only):" }));
        nodes.push(el("pre", { class: "project-prompt-pane", tabindex: 0, attrs: { "aria-label": "Prompt for this folder" }, text: text || "No prompt: agents launched here use the prompt in your settings." }));
        if (text) {
          read = el("input", { type: "checkbox" });
          nodes.push(el("label", { class: "check project-read" }, read, "I have read the prompt above."));
        }
      }
      for (const note of preview.notes || []) nodes.push(el("p", { class: "muted project-note", text: note }));
      const unchanged = Number(preview.unchanged) || 0;
      if (unchanged) nodes.push(el("p", { class: "muted", text: unchanged === 1 ? "1 other setting stays as it is." : unchanged + " other settings stay as they are." }));
      const needs = preview.needs && preview.needs.connection;
      if (needs)
        nodes.push(
          AW.ui.notice("warn", "Add " + (/^[AEIOU]/i.test(needs.provider) ? "an " : "a ") + needs.provider + " connection named " + (needs.name || needs.provider) + ".", {
            actions: [
              {
                label: "Open Connections",
                run() {
                  dialog.close(null);
                  return AW.router.go("accounts", { add: needs.provider, name: needs.name, returnTo: "profiles" });
                },
              },
            ],
          }),
        );
      const errors = el("div", { class: "project-notices" });
      nodes.push(errors);
      const blockers = Array.isArray(preview.blockers) ? preview.blockers : [];
      const cancel = AW.ui.button("Cancel", { onClick: () => dialog.close(null) });
      let actions;
      if (blockers.length) actions = el("div", { class: "dialog-actions project-blocked" }, el("div", { class: "project-blockers" }, blockers.map((text) => el("p", { text }))), cancel);
      else {
        const apply = AW.ui.button("Apply profile", {
          kind: "primary",
          pending: "Applying…",
          async onClick() {
            if (read && !read.checked) {
              read.focus();
              AW.ui.showError(errors, { message: "Tick “I have read the prompt above.” to apply this profile." });
              return;
            }
            AW.ui.clearError(errors);
            try {
              await AW.request("project-apply", { ...AW.form.get(), workspace, name, confirm: preview.digest });
            } catch (thrown) {
              AW.ui.showError(errors, thrown, { retry: load });
              return;
            }
            dialog.close(true);
            AW.ui.toast("Applied the profile " + name + ".", { kind: "ok" });
            try {
              await AW.store.hydrate();
            } catch (thrown) {
              AW.ui.toast(AW.asError(thrown).message, { kind: "danger" });
            }
            if (parts && ctx.workspace === workspace) {
              loadFolder();
              P.refresh({ fresh: true });
            }
          },
        });
        const sync = () => {
          if (read && !read.checked) apply.setAttribute("aria-disabled", "true");
          else apply.removeAttribute("aria-disabled");
        };
        if (read) read.addEventListener("change", sync);
        sync();
        actions = el("div", { class: "dialog-actions" }, cancel, apply);
      }
      nodes.push(actions);
      body.replaceChildren(...nodes);
      AW.focus.first(body);
    }

    dialog.open();
    load();
    return dialog.opened().then(() => origin && origin.isConnected && origin.focus());
  }

  P.tab({
    id: "profiles",
    label: "Project profiles",
    order: 5,
    mount(panel) {
      const folderBody = el("div", { class: "project-folder-body" });
      const folderErrors = el("div", { class: "project-notices" });
      const count = el("span", { class: "panel-count" });
      const list = el("div", { class: "project-profiles", id: "profile-list" });
      const name = el("input", { type: "text", id: "profile-name", attrs: { placeholder: "e.g. Research", maxlength: "80", autocomplete: "off" } });
      const nameField = AW.ui.field("Save the current choices as", name);
      const saveButton = AW.ui.button("Save profile", { id: "profile-save", pending: "Saving…", onClick: saveProfile });
      name.addEventListener("keydown", (event) => {
        if (event.key === "Enter") {
          event.preventDefault();
          saveButton.click();
        }
      });
      const saveErrors = el("div", { class: "project-notices" });
      const saveRow = el("div", { class: "project-save-profile" }, nameField.el, saveButton);
      const asDefault = el("input", { type: "checkbox", id: "profile-as-default" });
      const places = el("div", { class: "project-places" });
      const placesErrors = el("div", { class: "project-notices" });
      panel.append(
        el("section", { class: "panel project-panel", attrs: { "aria-label": "Remembered on this computer" } }, el("div", { class: "panel-head" }, el("h2", { class: "panel-title", text: "Remembered on this computer" })), folderBody, folderErrors),
        el(
          "section",
          { class: "panel project-panel", attrs: { "aria-label": "Profiles in this folder" } },
          el("div", { class: "panel-head" }, el("h2", { class: "panel-title" }, "Profiles in this folder · ", el("span", { class: "project-path-title", text: ".agent-workbench/profiles.json" })), count),
          list,
          saveRow,
          el("label", { class: "check project-save-default" }, asDefault, "Make it the folder default"),
          saveErrors,
          el("p", { class: "project-caption", text: "Commit .agent-workbench/profiles.json to share these with your team. On another computer, Launch offers the folder default for review." }),
        ),
        el(
          "section",
          { class: "panel project-panel", attrs: { "aria-label": "Where this folder's settings live" } },
          el("div", { class: "panel-head" }, el("h2", { class: "panel-title", text: "Where this folder's settings live" })),
          places,
          placesErrors,
        ),
      );
      parts = { folderBody, folderErrors, count, list, name, nameField, saveRow, saveErrors, asDefault, places, placesErrors };
    },
    update(next) {
      const moved = next.workspace !== ctx.workspace;
      ctx = next;
      if (!parts || !ctx.workspace) return;
      if (moved) {
        folder = null;
        names = null;
        defaultName = "";
        problem = "";
        places = null;
        renaming = "";
      }
      paint();
      if (next.fresh || moved) {
        loadFolder();
        loadNames();
        loadPlaces();
      }
    },
  });

  AW.profiles = {
    // The same review and confirmation as Project profiles; nothing is applied without them.
    review: (workspace, name, origin) => openApply(name, origin, workspace),
  };
})();
