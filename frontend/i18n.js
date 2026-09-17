/* Lightweight i18n. No key dictionary to keep in sync — text lives at the call site.
 *
 * Dynamic (JS-generated) strings: call T("中文", "English") anywhere and it
 * returns whichever matches the current language.
 *
 * Static HTML text: give the element data-zh="..." (and optionally
 * data-zh-title / data-zh-ph for title/placeholder attributes) plus the
 * matching data-en-* attributes; applyStaticI18n() fills in textContent/
 * title/placeholder for the active language once the DOM is ready.
 *
 * Note: this only covers the app's own UI chrome (buttons, labels, tooltips).
 * TFT game data (unit/item/augment names) and the LLM coach's replies are
 * Chinese-only right now — see the top-level README for why.
 */
(function () {
  window.LANG = localStorage.getItem("tft_lang") || (navigator.language.toLowerCase().startsWith("zh") ? "zh" : "en");

  window.T = function (zh, en) {
    return window.LANG === "zh" ? zh : (en != null ? en : zh);
  };

  window.setLang = function (lang) {
    localStorage.setItem("tft_lang", lang);
    location.reload();
  };

  window.applyStaticI18n = function () {
    const lang = window.LANG;
    document.querySelectorAll("[data-" + lang + "]").forEach((el) => {
      el.textContent = el.getAttribute("data-" + lang);
    });
    document.querySelectorAll("[data-" + lang + "-title]").forEach((el) => {
      el.title = el.getAttribute("data-" + lang + "-title");
    });
    document.querySelectorAll("[data-" + lang + "-ph]").forEach((el) => {
      el.placeholder = el.getAttribute("data-" + lang + "-ph");
    });
    const btn = document.getElementById("btn-lang");
    if (btn) btn.textContent = lang === "zh" ? "EN" : "中";
  };
})();
document.addEventListener("DOMContentLoaded", window.applyStaticI18n);
