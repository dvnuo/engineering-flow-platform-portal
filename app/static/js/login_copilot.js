/*
 * GitHub Copilot sign-in on the login page.
 *
 * Same device flow the runtime-profile panel uses to connect Copilot, run
 * before there is a session: start a flow, show the one-time code, poll
 * until GitHub reports it authorized. The server turns that authorization
 * into a portal account + session cookie, so the only thing this script
 * does with the result is follow the redirect.
 */
(function () {
  var card = document.getElementById("login-card");
  if (!card) return;

  var authBase = (card.dataset.copilotAuthBase || "").trim() || "/api/auth/copilot";
  var chooseButton = card.querySelector("[data-copilot-choose]");
  var panel = card.querySelector("[data-copilot-panel]");
  var startButton = card.querySelector("[data-copilot-start]");
  var deviceStep = card.querySelector('[data-copilot-step="device"]');
  var userCode = card.querySelector("[data-copilot-user-code]");
  var deviceLink = card.querySelector("[data-copilot-device-link]");
  var timerNode = card.querySelector("[data-copilot-timer]");
  var statusNode = card.querySelector("[data-copilot-status]");
  var errorNode = document.getElementById("login-error");
  if (!chooseButton || !panel || !startButton) return;

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

  function resetStart(label) {
    stopTimers();
    startButton.disabled = false;
    if (label) startButton.textContent = label;
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

  chooseButton.addEventListener("click", function () {
    var open = panel.classList.contains("hidden");
    panel.classList.toggle("hidden", !open);
    chooseButton.setAttribute("aria-expanded", open ? "true" : "false");
    setError("");
  });

  startButton.addEventListener("click", async function () {
    setError("");
    setStatus("");
    stopTimers();
    startButton.disabled = true;
    var originalLabel = startButton.textContent;
    startButton.textContent = "Starting…";

    var started;
    try {
      started = await postJson(authBase + "/start", {});
    } catch (networkError) {
      setError("Could not reach the server. Check your connection and try again.");
      resetStart(originalLabel);
      return;
    }
    var flow = started.data;
    if (!started.ok || flow.error || !flow.auth_id || !flow.device_code || !flow.user_code || !flow.flow_id) {
      setError(describeFailure(flow, "Could not start GitHub authorization. Try again."));
      resetStart(originalLabel);
      return;
    }

    if (userCode) userCode.textContent = flow.user_code;
    if (deviceLink) deviceLink.href = flow.verification_complete_url || flow.verification_url || "https://github.com/login/device";
    if (deviceStep) deviceStep.classList.remove("hidden");
    startButton.textContent = "Waiting for GitHub…";
    setStatus("Waiting for you to authorize on GitHub. This page updates on its own.");

    var remaining = Number(flow.expires_in || 600);
    if (timerNode) timerNode.textContent = remaining + "s";
    countdownTimer = setInterval(function () {
      remaining -= 1;
      if (timerNode) timerNode.textContent = Math.max(remaining, 0) + "s";
      if (remaining <= 0) {
        setError("Authorization timed out. Start again.");
        resetStart(originalLabel);
      }
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
        setError("Lost contact with the server while waiting for GitHub. Start again.");
        resetStart(originalLabel);
        return;
      }
      var result = checked.data;
      if (!checked.ok) {
        setError(describeFailure(result, "Authorization check failed (HTTP " + checked.status + ")."));
        resetStart(originalLabel);
        return;
      }
      if (result.status === "pending") return;
      if (result.status === "authorized") {
        stopTimers();
        setStatus("Signed in. Taking you to the portal…");
        window.location.href = result.redirect || "/app";
        return;
      }
      setError(describeFailure(result, "GitHub authorization failed."));
      resetStart(originalLabel);
    }, intervalMs);
  });
})();
