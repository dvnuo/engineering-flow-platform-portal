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

  function openAllowlistModal() {
    var root = document.getElementById("admin-users-panel");
    var modal = root && root.querySelector("[data-admin-allowlist-modal]");
    if (!modal) return;
    feedback(root, "", false);
    modal.classList.remove("hidden");
    modal.setAttribute("aria-hidden", "false");
    window.setTimeout(function () {
      var textarea = modal.querySelector('textarea[name="usernames"]');
      if (textarea) textarea.focus();
    }, 0);
  }

  function closeAllowlistModal() {
    var modal = document.querySelector("[data-admin-allowlist-modal]");
    if (!modal || modal.classList.contains("hidden")) return;
    modal.classList.add("hidden");
    modal.setAttribute("aria-hidden", "true");
    var trigger = document.getElementById("header-add-allowlist-btn");
    if (trigger && !trigger.classList.contains("hidden")) trigger.focus();
  }

  function parseUsernames(value) {
    var seen = Object.create(null);
    return String(value || "")
      .split(/\r?\n/)
      .map(function (username) { return username.trim().toLowerCase(); })
      .filter(function (username) {
        if (!username || seen[username]) return false;
        seen[username] = true;
        return true;
      });
  }

  async function errorMessage(response) {
    try {
      var payload = await response.json();
      if (typeof payload.detail === "string") return payload.detail;
      if (Array.isArray(payload.detail)) {
        return payload.detail.map(function (item) { return item.msg; }).filter(Boolean).join("; ");
      }
    } catch (_error) {}
    return "Request failed (" + response.status + ")";
  }

  async function request(url, options) {
    var response = await fetch(url, options || {});
    if (!response.ok) throw new Error(await errorMessage(response));
    if (response.status === 204) return null;
    return response.json();
  }

  async function reloadPanel(message, isError) {
    await htmx.ajax("GET", "/app/users/panel", {
      target: "#workspace-detail-content",
      swap: "innerHTML",
    });
    feedback(document.getElementById("admin-users-panel"), message, isError);
  }

  function applyMemberFilters(root) {
    if (!root) return;
    var search = root.querySelector("[data-admin-member-search]");
    var accessFilter = root.querySelector("[data-admin-member-access-filter]");
    var query = String(search && search.value || "").trim().toLowerCase();
    var access = String(accessFilter && accessFilter.value || "all");
    var cards = Array.prototype.slice.call(root.querySelectorAll("[data-member-id]"));
    var visible = 0;

    cards.forEach(function (card) {
      var identity = ((card.dataset.memberName || "") + " " + (card.dataset.memberUsername || "")).toLowerCase();
      var matchesQuery = !query || identity.indexOf(query) !== -1;
      var matchesAccess = access === "all" || card.dataset.memberAccess === access;
      card.hidden = !(matchesQuery && matchesAccess);
      if (!card.hidden) visible += 1;
    });

    var resultCount = root.querySelector("[data-admin-member-result-count]");
    if (resultCount) resultCount.textContent = visible + " shown";
    var empty = root.querySelector("[data-admin-member-filter-empty]");
    if (empty) empty.hidden = visible !== 0;
  }

  document.addEventListener("input", function (event) {
    var textarea = event.target.closest('[data-admin-allowlist-form] textarea[name="usernames"]');
    if (textarea) {
      var count = panelRoot(textarea).querySelector("[data-admin-allowlist-count]");
      if (count) count.textContent = String(parseUsernames(textarea.value).length);
      return;
    }
    if (event.target.closest("[data-admin-member-search]")) {
      applyMemberFilters(panelRoot(event.target));
    }
  });

  document.addEventListener("submit", async function (event) {
    var form = event.target.closest("[data-admin-allowlist-form]");
    if (!form) return;
    event.preventDefault();

    var root = panelRoot(form);
    var submit = form.querySelector('button[type="submit"]');
    var data = new FormData(form);
    var usernames = parseUsernames(data.get("usernames"));
    if (!usernames.length) {
      feedback(root, "Enter at least one username.", true);
      return;
    }

    if (submit) submit.disabled = true;
    feedback(root, "Adding " + usernames.length + " username" + (usernames.length === 1 ? "" : "s") + "…", false);

    try {
      var result = await request("/api/users/allowlist/bulk", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ usernames: usernames, role: data.get("role") }),
      });
      var messages = [];
      if (result.added.length) messages.push("Allowed " + result.added.length + ".");
      if (result.already_allowlisted.length) messages.push("Skipped " + result.already_allowlisted.length + " already allowed.");
      var successMessage = messages.join(" ") || "No changes were needed.";
      closeAllowlistModal();
      await reloadPanel(successMessage, false);
      if (window.showToast) window.showToast(successMessage, { variant: "success" });
    } catch (error) {
      feedback(root, error.message, true);
      if (window.showAlert) {
        await window.showAlert({ title: "Unable to update allowlist", message: error.message });
      }
      if (submit) submit.disabled = false;
    }
  });

  document.addEventListener("change", async function (event) {
    if (event.target.closest("[data-admin-member-access-filter]")) {
      applyMemberFilters(panelRoot(event.target));
      return;
    }

    var select = event.target.closest("[data-admin-role-select]");
    if (!select) return;
    var previousRole = select.dataset.originalRole;
    var nextRole = select.value;
    if (previousRole === nextRole) return;

    var card = select.closest("[data-member-id]");
    var statusNode = card && card.querySelector("[data-admin-role-status]");
    select.disabled = true;
    if (statusNode) {
      statusNode.textContent = "Saving…";
      statusNode.classList.remove("is-error", "is-success");
    }

    try {
      var updated = await request("/api/users/" + encodeURIComponent(select.dataset.userId), {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ role: nextRole }),
      });
      select.dataset.originalRole = updated.role;
      var adminSummary = panelRoot(select).querySelector("[data-admin-summary-admins]");
      var isAllowedMember = card && card.dataset.memberAccess === "allowed";
      if (adminSummary && isAllowedMember && (previousRole === "admin") !== (updated.role === "admin")) {
        var adminCount = Number(adminSummary.textContent) || 0;
        adminSummary.textContent = String(adminCount + (updated.role === "admin" ? 1 : -1));
      }
      if (statusNode) {
        statusNode.textContent = "Saved";
        statusNode.classList.add("is-success");
      }
    } catch (error) {
      select.value = previousRole;
      if (statusNode) {
        statusNode.textContent = "Not saved";
        statusNode.classList.add("is-error");
      }
      if (window.showAlert) {
        await window.showAlert({ title: "Unable to change role", message: error.message });
      }
    } finally {
      select.disabled = false;
    }
  });

  document.addEventListener("click", async function (event) {
    var openButton = event.target.closest("[data-open-admin-allowlist-modal]");
    if (openButton) {
      event.preventDefault();
      openAllowlistModal();
      return;
    }

    var closeButton = event.target.closest("[data-close-admin-allowlist-modal]");
    if (closeButton) {
      event.preventDefault();
      closeAllowlistModal();
      return;
    }

    var modalBackdrop = event.target.closest("[data-admin-allowlist-modal]");
    if (modalBackdrop && event.target === modalBackdrop) {
      closeAllowlistModal();
      return;
    }

    var allowButton = event.target.closest("[data-allow-member]");
    if (allowButton) {
      event.preventDefault();
      var allowRoot = panelRoot(allowButton);
      var allowCard = allowButton.closest("[data-member-id]");
      var roleSelect = allowCard && allowCard.querySelector("[data-admin-role-select]");
      var username = allowButton.dataset.username;
      allowButton.disabled = true;
      try {
        await request("/api/users/allowlist", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ username: username, role: roleSelect ? roleSelect.value : "user" }),
        });
        await reloadPanel(username + " is now allowed.", false);
      } catch (error) {
        feedback(allowRoot, error.message, true);
        if (window.showAlert) {
          await window.showAlert({ title: "Unable to allow member", message: error.message });
        }
        allowButton.disabled = false;
      }
      return;
    }

    var button = event.target.closest("[data-remove-allowlist]");
    if (!button) return;
    event.preventDefault();
    var root = panelRoot(button);
    var removeUsername = button.dataset.allowlistUsername || "this member";
    var confirmed = window.showConfirm
      ? await window.showConfirm({
          title: "Revoke allowlist access",
          message: removeUsername + " will immediately lose registration, sign-in, and session access.",
          confirmText: "Revoke access",
          danger: true,
        })
      : window.confirm("Revoke allowlist access for " + removeUsername + "?");
    if (!confirmed) return;

    button.disabled = true;
    try {
      await request("/api/users/allowlist/" + encodeURIComponent(button.dataset.removeAllowlist), {
        method: "DELETE",
      });
      await reloadPanel(removeUsername + " no longer has access.", false);
    } catch (error) {
      feedback(root, error.message, true);
      if (window.showAlert) {
        await window.showAlert({ title: "Unable to revoke access", message: error.message });
      }
      button.disabled = false;
    }
  });

  document.addEventListener("keydown", function (event) {
    if (event.key !== "Escape") return;
    var modal = document.querySelector("[data-admin-allowlist-modal]");
    if (!modal || modal.classList.contains("hidden")) return;
    event.preventDefault();
    closeAllowlistModal();
  });
})();
