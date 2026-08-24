(function () {
  "use strict";

  function panelRoot(target) {
    return target && target.closest ? target.closest("#admin-users-panel") : null;
  }

  function feedback(root, message, isError) {
    var node = root && root.querySelector("[data-admin-users-feedback]");
    if (!node) return;
    node.textContent = message || "";
    node.classList.toggle("is-error", !!isError);
  }

  async function errorMessage(response) {
    try {
      var payload = await response.json();
      if (typeof payload.detail === "string") return payload.detail;
    } catch (_error) {}
    return "Request failed (" + response.status + ")";
  }

  async function request(url, options) {
    var response = await fetch(url, options || {});
    if (!response.ok) throw new Error(await errorMessage(response));
    if (response.status === 204) return null;
    return response.json();
  }

  async function reloadPanel() {
    await htmx.ajax("GET", "/app/users/panel", {
      target: "#tool-panel-body",
      swap: "innerHTML",
    });
  }

  document.addEventListener("submit", async function (event) {
    var allowlistForm = event.target.closest("[data-admin-allowlist-form]");
    var memberForm = event.target.closest("[data-admin-member-form]");
    var passwordForm = event.target.closest("[data-admin-password-form]");
    if (!allowlistForm && !memberForm && !passwordForm) return;
    event.preventDefault();

    var form = allowlistForm || memberForm || passwordForm;
    var root = panelRoot(form);
    var submit = form.querySelector('button[type="submit"]');
    if (submit) submit.disabled = true;
    feedback(root, "Saving…", false);

    try {
      var data = new FormData(form);
      if (allowlistForm) {
        await request("/api/users/allowlist", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ username: data.get("username"), role: data.get("role") }),
        });
      } else if (memberForm) {
        var userId = memberForm.dataset.userId;
        await request("/api/users/" + encodeURIComponent(userId), {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            role: data.get("role"),
            is_active: memberForm.querySelector('[name="is_active"]').checked,
          }),
        });
      } else {
        var passwordUserId = passwordForm.dataset.userId;
        await request("/api/users/" + encodeURIComponent(passwordUserId) + "/password", {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ password: data.get("password") }),
        });
      }
      await reloadPanel();
    } catch (error) {
      feedback(root, error.message, true);
      if (window.showAlert) {
        await window.showAlert({ title: "Member administration", message: error.message });
      }
    } finally {
      if (submit) submit.disabled = false;
    }
  });

  document.addEventListener("click", async function (event) {
    var button = event.target.closest("[data-remove-allowlist]");
    if (!button) return;
    event.preventDefault();
    var root = panelRoot(button);
    var username = button.dataset.allowlistUsername || "this member";
    var confirmed = window.showConfirm
      ? await window.showConfirm({
          title: "Revoke allowlist access",
          message: username + " will immediately lose registration, sign-in, and session access.",
          confirmText: "Revoke access",
          danger: true,
        })
      : false;
    if (!confirmed) return;

    button.disabled = true;
    try {
      await request("/api/users/allowlist/" + encodeURIComponent(button.dataset.removeAllowlist), {
        method: "DELETE",
      });
      await reloadPanel();
    } catch (error) {
      feedback(root, error.message, true);
      if (window.showAlert) {
        await window.showAlert({ title: "Unable to revoke access", message: error.message });
      }
      button.disabled = false;
    }
  });
})();
