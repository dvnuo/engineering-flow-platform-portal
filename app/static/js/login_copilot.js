/*
 * GitHub Copilot sign-in on the login page.
 *
 * Same device flow the runtime-profile panel uses to connect Copilot, run
 * before there is a session: start a flow, show the one-time code, poll
 * until GitHub reports it authorized. The server turns that authorization
 * into a portal account + session cookie, so the only thing this script
 * does with the result is follow the redirect.
 *
 * Choosing Copilot puts the card into "copilot mode": the other sign-in
 * method folds away so it cannot be clicked by accident, and a back link
 * restores the choice.
 */
(function () {
  var card = document.getElementById("login-card");
  if (!card) return;

  var authBase = (card.dataset.copilotAuthBase || "").trim() || "/api/auth/copilot";
  var intro = card.querySelector("[data-auth-intro]");
  var chooseButton = card.querySelector("[data-copilot-choose]");
  var panel = card.querySelector("[data-copilot-panel]");
  var backButton = card.querySelector("[data-copilot-back]");
  var startButton = card.querySelector("[data-copilot-start]");
  var steps = {
    enterprise: card.querySelector('[data-copilot-step="enterprise"]'),
    device: card.querySelector('[data-copilot-step="device"]'),
    done: card.querySelector('[data-copilot-step="done"]'),
  };
  var userCode = card.querySelector("[data-copilot-user-code]");
  var copyButton = card.querySelector("[data-copilot-copy]");
  var deviceLink = card.querySelector("[data-copilot-device-link]");
  var timerNode = card.querySelector("[data-copilot-timer]");
  var statusNode = card.querySelector("[data-copilot-status]");
  var errorNode = document.getElementById("login-error");
  if (!chooseButton || !panel || !startButton) return;

  var introText = intro ? intro.textContent : "";
  var startLabel = startButton.textContent;
  var pollTimer = null;
  var countdownTimer = null;

  function setError(message) {
    if (errorNode) errorNode.textContent = message || "";
  }

  function setStatus(message) {
    if (statusNode) statusNode.textContent = message || "";
  }

  function stopTimers() {
    if (pollTimer) clearInterval(pollTimer);
    if (countdownTimer) clearInterval(countdownTimer);
    pollTimer = null;
    countdownTimer = null;
  }

  function setStep(name) {
    Object.keys(steps).forEach(function (key) {
      var node = steps[key];
      if (!node) return;
      var order = ["enterprise", "device", "done"];
      var reached = order.indexOf(key) <= order.indexOf(name);
      node.classList.toggle("hidden", !reached);
      node.classList.toggle("is-active", key === name);
      node.classList.toggle("is-done", order.indexOf(key) < order.indexOf(name));
    });
  }

  function resetFlow() {
    stopTimers();
    startButton.disabled = false;
    startButton.textContent = startLabel;
    if (userCode) userCode.textContent = "";
    if (copyButton) copyButton.textContent = "Copy";
    setStatus("");
    setStep("enterprise");
  }

  function enterCopilotMode() {
    card.classList.add("is-copilot-mode");
    panel.classList.remove("hidden");
    chooseButton.setAttribute("aria-expanded", "true");
    chooseButton.classList.add("is-selected");
    if (intro) intro.textContent = "Sign in with GitHub Copilot";
    setError("");
    resetFlow();
    var firstAction = panel.querySelector("a, button");
    if (firstAction) firstAction.focus({ preventScroll: true });
  }

  function leaveCopilotMode() {
    resetFlow();
    card.classList.remove("is-copilot-mode");
    panel.classList.add("hidden");
    chooseButton.setAttribute("aria-expanded", "false");
    chooseButton.classList.remove("is-selected");
    if (intro) intro.textContent = introText;
    setError("");
    chooseButton.focus({ preventScroll: true });
  }

  async function postJson(url, body) {
    var response = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {}),
    });
    var data = null;
    try {
      data = await response.json();
    } catch (parseError) {
      data = null;
    }
    return { ok: response.ok, status: response.status, data: data || {} };
  }

  function describeFailure(data, fallback) {
    return data.message || data.detail || data.details || data.error || fallback;
  }

  function failAndReset(message) {
    setError(message);
    stopTimers();
    startButton.disabled = false;
    startButton.textContent = startLabel;
    setStep("enterprise");
  }

  chooseButton.addEventListener("click", function () {
    if (card.classList.contains("is-copilot-mode")) return;
    enterCopilotMode();
  });

  if (backButton) backButton.addEventListener("click", leaveCopilotMode);

  if (copyButton) {
    copyButton.addEventListener("click", async function () {
      var code = userCode ? userCode.textContent.trim() : "";
      if (!code) return;
      try {
        await navigator.clipboard.writeText(code);
        copyButton.textContent = "Copied";
        setTimeout(function () { copyButton.textContent = "Copy"; }, 1600);
      } catch (clipboardError) {
        copyButton.textContent = "Select & copy";
      }
    });
  }

  startButton.addEventListener("click", async function () {
    setError("");
    setStatus("");
    stopTimers();
    startButton.disabled = true;
    startButton.textContent = "Contacting GitHub…";

    var started;
    try {
      started = await postJson(authBase + "/start", {});
    } catch (networkError) {
      failAndReset("Could not reach the server. Check your connection and try again.");
      return;
    }
    var flow = started.data;
    if (!started.ok || flow.error || !flow.auth_id || !flow.device_code || !flow.user_code || !flow.flow_id) {
      failAndReset(describeFailure(flow, "Could not start GitHub authorization. Try again."));
      return;
    }

    if (userCode) userCode.textContent = flow.user_code;
    if (deviceLink) deviceLink.href = flow.verification_complete_url || flow.verification_url || "https://github.com/login/device";
    startButton.textContent = "Waiting for GitHub…";
    setStep("device");
    if (deviceLink) deviceLink.focus({ preventScroll: true });

    var remaining = Number(flow.expires_in || 600);
    function renderTimer() {
      if (!timerNode) return;
      var m = Math.floor(Math.max(remaining, 0) / 60);
      var s = Math.max(remaining, 0) % 60;
      timerNode.textContent = "code valid " + m + ":" + (s < 10 ? "0" : "") + s;
    }
    renderTimer();
    countdownTimer = setInterval(function () {
      remaining -= 1;
      renderTimer();
      if (remaining <= 0) failAndReset("The code expired before GitHub confirmed it. Start again.");
    }, 1000);

    var intervalMs = Math.max(Number(flow.interval || 5), 3) * 1000;
    pollTimer = setInterval(async function () {
      var checked;
      try {
        checked = await postJson(authBase + "/check", {
          flow_id: flow.flow_id,
          auth_id: flow.auth_id,
          device_code: flow.device_code,
        });
      } catch (networkError) {
        failAndReset("Lost contact with the server while waiting for GitHub. Start again.");
        return;
      }
      var result = checked.data;
      if (!checked.ok) {
        failAndReset(describeFailure(result, "Authorization check failed (HTTP " + checked.status + ")."));
        return;
      }
      if (result.status === "pending") return;
      if (result.status === "authorized") {
        stopTimers();
        setStep("done");
        card.classList.add("is-signed-in");
        setTimeout(function () {
          window.location.href = result.redirect || "/app";
        }, 500);
        return;
      }
      failAndReset(describeFailure(result, "GitHub authorization failed."));
    }, intervalMs);
  });
})();
