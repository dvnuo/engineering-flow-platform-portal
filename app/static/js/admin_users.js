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

  function openAllowlistModal(prefillUsername) {
    var root = document.getElementById("admin-users-panel");
    var modal = root && root.querySelector("[data-admin-allowlist-modal]");
    if (!modal) return;
    feedback(root, "", false);
    modal.classList.remove("hidden");
    modal.setAttribute("aria-hidden", "false");
    var textarea = modal.querySelector('textarea[name="usernames"]');
    if (textarea && prefillUsername) {
      textarea.value = prefillUsername;
      var count = modal.querySelector("[data-admin-allowlist-count]");
      if (count) count.textContent = String(parseUsernames(textarea.value).length);
    }
    window.setTimeout(function () {
      // With the username filled in, what is left to choose is the group.
      var focus = prefillUsername ? modal.querySelector("[data-admin-allowlist-group]") || textarea : textarea;
      if (focus) focus.focus();
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

  function isAdminRole(role) {
    return role === "admin" || role === "group_admin";
  }

  function roleWord(role) {
    if (role === "admin") return t("Admin");
    if (role === "group_admin") return t("Group admin");
    return t("User");
  }

  // The row's role select, or (own row, a system administrator's row seen by a
  // group administrator) the role it shows as text.
  function selectedRole(row) {
    var select = row && row.querySelector("[data-admin-role-select]");
    if (select) return select.value;
    var role = row && row.dataset.memberRole;
    return isAdminRole(role) ? role : "user";
  }

  // Saved as soon as it changes, like the group. Granting or removing Admin
  // (every group, member and assistant) asks first.
  async function changeRole(select) {
    var root = panelRoot(select);
    var row = select.closest("tr[data-member-id]");
    var previousRole = select.dataset.originalRole || "user";
    var nextRole = select.value;
    if (previousRole === nextRole) return;
    var username = select.dataset.memberUsername || t("this member");
    if (previousRole === "admin" || nextRole === "admin") {
      var title = t("Change {username}'s role to {role}?", { username: username, role: roleWord(nextRole) });
      var confirmed = window.showConfirm
        ? await window.showConfirm({
            title: title,
            message: nextRole === "admin"
              ? t("An Admin manages every group, member and assistant on the Portal.")
              : t("They lose the Admin role's access to every group, member and assistant."),
            confirmText: t("Change role"),
          })
        : window.confirm(title);
      if (!confirmed) {
        select.value = previousRole;
        return;
      }
    }
    select.classList.add("is-saving");
    try {
      var updated = await request("/api/users/" + encodeURIComponent(select.dataset.userId), {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ role: nextRole }),
      });
      var savedRole = isAdminRole(updated && updated.role) ? updated.role : "user";
      select.dataset.originalRole = savedRole;
      select.value = savedRole;
      if (row) {
        row.dataset.memberRole = savedRole;
        var cell = select.closest("td");
        if (cell) {
          cell.dataset.sortValue = roleWord(savedRole).toLowerCase();
          cell.dataset.exportValue = roleWord(savedRole);
        }
      }
      refreshFilterCounts(root);
      applyMemberFilters(root);
      if (window.showToast) {
        window.showToast(t("{username} is now {role}.", { username: username, role: roleWord(savedRole) }), { variant: "success" });
      }
    } catch (error) {
      // Put the select back so it never shows a role the server did not accept.
      select.value = previousRole;
      feedback(root, error.message, true);
      if (window.showAlert) {
        await window.showAlert({ title: t("Unable to change role"), message: error.message });
      }
    } finally {
      select.classList.remove("is-saving");
    }
  }

  function groupIdOf(value) {
    var id = Number(value);
    return Number.isInteger(id) && id > 0 ? id : undefined;
  }

  // A system administrator moves a member to another group from the row's
  // select: the member follows that group's Default connectors from then on,
  // and a group administrator becomes a member of the new group.
  async function moveMember(select) {
    var root = panelRoot(select);
    var previous = select.dataset.originalGroup || "";
    var username = select.dataset.memberUsername || t("this member");
    var label = select.options[select.selectedIndex] ? select.options[select.selectedIndex].text : "";
    var confirmed = window.showConfirm
      ? await window.showConfirm({
          title: t("Move {username} to {group}?", { username: username, group: label }),
          message: t("They follow {group}'s Default connectors from now on; their idle assistants restart to pick that up. A group administrator becomes a member of the new group.", { group: label }),
          confirmText: t("Move member"),
        })
      : window.confirm(t("Move {username} to {group}?", { username: username, group: label }));
    if (!confirmed) {
      select.value = previous;
      return;
    }
    select.disabled = true;
    try {
      await request("/api/users/" + encodeURIComponent(select.dataset.userId), {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ group_id: groupIdOf(select.value) }),
      });
      await reloadPanel(t("{username} is now in {group}.", { username: username, group: label }), false);
    } catch (error) {
      select.value = previous;
      select.disabled = false;
      feedback(root, error.message, true);
      if (window.showAlert) {
        await window.showAlert({ title: t("Unable to move member"), message: error.message });
      }
    }
  }

  // ---- several members at once: pick rows, move them to one group ----

  // The box last clicked, so a shift-click picks every shown row between. An
  // element, not a member id: once the panel is loaded again it is no longer
  // in the page and a shift-click starts over.
  var selectionAnchor = null;

  // Ids per request; the server takes up to 1000.
  var MOVE_BATCH_SIZE = 500;

  function selectBoxes(root) {
    return root ? Array.prototype.slice.call(root.querySelectorAll("[data-admin-member-select]")) : [];
  }

  function selectedMemberRows(root) {
    return selectBoxes(root)
      .filter(function (box) { return box.checked; })
      .map(function (box) { return box.closest("tr[data-member-id]"); })
      .filter(Boolean);
  }

  // The header box, the row tint and the bar follow the boxes. A row the
  // filters hide stays selected (search, pick, search again), and the bar
  // says how many of the selected are out of sight.
  function refreshSelection(root) {
    var boxes = selectBoxes(root);
    if (!boxes.length) return;
    var selected = 0;
    var hiddenSelected = 0;
    var shown = 0;
    var shownSelected = 0;
    boxes.forEach(function (box) {
      var row = box.closest("tr[data-member-id]");
      var visible = !!row && !row.hidden;
      if (row) row.classList.toggle("is-selected", box.checked);
      if (visible) shown += 1;
      if (!box.checked) return;
      selected += 1;
      if (visible) shownSelected += 1;
      else hiddenSelected += 1;
    });
    var all = root.querySelector("[data-admin-member-select-all]");
    if (all) {
      all.checked = shown > 0 && shownSelected === shown;
      all.indeterminate = shownSelected > 0 && shownSelected < shown;
      all.disabled = shown === 0;
    }
    var bar = root.querySelector("[data-admin-member-bulk-bar]");
    if (!bar) return;
    bar.hidden = selected === 0;
    var count = bar.querySelector("[data-admin-member-selected-count]");
    if (count) {
      var text = tn(selected, "{n} member selected", "{n} members selected");
      if (hiddenSelected) text += " \u00b7 " + t("{n} hidden by the filters", { n: hiddenSelected });
      count.textContent = text;
    }
    var target = bar.querySelector("[data-admin-member-bulk-group]");
    var move = bar.querySelector("[data-admin-member-bulk-move]");
    if (move) move.disabled = selected === 0 || !groupIdOf(target && target.value);
  }

  function pickMember(box, extend) {
    var root = panelRoot(box);
    if (extend && selectionAnchor && root && root.contains(selectionAnchor)) {
      var rows = memberRows(root).filter(function (row) { return !row.hidden; });
      var from = rows.indexOf(selectionAnchor.closest("tr[data-member-id]"));
      var to = rows.indexOf(box.closest("tr[data-member-id]"));
      if (from !== -1 && to !== -1) {
        rows.slice(Math.min(from, to), Math.max(from, to) + 1).forEach(function (row) {
          var other = row.querySelector("[data-admin-member-select]");
          if (other) other.checked = box.checked;
        });
      }
    }
    selectionAnchor = box;
    refreshSelection(root);
  }

  // The header box picks (or drops) every row shown; hidden rows keep their state.
  function pickShownMembers(root, checked) {
    memberRows(root).forEach(function (row) {
      if (row.hidden) return;
      var box = row.querySelector("[data-admin-member-select]");
      if (box) box.checked = checked;
    });
    selectionAnchor = null;
    refreshSelection(root);
  }

  function clearSelection(root) {
    selectBoxes(root).forEach(function (box) { box.checked = false; });
    selectionAnchor = null;
    refreshSelection(root);
  }

  function usernameList(rows) {
    var names = rows.map(function (row) { return row.dataset.memberUsername || ""; });
    if (names.length <= 6) return names.join(", ");
    return t("{usernames} and {n} more", { usernames: names.slice(0, 5).join(", "), n: names.length - 5 });
  }

  // One request per batch, the answers added up; a batch that fails stops the
  // rest, after the earlier ones went through.
  async function sendMoves(userIds, groupId) {
    var total = null;
    for (var start = 0; start < userIds.length; start += MOVE_BATCH_SIZE) {
      var result = await request("/api/users/move-to-group", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ user_ids: userIds.slice(start, start + MOVE_BATCH_SIZE), group_id: groupId }),
      });
      if (!total) {
        total = result;
        continue;
      }
      ["moved", "unchanged", "demoted", "llm_needs_attention", "failed", "not_found"].forEach(function (key) {
        total[key] = total[key].concat(result[key] || []);
      });
    }
    return total;
  }

  function moveSummary(result) {
    var parts = [tn(result.moved.length, "Moved {n} member to {group}.", "Moved {n} members to {group}.", { group: result.group_name })];
    if (result.unchanged.length) {
      parts.push(tn(result.unchanged.length, "{n} was already there.", "{n} were already there."));
    }
    if (result.demoted.length) {
      parts.push(tn(result.demoted.length, "{n} group administrator is now a member.", "{n} group administrators are now members."));
    }
    if (result.llm_needs_attention.length) {
      parts.push(tn(result.llm_needs_attention.length, "{usernames} needs to sign in to the model provider again.", "{usernames} need to sign in to the model provider again.", { usernames: result.llm_needs_attention.join(", ") }));
    }
    if (result.failed.length) {
      parts.push(t("Not moved: {usernames}.", { usernames: result.failed.join(", ") }));
    }
    if (result.not_found.length) {
      parts.push(tn(result.not_found.length, "{n} member no longer exists.", "{n} members no longer exist."));
    }
    return parts.join(" ");
  }

  // Every selected member moves the way one does from the row's select; the
  // ones already in the group stay as they are.
  async function moveSelectedMembers(button) {
    var root = panelRoot(button);
    var target = root && root.querySelector("[data-admin-member-bulk-group]");
    var groupId = groupIdOf(target && target.value);
    var rows = selectedMemberRows(root);
    if (!groupId || !rows.length) return;
    var label = target.options[target.selectedIndex] ? target.options[target.selectedIndex].text : "";
    var moving = rows.filter(function (row) { return String(row.dataset.memberGroup || "") !== String(groupId); });
    if (!moving.length) {
      var already = tn(rows.length, "The selected member is already in {group}.", "The selected members are already in {group}.", { group: label });
      if (window.showAlert) await window.showAlert({ title: t("Nothing to move"), message: already });
      else if (window.showToast) window.showToast(already, { variant: "info" });
      return;
    }
    var message = [
      t("Moving: {usernames}.", { usernames: usernameList(moving) }),
      t("They follow {group}'s Default connectors from now on; their idle assistants restart to pick that up.", { group: label }),
    ];
    var leads = moving.filter(function (row) { return row.dataset.memberRole === "group_admin"; });
    if (leads.length) {
      message.push(tn(
        leads.length,
        "{usernames} is a group administrator and becomes a member of {group}.",
        "{usernames} are group administrators and become members of {group}.",
        { usernames: usernameList(leads), group: label }
      ));
    }
    if (moving.length < rows.length) {
      message.push(tn(rows.length - moving.length, "{n} selected member is already in {group}.", "{n} selected members are already in {group}.", { group: label }));
    }
    var title = tn(moving.length, "Move {n} member to {group}?", "Move {n} members to {group}?", { group: label });
    var confirmed = window.showConfirm
      ? await window.showConfirm({ title: title, message: message.join(" "), confirmText: t("Move members") })
      : window.confirm(title);
    if (!confirmed) return;
    button.disabled = true;
    try {
      var result = await sendMoves(rows.map(function (row) { return Number(row.dataset.memberId); }), groupId);
      var summary = moveSummary(result);
      var partial = result.failed.length > 0;
      await reloadPanel(summary, partial);
      if (window.showToast) window.showToast(summary, { variant: partial ? "error" : "success" });
    } catch (error) {
      // An earlier batch may have gone through: show the groups as they are now.
      try {
        await reloadPanel(error.message, true);
      } catch (_reloadError) {
        button.disabled = false;
      }
      if (window.showAlert) {
        await window.showAlert({ title: t("Unable to move members"), message: error.message });
      }
    }
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
    selectionAnchor = null;
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
    closeMemberDetails(root);
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

  // ---- the member drawer: daily credits and models under the member's row ----

  function closeMemberDetails(root) {
    if (!root) return;
    root.querySelectorAll("tr[data-member-detail-for]").forEach(function (row) { row.remove(); });
    root.querySelectorAll("[data-admin-member-detail][aria-expanded='true']").forEach(function (button) {
      button.setAttribute("aria-expanded", "false");
    });
  }

  async function toggleMemberDetail(button) {
    var root = panelRoot(button);
    var row = button.closest("tr[data-member-id]");
    if (!root || !row) return;
    var memberId = button.dataset.adminMemberDetail;
    var existing = root.querySelector("tr[data-member-detail-for='" + memberId + "']");
    if (existing) {
      existing.remove();
      button.setAttribute("aria-expanded", "false");
      return;
    }
    closeMemberDetails(root);
    var detail = document.createElement("tr");
    detail.className = "portal-admin-member-detail";
    detail.setAttribute("data-member-detail-for", memberId);
    var cell = document.createElement("td");
    cell.colSpan = row.children.length;
    cell.innerHTML = '<div class="portal-inline-state" data-member-usage-target>' + t("Loading…") + "</div>";
    detail.appendChild(cell);
    row.insertAdjacentElement("afterend", detail);
    button.setAttribute("aria-expanded", "true");
    var target = cell.firstElementChild;
    var url = "/app/users/" + encodeURIComponent(memberId) + "/usage/panel?days=" + encodeURIComponent(selectedPeriodDays());
    try {
      if (window.htmx) {
        await window.htmx.ajax("GET", url, { target: target, swap: "outerHTML" });
      } else {
        var response = await fetch(url);
        if (!response.ok) throw new Error(await errorMessage(response));
        target.outerHTML = await response.text();
      }
    } catch (error) {
      target.textContent = error.message;
      target.classList.add("is-error");
    }
  }

  function csvField(value) {
    var text = String(value === undefined || value === null ? "" : value).replace(/\s+/g, " ").trim();
    return /[",\n;]/.test(text) ? "\"" + text.replace(/"/g, "\"\"") + "\"" : text;
  }

  function exportMembersCsv(root) {
    var table = root && root.querySelector("[data-admin-member-table]");
    if (!table) return;
    // The table's own header row: an open member drawer holds tables of its own.
    var headers = Array.prototype.slice.call(table.tHead ? table.tHead.rows[0].cells : [])
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
    var groupFilter = root.querySelector("[data-admin-member-group-filter]");
    var query = String(search && search.value || "").trim().toLowerCase();
    var access = String(accessFilter && accessFilter.value || "all");
    var groupId = String(groupFilter && groupFilter.value || "all");
    var cards = Array.prototype.slice.call(root.querySelectorAll("[data-member-id]"));
    var visible = 0;

    cards.forEach(function (card) {
      var identity = ((card.dataset.memberName || "") + " " + (card.dataset.memberUsername || "")).toLowerCase();
      var matchesQuery = !query || identity.indexOf(query) !== -1;
      // "inactive" = no run in the selected period and "admins" = a group or
      // system administrator, whatever the access.
      var matchesAccess = access === "all"
        || (access === "inactive" ? Number(card.dataset.memberExecutionsPeriod || 0) === 0
          : access === "admins" ? isAdminRole(card.dataset.memberRole)
          : card.dataset.memberAccess === access);
      var matchesGroup = groupId === "all" || String(card.dataset.memberGroup || "") === groupId;
      card.hidden = !(matchesQuery && matchesAccess && matchesGroup);
      if (!card.hidden) visible += 1;
    });

    var resultCount = root.querySelector("[data-admin-member-result-count]");
    if (resultCount) resultCount.textContent = t("{n} shown", { n: visible });
    var empty = root.querySelector("[data-admin-member-filter-empty]");
    if (empty) empty.hidden = visible !== 0;
    refreshSelection(root);
  }

  // The Show filter's options carry their counts (users_panel.html renders
  // them); a role change moves a member in or out of Administrators without
  // reloading the panel.
  function refreshFilterCounts(root) {
    var filter = root && root.querySelector("[data-admin-member-access-filter]");
    if (!filter) return;
    var rows = memberRows(root);
    var days = root.dataset.periodDays || "30";
    var count = function (test) { return rows.filter(test).length; };
    Array.prototype.forEach.call(filter.options, function (option) {
      if (option.value === "all") {
        option.textContent = t("All members ({n})", { n: rows.length });
      } else if (option.value === "blocked") {
        option.textContent = t("Needs access ({n})", { n: count(function (row) { return row.dataset.memberAccess === "blocked"; }) });
      } else if (option.value === "inactive") {
        option.textContent = t("Inactive in {days} days ({n})", { days: days, n: count(function (row) { return Number(row.dataset.memberExecutionsPeriod || 0) === 0; }) });
      } else if (option.value === "admins") {
        option.textContent = t("Administrators ({n})", { n: count(function (row) { return isAdminRole(row.dataset.memberRole); }) });
      }
    });
  }

  // ---- the row menu (Usage details, Revoke access) ----

  // A popover (users_panel.html): the browser draws it above the table box and
  // closes it on Escape or a click elsewhere. It opens under its button, or
  // above it when the button is near the bottom of the screen.
  var openRowMenu = null;

  function rowMenuTrigger(menu) {
    return menu && menu.id ? document.querySelector('[popovertarget="' + menu.id + '"]') : null;
  }

  function placeRowMenu(menu, flip) {
    var trigger = rowMenuTrigger(menu);
    if (!trigger) return;
    var rect = trigger.getBoundingClientRect();
    menu.style.left = "auto";
    menu.style.bottom = "auto";
    menu.style.right = Math.max(8, document.documentElement.clientWidth - rect.right) + "px";
    menu.style.top = rect.bottom + 4 + "px";
    if (!flip) return;
    var height = menu.getBoundingClientRect().height;
    if (rect.bottom + 4 + height > window.innerHeight - 8 && rect.top - 4 - height >= 8) {
      menu.style.top = rect.top - 4 - height + "px";
    }
  }

  function closeRowMenu(fromElement) {
    var menu = fromElement && fromElement.closest ? fromElement.closest("[data-admin-row-menu]") : openRowMenu;
    if (!menu || typeof menu.hidePopover !== "function") return;
    try {
      menu.hidePopover();
    } catch (error) {
      // Already closed.
    }
  }

  function menuItems(menu) {
    return Array.prototype.slice.call(menu.querySelectorAll('[role="menuitem"]:not([disabled])'));
  }

  // beforetoggle runs before the menu is drawn, so it never shows at the
  // browser's default spot; toggle runs once it has a height to flip by.
  document.addEventListener("beforetoggle", function (event) {
    var menu = event.target;
    if (!menu || !menu.matches || !menu.matches("[data-admin-row-menu]")) return;
    if (event.newState === "open") placeRowMenu(menu, false);
  }, true);

  document.addEventListener("toggle", function (event) {
    var menu = event.target;
    if (!menu || !menu.matches || !menu.matches("[data-admin-row-menu]")) return;
    var trigger = rowMenuTrigger(menu);
    var isOpen = event.newState === "open";
    if (trigger) trigger.setAttribute("aria-expanded", isOpen ? "true" : "false");
    if (isOpen) {
      openRowMenu = menu;
      placeRowMenu(menu, true);
      var first = menuItems(menu)[0];
      if (first) first.focus();
    } else if (openRowMenu === menu) {
      openRowMenu = null;
    }
  }, true);

  // A menu left open would float away from its row: scrolling closes it.
  document.addEventListener("scroll", function (event) {
    if (openRowMenu && !(event.target && event.target.nodeType === 1 && openRowMenu.contains(event.target))) closeRowMenu(null);
  }, true);
  window.addEventListener("resize", function () { closeRowMenu(null); });

  // ---- the pending registrations, a modal opened from the toolbar ----

  function openPendingModal() {
    var modal = document.querySelector("[data-admin-pending-modal]");
    if (!modal) return;
    modal.classList.remove("hidden");
    modal.setAttribute("aria-hidden", "false");
    var close = modal.querySelector("[data-close-admin-pending-modal]");
    if (close) close.focus();
  }

  function closePendingModal() {
    var modal = document.querySelector("[data-admin-pending-modal]");
    if (!modal || modal.classList.contains("hidden")) return false;
    modal.classList.add("hidden");
    modal.setAttribute("aria-hidden", "true");
    var trigger = document.querySelector("[data-open-admin-pending-modal]");
    if (trigger) trigger.focus();
    return true;
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
        body: JSON.stringify({ usernames: usernames, role: data.get("role"), group_id: groupIdOf(data.get("group_id")) }),
      });
      var messages = [];
      if (result.added.length) messages.push(t("Allowed {n}.", { n: result.added.length }));
      if (result.already_allowlisted.length) messages.push(t("Skipped {n} already allowed.", { n: result.already_allowlisted.length }));
      if (result.refused && result.refused.length) {
        messages.push(t("Not added, they belong to another group: {usernames}.", { usernames: result.refused.join(", ") }));
      }
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
    if (event.target.closest("[data-admin-member-access-filter]") || event.target.closest("[data-admin-member-group-filter]")) {
      applyMemberFilters(panelRoot(event.target));
      return;
    }
    if (event.target.closest("[data-admin-member-bulk-group]")) {
      refreshSelection(panelRoot(event.target));
      return;
    }
    var groupSelect = event.target.closest("[data-admin-group-select]");
    if (groupSelect) {
      await moveMember(groupSelect);
      return;
    }
    if (event.target.closest("[data-admin-member-period]")) {
      await reloadPanel("", false);
      return;
    }

    var roleSelect = event.target.closest("[data-admin-role-select]");
    if (roleSelect) await changeRole(roleSelect);
  });

  document.addEventListener("click", async function (event) {
    // A box is already toggled when its click arrives (mouse or Space).
    var memberBox = event.target.closest("[data-admin-member-select]");
    if (memberBox) {
      pickMember(memberBox, event.shiftKey);
      return;
    }
    var allBox = event.target.closest("[data-admin-member-select-all]");
    if (allBox) {
      pickShownMembers(panelRoot(allBox), allBox.checked);
      return;
    }
    var bulkMove = event.target.closest("[data-admin-member-bulk-move]");
    if (bulkMove) {
      event.preventDefault();
      await moveSelectedMembers(bulkMove);
      return;
    }
    var clearButton = event.target.closest("[data-admin-member-clear-selection]");
    if (clearButton) {
      event.preventDefault();
      clearSelection(panelRoot(clearButton));
      return;
    }

    var openButton = event.target.closest("[data-open-admin-allowlist-modal]");
    if (openButton) {
      event.preventDefault();
      openAllowlistModal();
      return;
    }

    var detailButton = event.target.closest("[data-admin-member-detail]");
    if (detailButton) {
      event.preventDefault();
      await toggleMemberDetail(detailButton);
      return;
    }

    // The row menu's Usage details: the same drawer as a click on the name,
    // left open if it already is.
    var usageItem = event.target.closest("[data-admin-member-usage]");
    if (usageItem) {
      event.preventDefault();
      closeRowMenu(usageItem);
      var usageRow = usageItem.closest("tr[data-member-id]");
      var nameButton = usageRow && usageRow.querySelector("[data-admin-member-detail]");
      if (!nameButton) return;
      if (nameButton.getAttribute("aria-expanded") !== "true") await toggleMemberDetail(nameButton);
      var drawer = usageRow.nextElementSibling;
      if (drawer && drawer.matches("tr[data-member-detail-for]")) drawer.scrollIntoView({ block: "nearest" });
      return;
    }

    if (event.target.closest("[data-open-admin-pending-modal]")) {
      event.preventDefault();
      openPendingModal();
      return;
    }
    var pendingModal = event.target.closest("[data-admin-pending-modal]");
    if (event.target.closest("[data-close-admin-pending-modal]") || (pendingModal && event.target === pendingModal)) {
      event.preventDefault();
      closePendingModal();
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
      // A system administrator chooses the group in the allowlist dialog;
      // a group administrator's own group needs no choice.
      if (allowRoot && allowRoot.querySelector("[data-admin-allowlist-group]")) {
        openAllowlistModal(username);
        return;
      }
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
    closeRowMenu(button);
    var root = panelRoot(button);
    // A removal from the pending list reopens it after the reload, for the next one.
    var fromPendingList = !!button.closest("[data-admin-pending-modal]");
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
      if (fromPendingList) openPendingModal();
    } catch (error) {
      feedback(root, error.message, true);
      if (window.showAlert) {
        await window.showAlert({ title: t("Unable to revoke access"), message: error.message });
      }
      button.disabled = false;
    }
  });

  document.addEventListener("keydown", function (event) {
    // Arrow keys, Home and End move between the open row menu's items
    // (Escape closing it is the browser's).
    if (openRowMenu && openRowMenu.contains(document.activeElement)) {
      var items = menuItems(openRowMenu);
      var index = items.indexOf(document.activeElement);
      var next = null;
      if (event.key === "ArrowDown") next = items[(index + 1) % items.length];
      else if (event.key === "ArrowUp") next = items[(index - 1 + items.length) % items.length];
      else if (event.key === "Home") next = items[0];
      else if (event.key === "End") next = items[items.length - 1];
      if (next) {
        event.preventDefault();
        next.focus();
      }
      return;
    }
    if (event.key !== "Escape") return;
    if (closePendingModal()) {
      event.preventDefault();
      return;
    }
    var modal = document.querySelector("[data-admin-allowlist-modal]");
    if (!modal || modal.classList.contains("hidden")) return;
    event.preventDefault();
    closeAllowlistModal();
  });
})();
