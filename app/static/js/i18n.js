// Portal localisation in the browser: t() and tn() over the catalogue the page
// inlined as window.EFP_I18N (base.html). English is the source language, so
// the key is the English text and a missing translation returns it; {name}
// placeholders are filled from the params object. This script is blocking and
// loads before every first-party script, including the ones that write into
// the shell at parse time, so they can call t() at top level.
//
// Call t() only where no local `t` is in scope: a `const t` or a parameter
// named t shadows the global, and scripts/i18n_check.py cannot see that.
(function () {
  const data = (typeof window !== "undefined" && window.EFP_I18N && typeof window.EFP_I18N === "object") ? window.EFP_I18N : {};
  const messages = data.messages && typeof data.messages === "object" ? data.messages : {};
  const documentLang = (typeof document !== "undefined" && document.documentElement) ? document.documentElement.lang : "";
  const locale = String(data.locale || documentLang || "en");

  function interpolate(text, params) {
    const source = String(text);
    if (!params || typeof params !== "object" || source.indexOf("{") === -1) return source;
    return source.replace(/\{([A-Za-z_][A-Za-z0-9_]*)\}/g, (match, name) => (
      Object.prototype.hasOwnProperty.call(params, name) ? String(params[name]) : match
    ));
  }

  function lookup(key) {
    const value = messages[key];
    return typeof value === "string" && value.trim() ? value : null;
  }

  // The English text in the page's language, with its placeholders filled.
  function t(key, params) {
    return interpolate(lookup(key) || key, params);
  }

  // The plural form for count: one or other in English (count === 1 picks
  // one), the catalogue's single entry under "one|other" elsewhere. {n} is
  // the count unless params says otherwise.
  function tn(count, one, other, params) {
    const merged = Object.assign({ n: count }, params || {});
    const translated = lookup(`${one}|${other}`);
    if (translated) return interpolate(translated, merged);
    return interpolate(count === 1 ? one : other, merged);
  }

  const api = { locale, messages, t, tn, interpolate };
  if (typeof window !== "undefined") window.EFP_I18N = api;
  // In a browser window is globalThis; in Node (the test harness) it is a
  // plain object, so the bare names go on the real global as well.
  const root = typeof globalThis !== "undefined" ? globalThis : window;
  root.t = t;
  root.tn = tn;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})();
