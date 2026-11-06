// Administration > Errors: the detail card that htmx swaps into #admin-error-detail
// needs two things the markup cannot do alone: show itself when it arrives, and
// copy its plain-text version (the hidden textarea) for a ticket. Filters and
// row clicks are htmx attributes in partials/admin_errors_panel.html.
(function () {
  const DETAIL_ID = "admin-error-detail";

  function detailHost() {
    return document.getElementById(DETAIL_ID);
  }

  document.addEventListener("htmx:afterSwap", function (event) {
    const target = event.detail && event.detail.target;
    if (!target || target.id !== DETAIL_ID) return;
    target.classList.remove("hidden");
    if (typeof target.scrollIntoView === "function") {
      target.scrollIntoView({ block: "nearest", behavior: "smooth" });
    }
  });

  function fallbackCopy(source, done) {
    try {
      source.classList.remove("hidden");
      source.focus();
      source.select();
      document.execCommand("copy");
      source.classList.add("hidden");
      done();
    } catch (_error) {
      source.classList.add("hidden");
    }
  }

  document.addEventListener("click", function (event) {
    const closeButton = event.target.closest("[data-close-error-detail]");
    if (closeButton) {
      const host = detailHost();
      if (host) {
        host.innerHTML = "";
        host.classList.add("hidden");
      }
      return;
    }
    const copyButton = event.target.closest("[data-copy-error-detail]");
    if (!copyButton) return;
    const host = detailHost();
    const source = host ? host.querySelector("[data-error-copy-text]") : null;
    const text = source ? String(source.value || source.textContent || "") : "";
    if (!source || !text) return;
    const originalLabel = copyButton.textContent;
    const done = function () {
      copyButton.textContent = copyButton.getAttribute("data-copied-label") || "Copied";
      window.setTimeout(function () {
        copyButton.textContent = originalLabel;
      }, 1500);
    };
    if (navigator.clipboard && typeof navigator.clipboard.writeText === "function") {
      navigator.clipboard.writeText(text).then(done, function () {
        fallbackCopy(source, done);
      });
    } else {
      fallbackCopy(source, done);
    }
  });
})();
