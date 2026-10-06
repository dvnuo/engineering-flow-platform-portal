// Portal's additions to the hosted Appium Inspector (served at /inspector/):
// the session view starts in Tap/Swipe By Coordinates, so the member uses the
// phone directly; the screen refreshes itself while the app is still loading
// after an action; and a note says that the local bridge records everything
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
  // The Inspector's Refresh Source & Screenshot button (GeneralControlsGroup.jsx).
  const REFRESH_BUTTON_ID = "btnReload";
  // After an action the Inspector refreshes once, at once. An app still
  // loading then shows its spinner until the member presses Refresh, so the
  // page is refreshed again while its source keeps changing: first after
  // SETTLE_FIRST_MS, then every SETTLE_NEXT_MS, at most SETTLE_MAX_REFRESHES
  // times, and no more once two sources in a row are the same.
  const SETTLE_FIRST_MS = 1500;
  const SETTLE_NEXT_MS = 2500;
  const SETTLE_MAX_REFRESHES = 4;
  let modeApplied = false;
  let hintShown = false;

  // ---- Tap/Swipe By Coordinates by default -------------------------------

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

  // ---- refresh while the app loads --------------------------------------

  let inFlight = 0;
  let lastSource = null;
  let settleArmed = false;
  let settleLeft = 0;
  let settleTimer = null;

  // What counts as an action: a POST to the session that changes the device,
  // not a find, a setting, a window query, or a mobile: getter.
  function isAction(method, pathname, body) {
    if (method !== "POST") return false;
    const at = pathname.indexOf("/session/");
    if (at < 0) return false;
    const afterId = pathname.slice(at + "/session/".length);
    const slash = afterId.indexOf("/");
    const rest = slash < 0 ? "" : afterId.slice(slash + 1);
    if (!rest || rest === "element" || rest === "elements" || rest === "source" || rest === "screenshot" || rest === "timeouts" || rest === "context" || rest === "orientation") return false;
    if (rest.startsWith("window") || rest.startsWith("appium/settings") || rest.startsWith("appium/device/hide_keyboard")) return false;
    if (rest === "execute/sync" || rest === "execute") {
      let script = "";
      try {
        script = String(JSON.parse(typeof body === "string" ? body : "{}").script || "");
      } catch (_error) {
        return false;
      }
      const name = script.replace(/^mobile:\s*/i, "").toLowerCase();
      return script !== name && !name.startsWith("get") && !name.startsWith("is") && !name.startsWith("list");
    }
    return true;
  }

  function schedule(ms) {
    window.clearTimeout(settleTimer);
    settleTimer = window.setTimeout(refreshNow, ms);
  }

  function armSettle() {
    settleArmed = true;
    settleLeft = SETTLE_MAX_REFRESHES;
    schedule(SETTLE_FIRST_MS);
  }

  function disarmSettle() {
    settleArmed = false;
    window.clearTimeout(settleTimer);
  }

  function refreshNow() {
    if (!settleArmed || settleLeft <= 0) return;
    const button = document.getElementById(REFRESH_BUTTON_ID);
    if (inFlight > 0 || !button || button.disabled) {
      schedule(500);
      return;
    }
    settleLeft -= 1;
    button.click();
  }

  // Every source the Inspector fetched: while it keeps changing after an
  // action, another refresh follows; once it repeats, the screen has settled.
  function sourceSeen(text) {
    const changed = text !== lastSource;
    lastSource = text;
    if (!settleArmed) return;
    if (changed && settleLeft > 0) schedule(SETTLE_NEXT_MS);
    else disarmSettle();
  }

  function watchFetch() {
    const nativeFetch = window.fetch;
    if (typeof nativeFetch !== "function") return;
    window.fetch = function (input, init) {
      // webdriverio passes a URL object; a Request has .url; a string is a string.
      const url = typeof input === "string" ? input : input && typeof input.url === "string" ? input.url : String(input || "");
      const method = String((init && init.method) || (input && input.method) || "GET").toUpperCase();
      let pathname = "";
      try {
        pathname = new URL(url, window.location.href).pathname;
      } catch (_error) {
        pathname = "";
      }
      const promise = nativeFetch.apply(this, arguments);
      if (!pathname.includes("/session/")) return promise;
      inFlight += 1;
      if (isAction(method, pathname, init && init.body)) armSettle();
      return promise.then(
        (response) => {
          inFlight -= 1;
          if (method === "GET" && pathname.endsWith("/source")) response.clone().text().then(sourceSeen, () => {});
          return response;
        },
        (error) => {
          inFlight -= 1;
          throw error;
        },
      );
    };
  }

  // ---- the recording note -------------------------------------------------

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
      "the Inspector’s own Start Recording only shows code. Tap and swipe on the screenshot as on the phone; " +
      "the screen refreshes itself for a few seconds while the app loads. " +
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

  // ---- wiring -------------------------------------------------------------

  function tick() {
    applyMode();
    showHint();
    if (modeApplied && hintShown) observer.disconnect();
  }

  watchFetch();
  // A button pressed by the member (Refresh, a tab, a mode) means they moved
  // on; the next action re-arms. Taps on the screenshot are actions.
  document.addEventListener("pointerdown", (event) => {
    if (event.target && event.target.closest && event.target.closest("button")) disarmSettle();
  }, true);
  const observer = new MutationObserver(tick);
  observer.observe(document.documentElement, { childList: true, subtree: true });
  tick();
})();
