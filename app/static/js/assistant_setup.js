/**
 * First-run onboarding, simple assistant creation, and the admin panels that
 * feed them.
 *
 * Kept out of chat_ui.js on purpose: none of this touches the chat transcript,
 * and the create/onboarding flow is what a brand-new member sees before they
 * have any chat state at all.
 */
(function () {
  "use strict";

  const state = {
    assistantTypes: null,
    selectedTypeId: null,
    onboardingStep: 0,
    onboardingActive: false,
  };

  // ------------------------------------------------------------------ utils

  function esc(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function toast(message, variant) {
    if (typeof window.showToast === "function") {
      window.showToast(message, variant ? { variant: variant } : undefined);
    }
  }

  function renderIcons(root) {
    if (window.lucide && typeof window.lucide.createIcons === "function") {
      try {
        window.lucide.createIcons(root ? { nameAttr: "data-lucide" } : undefined);
      } catch (error) {
        /* icon rendering is cosmetic; never let it break a flow */
      }
    }
  }

  async function requestJson(url, options) {
    const response = await fetch(url, options);
    const text = await response.text();
    let payload = null;
    if (text) {
      try {
        payload = JSON.parse(text);
      } catch (error) {
        payload = null;
      }
    }
    if (!response.ok) {
      const detail = payload && (payload.detail || payload.message || payload.error);
      throw new Error(typeof detail === "string" ? detail : `Request failed (${response.status})`);
    }
    return payload;
  }

  function setFeedback(element, variant, message) {
    if (!element) return;
    element.textContent = message || "";
    element.classList.remove("is-error", "is-success");
    if (variant) element.classList.add(`is-${variant}`);
  }

  // -------------------------------------------------------- assistant types

  async function loadAssistantTypes(force) {
    if (state.assistantTypes && !force) return state.assistantTypes;
    const payload = await requestJson("/api/assistant-types");
    state.assistantTypes = Array.isArray(payload) ? payload : [];
    return state.assistantTypes;
  }

  function renderTypeChoices(container, types) {
    if (!container) return;
    if (!types.length) {
      container.innerHTML =
        '<div class="portal-inline-state is-warning">No assistant types are configured yet. ' +
        "Ask an administrator to add one, or use advanced setup.</div>";
      return;
    }
    container.innerHTML = types
      .map(
        (type, index) => `
      <label class="portal-assistant-type-option${index === 0 ? " is-selected" : ""}">
        <input type="radio" name="assistant_type_id" value="${esc(type.id)}"${index === 0 ? " checked" : ""} />
        <span class="portal-assistant-type-card">
          <span class="portal-assistant-type-icon"><i data-lucide="${esc(type.icon || "bot")}" class="w-5 h-5"></i></span>
          <span class="portal-assistant-type-copy">
            <strong>${esc(type.name)}</strong>
            ${type.description ? `<small>${esc(type.description)}</small>` : ""}
          </span>
        </span>
      </label>`
      )
      .join("");

    // Selection is styling only; the radio inputs remain the source of truth so
    // keyboard navigation and form semantics keep working.
    container.addEventListener("change", () => {
      container.querySelectorAll(".portal-assistant-type-option").forEach((option) => {
        const input = option.querySelector("input");
        option.classList.toggle("is-selected", Boolean(input && input.checked));
      });
    });
    renderIcons(container);
  }

  function selectedTypeId(form) {
    const checked = form ? form.querySelector('input[name="assistant_type_id"]:checked') : null;
    return checked ? checked.value : "";
  }

  async function createAssistant(name, assistantTypeId) {
    return requestJson("/api/agents/simple", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: name, assistant_type_id: assistantTypeId }),
    });
  }

  async function afterCreate(agent) {
    if (typeof window.refreshPortalAll === "function") {
      try {
        await window.refreshPortalAll();
      } catch (error) {
        /* the assistant exists either way; a failed refresh is not fatal */
      }
    }
    if (agent && agent.id && typeof window.selectPortalAgentById === "function") {
      try {
        await window.selectPortalAgentById(agent.id);
      } catch (error) {
        /* selection is a convenience, not a requirement */
      }
    }
  }

  // ----------------------------------------------------- simple create modal

  const simpleModal = () => document.getElementById("create-simple-modal");

  function closeSimpleModal() {
    const modal = simpleModal();
    if (!modal) return;
    modal.classList.add("hidden");
    modal.setAttribute("aria-hidden", "true");
  }

  /**
   * Open simple mode. Returns false when it cannot be used, which tells
   * chat_ui.js to fall back to the advanced wizard rather than dead-end.
   */
  window.openSimpleCreateModal = async function openSimpleCreateModal() {
    const modal = simpleModal();
    if (!modal) return false;

    let types = [];
    try {
      types = await loadAssistantTypes(true);
    } catch (error) {
      return false;
    }
    if (!types.length) return false;

    const form = document.getElementById("create-simple-form");
    if (form) {
      form.reset();
      renderTypeChoices(form.querySelector("[data-simple-type-choices]"), types);
    }
    setFeedback(document.getElementById("create-simple-msg"), "", "");
    modal.classList.remove("hidden");
    modal.setAttribute("aria-hidden", "false");
    const nameInput = form ? form.querySelector('input[name="name"]') : null;
    if (nameInput) window.setTimeout(() => nameInput.focus(), 30);
    return true;
  };

  function bindSimpleCreate() {
    const form = document.getElementById("create-simple-form");
    if (!form) return;

    document.getElementById("close-create-simple-modal")?.addEventListener("click", closeSimpleModal);

    form.querySelector("[data-open-advanced-create]")?.addEventListener("click", async () => {
      const name = (form.querySelector('input[name="name"]') || {}).value || "";
      closeSimpleModal();
      if (typeof window.openAdvancedCreateModal === "function") {
        await window.openAdvancedCreateModal();
        // Carry the name across so switching modes never costs typing.
        const advancedName = document.querySelector('#create-form input[name="name"]');
        if (advancedName && name.trim()) advancedName.value = name.trim();
      }
    });

    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const msg = document.getElementById("create-simple-msg");
      const name = (form.querySelector('input[name="name"]').value || "").trim();
      const typeId = selectedTypeId(form);
      if (!name) return setFeedback(msg, "error", "Give your assistant a name.");
      if (!typeId) return setFeedback(msg, "error", "Choose an assistant type.");

      const submitButton = form.querySelector('button[type="submit"]');
      if (submitButton) submitButton.disabled = true;
      setFeedback(msg, "", "Creating…");
      try {
        const agent = await createAssistant(name, typeId);
        setFeedback(msg, "success", "Assistant created.");
        closeSimpleModal();
        toast("Assistant created. Starting it now…");
        await afterCreate(agent);
      } catch (error) {
        setFeedback(msg, "error", error.message);
      } finally {
        if (submitButton) submitButton.disabled = false;
      }
    });
  }

  // --------------------------------------------------------------- onboarding

  const ONBOARDING_LAST_STEP = 2;

  function onboardingOverlay() {
    return document.getElementById("onboarding-overlay");
  }

  function showOnboardingStep(index) {
    const overlay = onboardingOverlay();
    if (!overlay) return;
    state.onboardingStep = index;
    overlay.querySelectorAll("[data-onboarding-step]").forEach((section) => {
      section.classList.toggle("hidden", Number(section.dataset.onboardingStep) !== index);
    });
    overlay.querySelectorAll("[data-onboarding-dot]").forEach((dot) => {
      dot.classList.toggle("is-active", Number(dot.dataset.onboardingDot) === index);
    });
    overlay.querySelector("[data-onboarding-back]")?.classList.toggle("hidden", index === 0);
    overlay.querySelector("[data-onboarding-next]")?.classList.toggle("hidden", index === ONBOARDING_LAST_STEP);
    overlay.querySelector("[data-onboarding-create]")?.classList.toggle("hidden", index !== ONBOARDING_LAST_STEP);
    renderIcons(overlay);
  }

  async function finishOnboarding({ silent } = {}) {
    const overlay = onboardingOverlay();
    if (overlay) {
      overlay.classList.add("hidden");
      overlay.setAttribute("aria-hidden", "true");
    }
    state.onboardingActive = false;
    try {
      await requestJson("/api/auth/me/onboarding-complete", { method: "POST" });
    } catch (error) {
      // Recording completion is bookkeeping. Failing it must not trap someone
      // in the tour, so the overlay closes regardless.
      if (!silent) console.warn("Could not record onboarding completion", error);
    }
  }

  async function maybeStartOnboarding() {
    const overlay = onboardingOverlay();
    if (!overlay) return;
    let me = null;
    try {
      me = await requestJson("/api/auth/me");
    } catch (error) {
      return;
    }
    if (!me || me.onboarding_completed) return;

    let types = [];
    try {
      types = await loadAssistantTypes();
    } catch (error) {
      types = [];
    }

    state.onboardingActive = true;
    renderTypeChoices(overlay.querySelector("[data-onboarding-type-choices]"), types);
    overlay.classList.remove("hidden");
    overlay.setAttribute("aria-hidden", "false");
    showOnboardingStep(0);
  }

  function bindOnboarding() {
    const overlay = onboardingOverlay();
    if (!overlay) return;

    overlay.querySelector("[data-onboarding-next]")?.addEventListener("click", () => {
      showOnboardingStep(Math.min(state.onboardingStep + 1, ONBOARDING_LAST_STEP));
    });
    overlay.querySelector("[data-onboarding-back]")?.addEventListener("click", () => {
      showOnboardingStep(Math.max(state.onboardingStep - 1, 0));
    });
    overlay.querySelector("[data-onboarding-skip]")?.addEventListener("click", () => {
      finishOnboarding();
    });

    document.getElementById("onboarding-create-form")?.addEventListener("submit", async (event) => {
      event.preventDefault();
      const form = event.target;
      const msg = overlay.querySelector("[data-onboarding-msg]");
      const name = (form.querySelector('input[name="name"]').value || "").trim();
      const typeId = selectedTypeId(form);
      if (!name) return setFeedback(msg, "error", "Give your assistant a name.");
      if (!typeId) {
        setFeedback(msg, "error", "No assistant types are available. Ask an administrator to add one.");
        return;
      }

      const createButton = overlay.querySelector("[data-onboarding-create]");
      if (createButton) createButton.disabled = true;
      setFeedback(msg, "", "Creating…");
      try {
        const agent = await createAssistant(name, typeId);
        await finishOnboarding({ silent: true });
        toast("Assistant created. Starting it now…");
        await afterCreate(agent);
      } catch (error) {
        setFeedback(msg, "error", error.message);
      } finally {
        if (createButton) createButton.disabled = false;
      }
    });

    // Esc leaves the tour rather than trapping someone behind a modal.
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && state.onboardingActive) finishOnboarding();
    });
  }

  // ------------------------------------------------------------ admin panels

  async function openAdminPanel(button) {
    const target = document.getElementById("workspace-detail-content");
    if (!target) return;
    const url = button.dataset.adminPanelUrl;

    document
      .querySelectorAll("[data-admin-panel]")
      .forEach((item) => item.classList.toggle("is-active", item === button));
    document.getElementById("user-management-nav-item")?.classList.remove("is-active");

    target.innerHTML = '<div class="portal-inline-state">Loading…</div>';
    try {
      const response = await fetch(url, { headers: { "HX-Request": "true" } });
      if (!response.ok) throw new Error(`Failed to load (${response.status})`);
      target.innerHTML = await response.text();
      target.dataset.workspaceState = `admin-${button.dataset.adminPanel}`;
      renderIcons(target);
    } catch (error) {
      target.innerHTML = `<div class="portal-inline-state is-error">${esc(error.message)}</div>`;
    }
  }

  function bindAdminNav() {
    document.querySelectorAll("[data-admin-panel]").forEach((button) => {
      button.addEventListener("click", () => openAdminPanel(button));
    });
  }

  // Panels arrive as innerHTML, so their controls are bound by delegation from
  // a container that is always present.
  function bindAdminPanelDelegation() {
    const root = document.getElementById("workspace-detail-content");
    if (!root) return;

    root.addEventListener("submit", async (event) => {
      const createForm = event.target.closest("[data-assistant-type-create-form]");
      if (createForm) {
        event.preventDefault();
        await submitAssistantTypeCreate(createForm);
        return;
      }
      const seedForm = event.target.closest("[data-seed-form]");
      if (seedForm) {
        event.preventDefault();
        await submitSeed(seedForm);
      }
    });

    root.addEventListener("click", async (event) => {
      const deleteButton = event.target.closest("[data-assistant-type-delete]");
      if (deleteButton) {
        const row = deleteButton.closest("[data-assistant-type-row]");
        await deleteAssistantType(row);
        return;
      }
      const branchButton = event.target.closest("[data-load-type-branches]");
      if (branchButton) {
        await loadTypeBranches(branchButton);
      }
    });

    root.addEventListener("change", async (event) => {
      const activeToggle = event.target.closest("[data-assistant-type-active]");
      if (activeToggle) {
        const row = activeToggle.closest("[data-assistant-type-row]");
        await setAssistantTypeActive(row, activeToggle.checked);
      }
    });
  }

  async function submitAssistantTypeCreate(form) {
    const msg = form.querySelector("[data-assistant-type-create-msg]");
    const data = new FormData(form);
    const body = {
      name: (data.get("name") || "").toString().trim(),
      description: (data.get("description") || "").toString().trim() || null,
      icon: (data.get("icon") || "").toString().trim() || "bot",
      runtime_type: (data.get("runtime_type") || "native").toString(),
      agent_settings_branch: (data.get("agent_settings_branch") || "").toString().trim() || null,
      skill_branch: (data.get("skill_branch") || "").toString().trim() || null,
      sort_order: Number(data.get("sort_order") || 0),
      is_active: true,
    };
    if (!body.name) return setFeedback(msg, "error", "Give the type a name.");

    setFeedback(msg, "", "Saving…");
    try {
      await requestJson("/api/assistant-types", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      state.assistantTypes = null;
      toast("Assistant type added.");
      await reloadAdminPanel("assistant-types");
    } catch (error) {
      setFeedback(msg, "error", error.message);
    }
  }

  async function setAssistantTypeActive(row, isActive) {
    if (!row) return;
    try {
      await requestJson(`/api/assistant-types/${encodeURIComponent(row.dataset.assistantTypeRow)}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ is_active: isActive }),
      });
      state.assistantTypes = null;
      row.classList.toggle("is-instance-disabled", !isActive);
      const label = row.querySelector(".portal-instance-state");
      if (label) label.textContent = isActive ? "Offered" : "Hidden";
    } catch (error) {
      toast(error.message, "error");
    }
  }

  async function deleteAssistantType(row) {
    if (!row) return;
    const name = row.querySelector(".portal-settings-instance-title")?.textContent || "this type";
    const confirmed = window.confirm(
      `Delete ${name}? Assistants already created from it keep working — creation copies these values onto the assistant.`
    );
    if (!confirmed) return;
    try {
      await requestJson(`/api/assistant-types/${encodeURIComponent(row.dataset.assistantTypeRow)}`, {
        method: "DELETE",
      });
      state.assistantTypes = null;
      toast("Assistant type deleted.");
      await reloadAdminPanel("assistant-types");
    } catch (error) {
      toast(error.message, "error");
    }
  }

  async function loadTypeBranches(button) {
    const kind = button.dataset.loadTypeBranches;
    const panel = button.closest("[data-assistant-types-panel]");
    const select = button.parentElement?.querySelector(`[data-branch-select="${kind}"]`);
    if (!select || !panel) return;

    // Branch lists come from the repository Portal is already configured with,
    // so an admin picks a real branch instead of typing one from memory.
    const repoUrl =
      kind === "skills"
        ? panel.querySelector("[data-skill-repo-url]")?.value
        : panel.querySelector("[data-agent-settings-repo-url]")?.value;

    button.disabled = true;
    const originalLabel = button.textContent;
    button.textContent = "Loading…";
    try {
      const query = repoUrl ? `?repo_url=${encodeURIComponent(repoUrl)}` : "";
      const payload = await requestJson(`/api/git-repos/branches${query}`);
      const branches = Array.isArray(payload?.branches) ? payload.branches : [];
      const current = select.value;
      select.innerHTML =
        '<option value="">Configured default branch</option>' +
        branches.map((branch) => `<option value="${esc(branch)}">${esc(branch)}</option>`).join("");
      if (current) select.value = current;
      button.textContent = branches.length ? `${branches.length} branches` : "No branches";
    } catch (error) {
      button.textContent = "Load failed";
      toast(error.message, "error");
    } finally {
      button.disabled = false;
      window.setTimeout(() => {
        button.textContent = originalLabel;
      }, 2500);
    }
  }

  async function submitSeed(form) {
    const msg = form.querySelector("[data-seed-msg]");
    const raw = form.querySelector('textarea[name="seed"]').value || "{}";
    let parsed;
    try {
      parsed = JSON.parse(raw);
    } catch (error) {
      return setFeedback(msg, "error", "That is not valid JSON.");
    }
    setFeedback(msg, "", "Saving…");
    try {
      await requestJson("/api/admin/runtime-profile-seed", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ seed: parsed }),
      });
      setFeedback(msg, "success", "Saved. New members will inherit this shape.");
      toast("Default connections saved.");
      await reloadAdminPanel("default-connections");
    } catch (error) {
      setFeedback(msg, "error", error.message);
    }
  }

  async function reloadAdminPanel(panelName) {
    const button = document.querySelector(`[data-admin-panel="${panelName}"]`);
    if (button) await openAdminPanel(button);
  }

  // -------------------------------------------------------------- bootstrap

  function init() {
    bindSimpleCreate();
    bindOnboarding();
    bindAdminNav();
    bindAdminPanelDelegation();
    // Onboarding decides for itself whether to show; it is a no-op for anyone
    // who has already been through it.
    maybeStartOnboarding();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
