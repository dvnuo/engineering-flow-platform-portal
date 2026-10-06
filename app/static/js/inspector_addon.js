// Portal's additions to the hosted Appium Inspector (served at /inspector/):
// the session view starts in Tap/Swipe By Coordinates, so the member uses the
// phone directly; while the screen shows the app loading, the page source is
// watched in the background and the screen refreshed once the loading is
// over; and a note says that the local bridge records everything (the
// Inspector's own Start Recording only shows code).
// The Inspector's own files are not touched; app/api/mobile.py adds this
// script to its page.
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
  // After an action the Inspector refreshes once, at once. When that source
  // shows the app loading (a progress indicator, a "loading" text), the
  // source is fetched again in the background every LOADING_POLL_MS (the
  // Inspector's own refresh covers the screen with a spinner and blocks it,
  // so it is pressed only once, when the loading is gone), for at most
  // LOADING_MAX_MS; a source that stops changing while the indicator stays
  // (a progress bar that is part of the screen) ends it after
  // LOADING_STUCK_POLLS. A screen without such signs is left alone.
  const LOADING_POLL_MS = 2000;
  const LOADING_MAX_MS = 60000;
  const LOADING_STUCK_POLLS = 3;
  const LOADING_CLASS = /android\.widget\.ProgressBar|ProgressIndicator|ProgressDialog|XCUIElementTypeActivityIndicator/;
  // Texts in a text, label, name, value, or content-desc attribute.
  const LOADING_TEXT = /(?:text|label|name|value|content-desc)="(?:[^"]*\b(?:loading|please wait|signing in|logging in|connecting)|[^"]*(?:正在|加载|载入|请稍候|稍等|处理中|登录中))/i;
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

  // ---- refresh while the app shows it is loading --------------------------

  let inFlight = 0;
  let afterAction = false;
  let watching = false;
  let watchStartedAt = 0;
  let watchTimer = null;
  let lastSource = null;
  let unchangedPolls = 0;
  // The session's source URL, taken from the Inspector's own requests, for
  // the background polls.
  let sourceUrl = "";
  let nativeFetch = null;

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

  function isLoading(source) {
    return LOADING_CLASS.test(source) || LOADING_TEXT.test(source);
  }

  function startWatch() {
    watching = true;
    watchStartedAt = Date.now();
    unchangedPolls = 0;
    showLoadingBadge();
    schedulePoll();
  }

  function stopWatch() {
    watching = false;
    window.clearTimeout(watchTimer);
    hideLoadingBadge();
  }

  function schedulePoll(ms) {
    window.clearTimeout(watchTimer);
    watchTimer = window.setTimeout(pollNow, ms || LOADING_POLL_MS);
  }

  // The Inspector's Refresh: one visible, blocking refresh.
  function pressRefresh() {
    const button = document.getElementById(REFRESH_BUTTON_ID);
    if (!button || button.disabled) return false;
    button.click();
    return true;
  }

  function pollNow() {
    if (!watching) return;
    if (Date.now() - watchStartedAt > LOADING_MAX_MS) {
      stopWatch();
      return;
    }
    if (inFlight > 0 || !sourceUrl || !nativeFetch) {
      schedulePoll(500);
      return;
    }
    // In the background: the Inspector keeps its screen and its controls.
    nativeFetch(sourceUrl, { method: "GET", headers: { Accept: "application/json" } })
      .then((response) => response.json())
      .then((payload) => polled(typeof payload.value === "string" ? payload.value : ""), () => schedulePoll());
  }

  // A background poll's source: still loading, keep watching; loading over,
  // refresh the Inspector once so it shows what came after.
  function polled(text) {
    if (!watching) return;
    if (!isLoading(text)) {
      stopWatch();
      if (!pressRefresh()) window.setTimeout(pressRefresh, 500);
      return;
    }
    unchangedPolls = text === lastSource ? unchangedPolls + 1 : 0;
    lastSource = text;
    if (unchangedPolls >= LOADING_STUCK_POLLS) stopWatch();
    else schedulePoll();
  }

  // Every source the Inspector itself fetched. The first one after an action
  // says whether the app is loading.
  function sourceSeen(text) {
    const first = afterAction;
    afterAction = false;
    lastSource = text;
    if (first && !watching && isLoading(text)) startWatch();
  }

  function watchFetch() {
    nativeFetch = window.fetch;
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
      if (isAction(method, pathname, init && init.body)) {
        afterAction = true;
        stopWatch();
      }
      return promise.then(
        (response) => {
          inFlight -= 1;
          if (method === "GET" && pathname.endsWith("/source")) {
            sourceUrl = url;
            response.clone().json().then((payload) => sourceSeen(typeof payload.value === "string" ? payload.value : ""), () => {});
          }
          return response;
        },
        (error) => {
          inFlight -= 1;
          throw error;
        },
      );
    };
  }

  // ---- the notes ----------------------------------------------------------

  const BAR_STYLE = [
    "#efp-inspector-hint,#efp-inspector-loading{position:fixed;left:0;right:0;bottom:0;z-index:2147483000;display:flex;align-items:center;gap:12px;padding:8px 16px;background:#1f2933;color:#f5f7fa;font:13px/1.5 system-ui,-apple-system,'Segoe UI',sans-serif;box-shadow:0 -2px 8px rgba(0,0,0,.25)}",
    "#efp-inspector-loading{left:auto;right:16px;bottom:16px;border-radius:8px;padding:6px 12px;background:#2d3a46}",
    "#efp-inspector-hint .efp-dot{flex:0 0 auto;width:10px;height:10px;border-radius:50%;background:#ff5a5f;animation:efp-rec 1.4s ease-in-out infinite}",
    "#efp-inspector-loading .efp-spin{flex:0 0 auto;width:12px;height:12px;border:2px solid rgba(255,255,255,.35);border-top-color:#fff;border-radius:50%;animation:efp-spin .9s linear infinite}",
    "#efp-inspector-hint p{flex:1 1 auto;margin:0}",
    "#efp-inspector-hint strong{color:#fff}",
    "#efp-inspector-hint button{flex:0 0 auto;padding:4px 12px;border:1px solid #9aa5b1;border-radius:6px;background:transparent;color:#f5f7fa;font:inherit;cursor:pointer}",
    "#efp-inspector-hint button:hover{background:rgba(255,255,255,.12)}",
    "@keyframes efp-rec{0%,100%{opacity:.35}50%{opacity:1}}",
    "@keyframes efp-spin{to{transform:rotate(360deg)}}",
  ].join("");
  let styleAdded = false;

  function ensureStyle() {
    if (styleAdded) return;
    styleAdded = true;
    const style = document.createElement("style");
    style.textContent = BAR_STYLE;
    document.head.appendChild(style);
  }

  function showLoadingBadge() {
    if (document.getElementById("efp-inspector-loading")) return;
    ensureStyle();
    const badge = document.createElement("div");
    badge.id = "efp-inspector-loading";
    badge.setAttribute("role", "status");
    badge.innerHTML = '<span class="efp-spin" aria-hidden="true"></span><span>The app is loading; the screen refreshes once it is done. You can keep working.</span>';
    document.body.appendChild(badge);
  }

  function hideLoadingBadge() {
    const badge = document.getElementById("efp-inspector-loading");
    if (badge) badge.remove();
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
    ensureStyle();
    const bar = document.createElement("div");
    bar.id = "efp-inspector-hint";
    bar.setAttribute("role", "status");
    bar.innerHTML =
      '<span class="efp-dot" aria-hidden="true"></span>' +
      "<p><strong>Recording.</strong> Every tap, swipe, and key you send through this Inspector is recorded by the local bridge; " +
      "the Inspector’s own Start Recording only shows code. Tap and swipe on the screenshot as on the phone. " +
      "While the app shows it is loading, the screen refreshes by itself once the loading is over; otherwise press Refresh Source & Screenshot when the device is ready. " +
      "To type into a field, switch to Select Elements, pick the field, and use Send Keys. " +
      "When you are done, go back to the Portal tab and press Save recording.</p>" +
      '<button type="button" data-efp-dismiss>Got it</button>';
    bar.querySelector("[data-efp-dismiss]").addEventListener("click", () => {
      remember();
      bar.remove();
    });
    document.body.appendChild(bar);
  }

  // ---- wiring -------------------------------------------------------------

  function tick() {
    applyMode();
    showHint();
    if (modeApplied && hintShown) observer.disconnect();
  }

  watchFetch();
  // A button pressed by the member (Refresh, a tab, a mode) means they took
  // over; the next action starts afresh. Taps on the screenshot are actions.
  document.addEventListener("pointerdown", (event) => {
    if (event.target && event.target.closest && event.target.closest("button")) stopWatch();
  }, true);
  const observer = new MutationObserver(tick);
  observer.observe(document.documentElement, { childList: true, subtree: true });
  tick();
})();
