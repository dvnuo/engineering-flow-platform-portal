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

  function checkRoleOption(group, role) {
    var options = group ? group.querySelectorAll("[data-admin-role-option]") : [];
    Array.prototype.forEach.call(options, function (input) {
      input.checked = input.value === role;
    });
  }

  function selectedRole(scope) {
    var checked = scope && scope.querySelector("[data-admin-role-option]:checked");
    return checked ? checked.value : "user";
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
      // Portal details are English keys (N_() at the raise site), shown translated.
      if (typeof payload.detail === "string") return tv(payload.detail);
      if (Array.isArray(payload.detail)) {
        return payload.detail.map(function (item) { return item.msg; }).filter(Boolean).join("; ");
      }
    } catch (_error) {}
    return t("Request failed ({status})", { status: response.status });
  }

  async function request(url, options) {
    var response = await fetch(url, options || {});
    if (!response.ok) throw new Error(await errorMessage(response));
    if (response.status === 204) return null;
    return response.json();
  }

  // The panel keeps the period its consumption columns are summed over.
  function selectedPeriodDays() {
    var root = document.getElementById("admin-users-panel");
    var select = root && root.querySelector("[data-admin-member-period]");
    return String(select && select.value || root && root.dataset.periodDays || "30");
  }

  async function reloadPanel(message, isError) {
    await htmx.ajax("GET", "/app/users/panel?days=" + encodeURIComponent(selectedPeriodDays()), {
      target: "#workspace-detail-content",
      swap: "innerHTML",
    });
    feedback(document.getElementById("admin-users-panel"), message, isError);
  }

  // ---- the member table: sorting by a column, export of what is shown ----

  function memberRows(root) {
    var body = root && root.querySelector("[data-admin-member-rows]");
    return body ? Array.prototype.slice.call(body.querySelectorAll("tr[data-member-id]")) : [];
  }

  function cellSortValue(cell) {
    if (!cell) return "";
    var raw = cell.dataset.sortValue;
    if (raw === undefined) return cell.textContent.trim().toLowerCase();
    var number = Number(raw);
    return Number.isFinite(number) && raw.trim() !== "" ? number : String(raw).toLowerCase();
  }

  function sortMemberTable(root, header) {
    var table = header.closest("table");
    var headers = Array.prototype.slice.call(table.querySelectorAll("th[data-sort-key]"));
    var index = Array.prototype.indexOf.call(header.parentNode.children, header);
    var direction = header.getAttribute("aria-sort") === "ascending" ? "descending" : "ascending";
    // Numbers sort descending first (the big consumers on top), text ascending.
    if (header.getAttribute("aria-sort") === "none" || !header.getAttribute("aria-sort")) {
      direction = header.dataset.sortType === "number" ? "descending" : "ascending";
    }
    headers.forEach(function (th) { th.setAttribute("aria-sort", th === header ? direction : "none"); });
    var rows = memberRows(root);
    var sign = direction === "ascending" ? 1 : -1;
    rows.sort(function (a, b) {
      var left = cellSortValue(a.children[index]);
      var right = cellSortValue(b.children[index]);
      if (left === right) return 0;
      if (typeof left === "number" && typeof right === "number") return (left - right) * sign;
      return String(left).localeCompare(String(right)) * sign;
    });
    var body = root.querySelector("[data-admin-member-rows]");
    rows.forEach(function (row) { body.appendChild(row); });
  }

  function csvField(value) {
    var text = String(value === undefined || value === null ? "" : value).replace(/\s+/g, " ").trim();
    return /[",\n;]/.test(text) ? "\"" + text.replace(/"/g, "\"\"") + "\"" : text;
  }

  function exportMembersCsv(root) {
    var table = root && root.querySelector("[data-admin-member-table]");
    if (!table) return;
    var headers = Array.prototype.slice.call(table.querySelectorAll("thead th"))
      .filter(function (th) { return !th.hasAttribute("data-no-export"); })
      .map(function (th) { return csvField(th.dataset.exportLabel || th.textContent); });
    var lines = [headers.join(",")];
    memberRows(root).forEach(function (row) {
      if (row.hidden) return;
      var cells = Array.prototype.slice.call(row.children).filter(function (td) { return !td.hasAttribute("data-no-export"); });
      lines.push(cells.map(function (td) { return csvField(td.dataset.exportValue !== undefined ? td.dataset.exportValue : td.textContent); }).join(","));
    });
    var blob = new Blob([String.fromCharCode(0xFEFF) + lines.join("\r\n") + "\r\n"], { type: "text/csv;charset=utf-8" });
    var link = document.createElement("a");
    link.href = URL.createObjectURL(blob);
    link.download = "efp-members-" + selectedPeriodDays() + "d-" + new Date().toISOString().slice(0, 10) + ".csv";
    document.body.appendChild(link);
    link.click();
    window.setTimeout(function () {
      URL.revokeObjectURL(link.href);
      link.remove();
    }, 0);
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
    if (resultCount) resultCount.textContent = t("{n} shown", { n: visible });
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
      feedback(root, t("Enter at least one username."), true);
      return;
    }

    if (submit) submit.disabled = true;
    feedback(root, tn(usernames.length, "Adding {n} username…", "Adding {n} usernames…"), false);

    try {
      var result = await request("/api/users/allowlist/bulk", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ usernames: usernames, role: data.get("role") }),
      });
      var messages = [];
      if (result.added.length) messages.push(t("Allowed {n}.", { n: result.added.length }));
      if (result.already_allowlisted.length) messages.push(t("Skipped {n} already allowed.", { n: result.already_allowlisted.length }));
      var successMessage = messages.join(" ") || t("No changes were needed.");
      closeAllowlistModal();
      await reloadPanel(successMessage, false);
      if (window.showToast) window.showToast(successMessage, { variant: "success" });
    } catch (error) {
      feedback(root, error.message, true);
      if (window.showAlert) {
        await window.showAlert({ title: t("Unable to update allowlist"), message: error.message });
      }
      if (submit) submit.disabled = false;
    }
  });

  document.addEventListener("change", async function (event) {
    if (event.target.closest("[data-admin-member-access-filter]")) {
      applyMemberFilters(panelRoot(event.target));
      return;
    }
    if (event.target.closest("[data-admin-member-period]")) {
      await reloadPanel("", false);
      return;
    }

    var option = event.target.closest("[data-admin-role-option]");
    if (!option) return;
    var group = option.closest("[data-admin-role-group]");
    if (!group) return;
    var previousRole = group.dataset.originalRole;
    var nextRole = option.value;
    if (previousRole === nextRole) return;

    var card = group.closest("[data-member-id]");
    var statusNode = card && card.querySelector("[data-admin-role-status]");
    group.classList.add("is-saving");
    if (statusNode) {
      statusNode.textContent = t("Saving…");
      statusNode.classList.remove("is-error", "is-success");
    }

    try {
      var updated = await request("/api/users/" + encodeURIComponent(group.dataset.userId), {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ role: nextRole }),
      });
      group.dataset.originalRole = updated.role;
      checkRoleOption(group, updated.role);
      var adminSummary = panelRoot(group).querySelector("[data-admin-summary-admins]");
      var isAllowedMember = card && card.dataset.memberAccess === "allowed";
      if (adminSummary && isAllowedMember && (previousRole === "admin") !== (updated.role === "admin")) {
        var adminCount = Number(adminSummary.textContent) || 0;
        adminSummary.textContent = String(adminCount + (updated.role === "admin" ? 1 : -1));
      }
      if (statusNode) {
        statusNode.textContent = t("Saved");
        statusNode.classList.add("is-success");
      }
    } catch (error) {
      // Put the pill back where it was so the control never shows a role the
      // server did not accept.
      checkRoleOption(group, previousRole);
      if (statusNode) {
        statusNode.textContent = t("Not saved");
        statusNode.classList.add("is-error");
      }
      if (window.showAlert) {
        await window.showAlert({ title: t("Unable to change role"), message: error.message });
      }
    } finally {
      group.classList.remove("is-saving");
    }
  });

  document.addEventListener("click", async function (event) {
    var openButton = event.target.closest("[data-open-admin-allowlist-modal]");
    if (openButton) {
      event.preventDefault();
      openAllowlistModal();
      return;
    }

    var sortHeader = event.target.closest("#admin-users-panel th[data-sort-key]");
    if (sortHeader) {
      event.preventDefault();
      sortMemberTable(panelRoot(sortHeader), sortHeader);
      return;
    }

    var exportButton = event.target.closest("[data-admin-member-export]");
    if (exportButton) {
      event.preventDefault();
      exportMembersCsv(panelRoot(exportButton));
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
      var username = allowButton.dataset.username;
      allowButton.disabled = true;
      try {
        await request("/api/users/allowlist", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ username: username, role: selectedRole(allowCard) }),
        });
        await reloadPanel(t("{username} is now allowed.", { username: username }), false);
      } catch (error) {
        feedback(allowRoot, error.message, true);
        if (window.showAlert) {
          await window.showAlert({ title: t("Unable to allow member"), message: error.message });
        }
        allowButton.disabled = false;
      }
      return;
    }

    var button = event.target.closest("[data-remove-allowlist]");
    if (!button) return;
    event.preventDefault();
    var root = panelRoot(button);
    var removeUsername = button.dataset.allowlistUsername || t("this member");
    var confirmed = window.showConfirm
      ? await window.showConfirm({
          title: t("Revoke allowlist access"),
          message: t("{username} will immediately lose registration, sign-in, and session access.", { username: removeUsername }),
          confirmText: t("Revoke access"),
          danger: true,
        })
      : window.confirm(t("Revoke allowlist access for {username}?", { username: removeUsername }));
    if (!confirmed) return;

    button.disabled = true;
    try {
      await request("/api/users/allowlist/" + encodeURIComponent(button.dataset.removeAllowlist), {
        method: "DELETE",
      });
      await reloadPanel(t("{username} no longer has access.", { username: removeUsername }), false);
    } catch (error) {
      feedback(root, error.message, true);
      if (window.showAlert) {
        await window.showAlert({ title: t("Unable to revoke access"), message: error.message });
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
