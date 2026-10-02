// Apply a saved light/dark choice before first paint. "system" (the default) needs no attribute:
// the stylesheet follows prefers-color-scheme on its own.
(function () {
  try {
    var theme = localStorage.getItem("studio.theme");
    if (theme === "light" || theme === "dark") document.documentElement.setAttribute("data-theme", theme);
  } catch (e) {
    /* storage blocked (private mode): fall back to the system theme */
  }
})();
