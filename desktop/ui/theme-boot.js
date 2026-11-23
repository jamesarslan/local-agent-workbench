// Sets the theme before the first paint; loaded from <head> because inline scripts are not allowed.
(function () {
  "use strict";
  var root = document.documentElement;
  var choice = "light";
  try {
    var stored = localStorage.getItem("appearance");
    var moved = { apple: "light", framer: "dark" }[stored];
    if (moved) localStorage.setItem("appearance", moved);
    choice = moved || stored || "light";
  } catch (error) {
    choice = "light";
  }
  if (choice !== "light" && choice !== "dark" && choice !== "system") choice = "light";
  var dark = false;
  try {
    dark = window.matchMedia("(prefers-color-scheme: dark)").matches;
  } catch (error) {
    dark = false;
  }
  root.dataset.themeChoice = choice;
  root.dataset.theme = choice === "system" ? (dark ? "dark" : "light") : choice;
})();
