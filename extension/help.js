// Hermes Chrome — Help Page Scripts (CSP Compliant)
document.addEventListener("DOMContentLoaded", () => {
  const body = document.body;
  const btnEn = document.getElementById("btnEn");
  const btnZh = document.getElementById("btnZh");

  function show(lang) {
    body.classList.toggle("show-zh", lang === "zh");
    if (btnEn) btnEn.classList.toggle("active", lang === "en");
    if (btnZh) btnZh.classList.toggle("active", lang === "zh");
    try {
      localStorage.setItem("hermesChromeHelpLang", lang);
    } catch (_) {}
  }

  if (btnEn) btnEn.addEventListener("click", () => show("en"));
  if (btnZh) btnZh.addEventListener("click", () => show("zh"));

  try {
    const saved = localStorage.getItem("hermesChromeHelpLang");
    if (saved === "zh" || saved === "en") {
      show(saved);
    } else if (navigator.language && navigator.language.toLowerCase().startsWith("zh")) {
      show("zh");
    }
  } catch (_) {}
});
