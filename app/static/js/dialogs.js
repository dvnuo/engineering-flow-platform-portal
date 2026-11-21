/*
 * Styled confirm/prompt dialogs that replace the native window.confirm/prompt
 * and back htmx's hx-confirm. Promise-based, theme-aware, accessible.
 *
 * Public API (also available as window.*):
 *   showConfirm({ title, message, confirmText, cancelText, danger }) -> Promise<boolean>
 *   showPrompt({ title, message, defaultValue, placeholder, confirmText, cancelText, required }) -> Promise<string|null>
 *   showAlert({ title, message, confirmText }) -> Promise<void>
 *
 * A confirmed action that takes time is given as `action`: the dialog then
 * stays open while it runs, with the confirm button busy (`busyText`), a line
 * that says what is happening (`busyMessage`), and nothing that closes it.
 * It closes when the action is done; when the action fails its message is
 * shown in the dialog and the member can try again or cancel. `validate`
 * checks what was typed and returns what is wrong with it, shown in the same
 * line. Without `action` the dialog closes on confirm, as before.
 *
 *   showConfirm({ ..., busyText, busyMessage, action: async () => {} })
 *   showPrompt({ ..., validate: (value) => "" | "what is wrong", action: async (value) => {} })
 *
 * portalBusyButton(button, label) marks a button as working (disabled, a
 * spinner, the label) and returns the function that puts it back.
 */
(function () {
  "use strict";

  var FOCUSABLE = 'a[href],button:not([disabled]),textarea,input,select,[tabindex]:not([tabindex="-1"])';
  var seq = 0;

  // A button that is working: it turns, cannot be pressed twice, and keeps
  // exactly the size it had, so nothing beside it moves. The label says what
  // is happening when that fits in the button; when it does not, the spinner
  // lies over the button's own label and the words go to assistive technology.
  function busyButton(button, label) {
    if (!button || button.classList.contains("is-busy")) return function () {};
    var kept = { html: button.innerHTML, disabled: button.disabled, width: button.style.width, name: button.getAttribute("aria-label") };
    var width = typeof button.getBoundingClientRect === "function" ? button.getBoundingClientRect().width : 0;
    if (width) button.style.width = width + "px";
    button.disabled = true;
    button.setAttribute("aria-busy", "true");
    button.classList.add("is-busy");
    if (label) {
      button.textContent = label;
      if (width && button.scrollWidth > button.clientWidth + 1) {
        button.innerHTML = kept.html;
        button.classList.add("is-busy-over");
        button.setAttribute("aria-label", label);
      }
    } else {
      button.classList.add("is-busy-over");
    }
    return function () {
      if (!button.classList.contains("is-busy")) return;
      button.innerHTML = kept.html;
      button.disabled = kept.disabled;
      button.style.width = kept.width;
      if (kept.name === null) button.removeAttribute("aria-label");
      else button.setAttribute("aria-label", kept.name);
      button.removeAttribute("aria-busy");
      button.classList.remove("is-busy", "is-busy-over");
    };
  }

  // Room for the busy label from the start: the button is as wide as the
  // wider of its two labels, so it does not grow when the action begins.
  function reserveBusyWidth(button, label) {
    if (!button || !label || typeof button.getBoundingClientRect !== "function") return;
    var html = button.innerHTML;
    var rest = button.getBoundingClientRect().width;
    button.classList.add("is-busy");
    button.textContent = label;
    var working = button.getBoundingClientRect().width;
    button.classList.remove("is-busy");
    button.innerHTML = html;
    if (working > rest) button.style.minWidth = Math.ceil(working) + "px";
  }

  function openDialog(opts) {
    seq += 1;
    var id = "portal-dialog-" + seq;
    var kind = opts.kind || "confirm"; // confirm | prompt | alert
    var danger = !!opts.danger;
    var previouslyFocused = document.activeElement;

    var root = document.createElement("div");
    root.className = "modal portal-dialog";
    root.setAttribute("role", "dialog");
    root.setAttribute("aria-modal", "true");
    root.setAttribute("aria-labelledby", id + "-title");
    root.setAttribute("aria-describedby", id + "-msg");

    var titleHtml = opts.title
      ? '<div class="portal-modal-titlebar"><h3 id="' + id + '-title">' + escapeHtml(opts.title) + "</h3></div>"
      : '<h3 id="' + id + '-title" class="sr-only">' + escapeHtml(opts.confirmText || t("Confirm")) + "</h3>";

    var messageHtml = opts.message
      ? '<p class="portal-modal-copy portal-dialog-msg" id="' + id + '-msg">' + escapeHtml(opts.message) + "</p>"
      : '<span id="' + id + '-msg" class="sr-only">' + escapeHtml(opts.title || "") + "</span>";

    var inputHtml = "";
    if (kind === "prompt") {
      inputHtml =
        '<input type="text" class="portal-form-input portal-dialog-input" ' +
        'value="' + escapeHtml(opts.defaultValue || "") + '" ' +
        'placeholder="' + escapeHtml(opts.placeholder || "") + '" />';
    }

    // The line for what is happening and for what went wrong. It is in the
    // dialog from the start, empty, so the dialog keeps its size.
    var hasAction = typeof opts.action === "function";
    var statusHtml = hasAction || typeof opts.validate === "function"
      ? '<p class="portal-dialog-status" data-dialog-status aria-live="polite"></p>'
      : "";

    var cancelBtn =
      kind === "alert"
        ? ""
        : '<button type="button" class="portal-btn" data-dialog-cancel>' +
          escapeHtml(opts.cancelText || t("Cancel")) +
          "</button>";
    var confirmClass = "portal-btn is-primary" + (danger ? " portal-dialog-danger" : "");
    var confirmBtn =
      '<button type="button" class="' + confirmClass + '" data-dialog-confirm>' +
      escapeHtml(opts.confirmText || (kind === "alert" ? t("OK") : t("Confirm"))) +
      "</button>";

    // A dialog that waits can take focus itself: while its action runs every
    // control in it is held, and the keyboard must stay in it all the same.
    root.innerHTML =
      '<div class="modal-card panel portal-dialog-card" data-dialog-card' + (hasAction ? ' tabindex="-1"' : "") + ">" +
      titleHtml +
      messageHtml +
      inputHtml +
      statusHtml +
      '<div class="portal-modal-actions">' + cancelBtn + confirmBtn + "</div>" +
      "</div>";

    document.body.appendChild(root);
    // Force reflow so the enter transition runs.
    // eslint-disable-next-line no-unused-expressions
    root.offsetWidth;
    root.classList.add("portal-dialog--in");

    var input = root.querySelector(".portal-dialog-input");
    var confirmEl = root.querySelector("[data-dialog-confirm]");
    var cancelEl = root.querySelector("[data-dialog-cancel]");
    var statusEl = root.querySelector("[data-dialog-status]");
    var cardEl = root.querySelector("[data-dialog-card]");
    if (hasAction) reserveBusyWidth(confirmEl, opts.busyText || t("Working\u2026"));

    return new Promise(function (resolve) {
      var settled = false;
      var busy = false;
      var releaseConfirm = null;

      function setStatus(text, isError) {
        if (!statusEl) return;
        statusEl.textContent = text || "";
        statusEl.title = text || "";
        statusEl.classList.toggle("is-error", !!isError);
        if (isError) statusEl.setAttribute("role", "alert");
        else statusEl.removeAttribute("role");
      }

      function setBusy(on) {
        busy = on;
        root.classList.toggle("is-busy", on);
        if (on) releaseConfirm = busyButton(confirmEl, opts.busyText || t("Working\u2026"));
        else if (releaseConfirm) { releaseConfirm(); releaseConfirm = null; }
        if (cancelEl) cancelEl.disabled = on;
        if (input) input.disabled = on;
        // Focus was on a control that is now held: the dialog takes it, so
        // the keys still reach this dialog and nothing behind it.
        if (on && cardEl && typeof cardEl.focus === "function") cardEl.focus();
      }

      function cleanup(result) {
        if (settled) return;
        settled = true;
        document.removeEventListener("keydown", onKeydown, true);
        root.classList.remove("portal-dialog--in");
        var done = function () {
          if (root.parentNode) root.parentNode.removeChild(root);
          if (previouslyFocused && typeof previouslyFocused.focus === "function") {
            try { previouslyFocused.focus(); } catch (e) { /* noop */ }
          }
        };
        // Wait out the exit transition, but never hang if it doesn't fire.
        var fallback = setTimeout(done, 220);
        root.addEventListener("transitionend", function te() {
          clearTimeout(fallback);
          root.removeEventListener("transitionend", te);
          done();
        });
        resolve(result);
      }

      function confirmResult() {
        if (busy) return;
        var val = kind === "prompt" && input ? input.value : "";
        var result = kind === "prompt" ? val : kind === "alert" ? undefined : true;
        if (kind === "prompt" && opts.required && !val.trim()) {
          if (input) { input.focus(); input.classList.add("portal-dialog-input--invalid"); }
          return;
        }
        if (typeof opts.validate === "function") {
          var problem = opts.validate(val);
          if (problem) {
            setStatus(problem, true);
            if (input) { input.focus(); input.classList.add("portal-dialog-input--invalid"); }
            return;
          }
        }
        if (!hasAction) { cleanup(result); return; }
        // The dialog waits with the member: it closes when the work is done.
        setStatus(opts.busyMessage || "", false);
        setBusy(true);
        Promise.resolve()
          .then(function () { return opts.action(kind === "prompt" ? val : true); })
          .then(function () {
            busy = false;
            cleanup(result);
          }, function (error) {
            setBusy(false);
            setStatus((error && error.message) || t("Request failed"), true);
            var back = input || confirmEl;
            if (back && typeof back.focus === "function") back.focus();
          });
      }
      function cancelResult() {
        if (busy) return;
        cleanup(kind === "prompt" ? null : kind === "alert" ? undefined : false);
      }

      confirmEl.addEventListener("click", confirmResult);
      if (cancelEl) cancelEl.addEventListener("click", cancelResult);
      // Click on the backdrop (outside the card) cancels.
      root.addEventListener("mousedown", function (e) {
        if (e.target === root) cancelResult();
      });
      if (input) {
        input.addEventListener("input", function () {
          input.classList.remove("portal-dialog-input--invalid");
          if (statusEl && statusEl.classList.contains("is-error")) setStatus("", false);
        });
        input.addEventListener("keydown", function (e) {
          if (e.key === "Enter") { e.preventDefault(); confirmResult(); }
        });
      }

      function onKeydown(e) {
        // While the action runs nothing closes the dialog and nothing starts it twice.
        if (busy && (e.key === "Escape" || e.key === "Enter")) { e.preventDefault(); return; }
        // Nor does Tab walk out of it: every control in it is held, so there
        // is nowhere in the dialog to go, and what lies behind it is not to
        // be reached while it waits.
        if (busy && e.key === "Tab") { e.preventDefault(); return; }
        if (e.key === "Escape") { e.preventDefault(); cancelResult(); return; }
        if (e.key === "Enter" && kind !== "prompt" && document.activeElement !== cancelEl) {
          e.preventDefault();
          confirmResult();
          return;
        }
        if (e.key === "Tab") trapFocus(root, e);
      }
      document.addEventListener("keydown", onKeydown, true);

      // Initial focus: the input for prompts, otherwise the primary action.
      window.requestAnimationFrame(function () {
        if (input) { input.focus(); input.select(); }
        else if (confirmEl) confirmEl.focus();
      });
    });
  }

  function trapFocus(root, e) {
    var items = Array.prototype.filter.call(
      root.querySelectorAll(FOCUSABLE),
      function (el) { return el.offsetParent !== null; }
    );
    if (!items.length) return;
    var first = items[0];
    var last = items[items.length - 1];
    if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
  }

  function escapeHtml(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }

  window.showConfirm = function (opts) { return openDialog(Object.assign({ kind: "confirm" }, opts || {})); };
  window.showPrompt = function (opts) { return openDialog(Object.assign({ kind: "prompt" }, opts || {})); };
  window.showAlert = function (opts) { return openDialog(Object.assign({ kind: "alert" }, opts || {})); };
  window.portalBusyButton = busyButton;

  // Route htmx's hx-confirm through the styled dialog instead of window.confirm.
  // The element may name the dialog's title, its confirm button and whether
  // the action is destructive (data-confirm-title / data-confirm-ok /
  // data-confirm-danger="1"); otherwise the question alone decides.
  document.addEventListener("htmx:confirm", function (evt) {
    var question = evt.detail && evt.detail.question;
    if (!question) return; // element has no hx-confirm; let htmx proceed normally
    evt.preventDefault();
    var data = (evt.detail.elt && evt.detail.elt.dataset) || {};
    var danger = data.confirmDanger === "1" || /delete|remove|restart|interrupt|discard|revoke/i.test(question);
    window.showConfirm({
      title: data.confirmTitle || "",
      message: question,
      confirmText: data.confirmOk || "",
      danger: danger,
    }).then(function (ok) {
      if (ok) evt.detail.issueRequest(true);
    });
  });

  // A request htmx made that came back with an error status swaps nothing
  // and would otherwise fail without a word: say so.
  document.addEventListener("htmx:responseError", function (evt) {
    if (typeof window.showToast !== "function") return;
    var xhr = evt.detail && evt.detail.xhr;
    var message = xhr && xhr.status
      ? t("The request failed (HTTP {status}). Reload the page and try again.", { status: xhr.status })
      : t("The request failed. Reload the page and try again.");
    window.showToast(message, { variant: "error" });
  });
})();
