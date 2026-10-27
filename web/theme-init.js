/* Runs before the page is drawn, so night mode never flashes white: the saved choice, else the phone's own setting. */
try {
  var t = localStorage.getItem("theme");
  if (t === "dark" || (!t && matchMedia("(prefers-color-scheme: dark)").matches)) document.documentElement.dataset.theme = "dark";
} catch (e) {}
/* The opening animation plays once per visit (not again after a language change or a reload). */
try {
  if (sessionStorage.getItem("splash")) document.documentElement.classList.add("no-splash"); else sessionStorage.setItem("splash", "1");
} catch (e) {}
