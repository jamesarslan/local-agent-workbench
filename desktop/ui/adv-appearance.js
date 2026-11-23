// Advanced, Appearance: the theme choice (Light, Dark, Match system) and the workstation name shown in the rail (ui.md 5.6, 8.1).
(function () {
  "use strict";
  const AW = window.AW;
  const el = AW.el;
  const ui = AW.ui;
  const FALLBACK = "Your workstation";
  const MAX = 60;

  AW.advanced.section("Appearance", (body, ctx) => {
    const theme = ui.segmented(
      "Theme",
      [
        { value: "light", label: "Light" },
        { value: "dark", label: "Dark" },
        { value: "system", label: "Match system" },
      ],
      AW.theme.get().choice,
      (choice) => {
        AW.theme.set(choice);
        ui.announce(choice === "system" ? "The theme now matches your system." : choice === "dark" ? "Dark theme on." : "Light theme on.");
      },
    );
    ctx.cleanup(AW.theme.on((now) => theme.set(now.choice)));
    const observer = new MutationObserver(() => theme.set(AW.theme.get().choice));
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme-choice"] });
    ctx.cleanup(() => observer.disconnect());

    const saved = () => AW.storage.get("machineLabel", "") || "";
    const input = el("input", { type: "text", id: "adv-machine-label", class: "adv-short", value: saved(), attrs: { maxlength: MAX, autocomplete: "off", spellcheck: "false", placeholder: FALLBACK } });
    const field = ui.field("Workstation name", input, { hint: "Shown at the bottom of the rail. Leave it empty to show “" + FALLBACK + "”." });
    const save = ui.button("Save name", {
      onClick() {
        const value = input.value.trim().replace(/\s+/g, " ");
        if (value.length > MAX) {
          field.setError("Use " + MAX + " characters or fewer.");
          input.focus();
          return;
        }
        field.setError(null);
        if (value) AW.storage.set("machineLabel", value);
        else AW.storage.remove("machineLabel");
        input.value = value;
        if (AW.rail && AW.rail.refresh) AW.rail.refresh();
        ui.toast(value ? "Workstation name saved." : "Workstation name cleared.", { kind: "ok" });
      },
    });
    input.addEventListener("keydown", (event) => {
      if (event.key === "Enter") {
        event.preventDefault();
        save.click();
      }
    });
    input.addEventListener("input", () => field.setError(null));
    ctx.guard(() => input.value.trim() !== saved());

    const themePanel = ui.panel("Theme");
    themePanel.body.append(theme.el, el("small", { class: "hint", text: "Match system follows your computer’s light or dark setting. The Dark mode button in the rail switches between Light and Dark." }));
    theme.el.setAttribute("aria-labelledby", themePanel.el.getAttribute("aria-labelledby"));
    theme.el.removeAttribute("aria-label");
    const namePanel = ui.panel("Workstation");
    namePanel.body.append(el("div", { class: "adv-inline" }, field.el, save));
    body.replaceChildren(themePanel.el, namePanel.el);
  });
})();
