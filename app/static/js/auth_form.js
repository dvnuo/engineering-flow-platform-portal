/*
 * Shared submit handling for the sign-in and registration forms.
 *
 * Both pages previously awaited a bare fetch() with no try/catch and no
 * pending state: a network failure (server down, VPN dropped) rejected into
 * nothing, so the page just sat there with no message, and nothing stopped a
 * user from firing the request repeatedly on a slow connection.
 */
function initAuthForm(options) {
  var form = document.getElementById(options.formId);
  var errorNode = document.getElementById(options.errorId);
  if (!form || !errorNode) return;

  var submitButton = form.querySelector('button[type="submit"]');

  function showError(message) {
    errorNode.textContent = message || "";
  }

  function setPending(pending) {
    if (!submitButton) return;
    submitButton.disabled = pending;
    submitButton.textContent = pending ? options.pendingText : options.submitText;
  }

  form.addEventListener("submit", async function (event) {
    event.preventDefault();
    showError("");

    var data = Object.fromEntries(new FormData(form).entries());
    var validationError = options.validate ? options.validate(data) : null;
    if (validationError) {
      showError(validationError);
      return;
    }

    setPending(true);
    var response;
    try {
      response = await fetch(options.endpoint, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(options.buildPayload(data)),
      });
    } catch (networkError) {
      // The case that used to fail completely silently.
      showError("Could not reach the server. Check your connection and try again.");
      setPending(false);
      return;
    }

    if (!response.ok) {
      var message = options.fallbackError;
      try {
        var payload = await response.json();
        if (typeof payload.detail === "string" && payload.detail.trim()) {
          message = payload.detail;
        } else if (Array.isArray(payload.detail)) {
          message = payload.detail.map(function (item) { return item.msg; }).filter(Boolean).join("; ") || message;
        }
      } catch (parseError) { /* keep the fallback */ }
      showError(message);
      setPending(false);
      return;
    }

    // Leave the button disabled through the navigation so a double submit
    // can't race the redirect.
    window.location.href = "/app";
  });
}

// Password reveal, so people can check what they typed before submitting.
document.addEventListener("click", function (event) {
  var toggle = event.target.closest("[data-toggle-password]");
  if (!toggle) return;
  var field = toggle.parentElement && toggle.parentElement.querySelector("input");
  if (!field) return;
  var reveal = field.type === "password";
  field.type = reveal ? "text" : "password";
  toggle.textContent = reveal ? "Hide" : "Show";
  toggle.setAttribute("aria-pressed", reveal ? "true" : "false");
  toggle.setAttribute("aria-label", reveal ? "Hide password" : "Show password");
});
