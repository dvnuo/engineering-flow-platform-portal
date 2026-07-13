/*
 * Styled confirm/prompt dialogs that replace the native window.confirm/prompt
 * and back htmx's hx-confirm. Promise-based, theme-aware, accessible.
 *
 * Public API (also available as window.*):
 *   showConfirm({ title, message, confirmText, cancelText, danger }) -> Promise<boolean>
 *   showPrompt({ title, message, defaultValue, placeholder, confirmText, cancelText, required }) -> Promise<string|null>
 *   showAlert({ title, message, confirmText }) -> Promise<void>
 */
(function () {
  "use strict";

  var FOCUSABLE = 'a[href],button:not([disabled]),textarea,input,select,[tabindex]:not([tabindex="-1"])';
  var seq = 0;

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
      : '<h3 id="' + id + '-title" class="sr-only">' + escapeHtml(opts.confirmText || "Confirm") + "</h3>";

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

    var cancelBtn =
      kind === "alert"
        ? ""
        : '<button type="button" class="portal-btn" data-dialog-cancel>' +
          escapeHtml(opts.cancelText || "Cancel") +
          "</button>";
    var confirmClass = "portal-btn is-primary" + (danger ? " portal-dialog-danger" : "");
    var confirmBtn =
      '<button type="button" class="' + confirmClass + '" data-dialog-confirm>' +
      escapeHtml(opts.confirmText || (kind === "alert" ? "OK" : "Confirm")) +
      "</button>";

    root.innerHTML =
      '<div class="modal-card panel portal-dialog-card" data-dialog-card>' +
      titleHtml +
      messageHtml +
      inputHtml +
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

    return new Promise(function (resolve) {
      var settled = false;

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
        if (kind === "prompt") {
          var val = input ? input.value : "";
          if (opts.required && !val.trim()) {
            if (input) { input.focus(); input.classList.add("portal-dialog-input--invalid"); }
            return;
          }
          cleanup(val);
        } else {
          cleanup(kind === "alert" ? undefined : true);
        }
      }
      function cancelResult() {
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
        });
        input.addEventListener("keydown", function (e) {
          if (e.key === "Enter") { e.preventDefault(); confirmResult(); }
        });
      }

      function onKeydown(e) {
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

  // Route htmx's hx-confirm through the styled dialog instead of window.confirm.
  document.addEventListener("htmx:confirm", function (evt) {
    var question = evt.detail && evt.detail.question;
    if (!question) return; // element has no hx-confirm; let htmx proceed normally
    evt.preventDefault();
    var danger = /delete|remove|restart|interrupt|discard|revoke/i.test(question);
    window.showConfirm({ message: question, danger: danger }).then(function (ok) {
      if (ok) evt.detail.issueRequest(true);
    });
  });
})();
