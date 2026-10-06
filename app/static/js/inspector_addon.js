// Portal's additions to the hosted Appium Inspector (served at /inspector/):
// the session view starts in Tap/Swipe By Coordinates, so the member uses the
// phone directly, and a note says that the local bridge records everything
// (the Inspector's own Start Recording only shows code). The Inspector's own
// files are not touched; app/api/mobile.py adds this script to its page.
(function () {
  "use strict";

  const DISMISSED_KEY = "efp.inspector.hint.dismissed";
  // The three screenshot mode buttons carry no stable ids; their Tabler
  // icons do: object-scan is Select Elements, crosshair is Tap/Swipe By
  // Coordinates (ScreenshotControls.jsx in appium-inspector).
  const SELECT_ICON = "tabler-icon-object-scan";
  const TAP_SWIPE_ICON = "tabler-icon-crosshair";
  let modeApplied = false;
  let hintShown = false;

  function modeButtons() {
    const selectIcon = document.querySelector("button svg." + SELECT_ICON);
    if (!selectIcon) return null;
    const selectButton = selectIcon.closest("button");
    const group = selectButton && (selectButton.closest(".ant-space-compact") || selectButton.parentElement);
    const tapSwipeIcon = group ? group.querySelector("button svg." + TAP_SWIPE_ICON) : null;
    return tapSwipeIcon ? { group, tapSwipe: tapSwipeIcon.closest("button") } : null;
  }

  // Once per page load, when the session view appears: a member who switches
  // back to Select Elements is left alone.
  function applyMode() {
    if (modeApplied) return;
    const found = modeButtons();
    if (!found) return;
    modeApplied = true;
    const button = found.tapSwipe;
    if (!button || button.disabled || button.classList.contains("ant-btn-primary")) return;
    button.click();
  }

  function dismissed() {
    try {
      return window.localStorage.getItem(DISMISSED_KEY) === "1";
    } catch (_error) {
      return false;
    }
  }

  function remember() {
    try {
      window.localStorage.setItem(DISMISSED_KEY, "1");
    } catch (_error) {
      /* private mode: the note returns next time */
    }
  }

  function showHint() {
    if (hintShown || !document.body || !modeButtons()) return;
    hintShown = true;
    if (dismissed()) return;
    const style = document.createElement("style");
    style.textContent = [
      "#efp-inspector-hint{position:fixed;left:0;right:0;bottom:0;z-index:2147483000;display:flex;align-items:center;gap:12px;padding:8px 16px;background:#1f2933;color:#f5f7fa;font:13px/1.5 system-ui,-apple-system,'Segoe UI',sans-serif;box-shadow:0 -2px 8px rgba(0,0,0,.25)}",
      "#efp-inspector-hint .efp-dot{flex:0 0 auto;width:10px;height:10px;border-radius:50%;background:#ff5a5f;animation:efp-rec 1.4s ease-in-out infinite}",
      "#efp-inspector-hint p{flex:1 1 auto;margin:0}",
      "#efp-inspector-hint strong{color:#fff}",
      "#efp-inspector-hint button{flex:0 0 auto;padding:4px 12px;border:1px solid #9aa5b1;border-radius:6px;background:transparent;color:#f5f7fa;font:inherit;cursor:pointer}",
      "#efp-inspector-hint button:hover{background:rgba(255,255,255,.12)}",
      "@keyframes efp-rec{0%,100%{opacity:.35}50%{opacity:1}}",
    ].join("");
    const bar = document.createElement("div");
    bar.id = "efp-inspector-hint";
    bar.setAttribute("role", "status");
    bar.innerHTML =
      '<span class="efp-dot" aria-hidden="true"></span>' +
      "<p><strong>Recording.</strong> Every tap, swipe, and key you send through this Inspector is recorded by the local bridge; " +
      "the Inspector’s own Start Recording only shows code. Tap and swipe on the screenshot as on the phone. " +
      "To type into a field, switch to Select Elements, pick the field, and use Send Keys. " +
      "When you are done, go back to the Portal tab and press Save recording.</p>" +
      '<button type="button" data-efp-dismiss>Got it</button>';
    bar.querySelector("[data-efp-dismiss]").addEventListener("click", () => {
      remember();
      bar.remove();
      style.remove();
    });
    document.head.appendChild(style);
    document.body.appendChild(bar);
  }

  function tick() {
    applyMode();
    showHint();
    if (modeApplied && hintShown) observer.disconnect();
  }

  const observer = new MutationObserver(tick);
  observer.observe(document.documentElement, { childList: true, subtree: true });
  tick();
})();
