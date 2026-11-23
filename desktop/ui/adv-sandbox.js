// Advanced, Sandbox: open or stop a named Docker workspace that runs OMP against the local model (ui.md 5.6).
(function () {
  "use strict";
  const AW = window.AW;
  const el = AW.el;
  const ui = AW.ui;
  const NAME = /^[a-z0-9][a-z0-9-]{0,35}$/;

  AW.advanced.section("Sandbox", async (body, ctx) => {
    const info = await AW.advanced.info();
    if (!ctx.alive()) return;
    const config = (info && info.config) || {};
    const checks = (info && info.checks) || {};
    const input = el("input", { type: "text", id: "adv-box-name", class: "mono adv-short", value: String(config.box || "dev"), attrs: { autocomplete: "off", spellcheck: "false", maxlength: 36 } });
    const field = ui.field("Docker workspace", input, { hint: "Each name gets its own container and its own files. Use 1–36 lowercase letters, digits or dashes." });
    const host = el("div", { class: "adv-errors" });
    let tracked = null;
    ctx.cleanup(() => tracked && tracked.destroy());

    const name = () => input.value.trim();
    const check = () => {
      const value = name();
      const problem = !value ? "Enter a workspace name." : NAME.test(value) ? null : "Use 1–36 lowercase letters, digits or dashes.";
      field.setError(problem);
      if (problem) input.focus();
      return !problem;
    };
    input.addEventListener("input", () => field.setError(null));

    const lock = (on) => {
      for (const button of [openButton, stopButton]) {
        if (on) button.setAttribute("aria-disabled", "true");
        else button.removeAttribute("aria-disabled");
      }
    };
    const follow = () => {
      if (tracked) tracked.destroy();
      tracked = AW.advanced.track(host, "sandbox", {
        onDone() {
          tracked = null;
          lock(false);
        },
      });
      if (tracked) lock(true);
    };

    async function run(start, origin) {
      if (!check()) return;
      const box = name();
      ui.clearError(host);
      if (start && box !== config.box) {
        try {
          await AW.request("studio-save", { box });
          config.box = box;
        } catch (thrown) {
          ui.showError(host, thrown);
          return;
        }
      }
      const promise = AW.jobs.run("studio-box", { name: box, start }, { title: start ? "Opening the sandbox" : "Stopping the sandbox", origin, key: "sandbox" });
      follow();
      try {
        await promise;
      } catch {
        return;
      }
      if (origin.isConnected) ui.toast(start ? "The sandbox " + box + " is open in a terminal window." : "The sandbox " + box + " stopped. Its files are kept.", { kind: "ok" });
    }

    const openButton = ui.button("Open sandbox", { kind: "primary", onClick: () => run(true, openButton) });
    const stopButton = ui.button("Stop sandbox", { onClick: () => run(false, stopButton) });
    input.addEventListener("keydown", (event) => {
      if (event.key === "Enter") {
        event.preventDefault();
        openButton.click();
      }
    });

    const panel = ui.panel("Sandbox");
    panel.body.append(
      el("p", { class: "muted adv-text", text: "A sandbox is a Docker container where OMP works in /workspace with the local model. Your project folders, your home folder and the Docker socket stay outside it. Stopping a sandbox keeps its files." }),
      field.el,
      el("div", { class: "actions" }, openButton, stopButton),
      el("p", { class: "hint", text: "The first run builds the image, which can take up to 30 minutes. The sandbox always talks to the local model on this computer." }),
      host,
    );
    if (!checks.Docker) {
      panel.body.prepend(
        ui.notice("warn", "Docker is not installed on this computer, so the sandbox cannot start.", {
          actions: [{ label: "Open Advanced → Setup", run: () => AW.router.go("advanced", { section: "Setup", tool: "Docker" }) }],
        }),
      );
    }
    body.replaceChildren(panel.el);
    follow();
  });
})();
