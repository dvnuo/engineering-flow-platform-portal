/*
 * The read-only share page (templates/share.html, css/share.css).
 *
 * Fetches /share/{token}/data.json and draws a reading page: the member's
 * turns as bubbles on the right, the assistant's answers as plain text under
 * its name on the left, rendered the way the chat renders them (the same
 * markdown-it options, highlight.js for code, Mermaid on demand from
 * <meta name="portal-mermaid-src">, EFP cards from efp_cards.js in their
 * read-only form, the chat's code block, table and callout classes). It
 * deliberately does not load chat_ui.js, which binds the app shell at load;
 * the small renderer here is the price of a page that works on its own.
 *
 * Its labels are English keys through t() (app/static/js/i18n.js, which
 * base.html loads ahead of this script with the viewer's catalogue); the
 * transcript itself stays in whatever language the session was in.
 *
 * The page is live: Refresh reloads the transcript, and while the session
 * reports a run in progress it refreshes itself every 30 s while visible. A
 * refresh that brings the same transcript back leaves the DOM alone, so the
 * reader's scroll position and open diagrams survive it.
 */
(function () {
  "use strict";

  var page = document.getElementById("share-page");
  if (!page) return;
  if (page.dataset.shareMissing === "true") {
    // The "no longer available" page has icons and nothing else to draw.
    window.addEventListener("load", renderIcons);
    return;
  }

  var dataUrl = page.dataset.shareDataUrl || "";
  var dom = {
    title: document.getElementById("share-title"),
    loadedAt: document.getElementById("share-loaded-at"),
    running: document.getElementById("share-running"),
    refresh: document.getElementById("share-refresh"),
    copyMarkdown: document.getElementById("share-copy-markdown"),
    showTools: document.getElementById("share-show-tools"),
    openPortal: document.getElementById("share-open-portal"),
    toc: document.getElementById("share-toc"),
    transcript: document.getElementById("share-transcript"),
  };
  var state = { data: null, loading: false, timer: null, mermaidSeq: 0, mermaidTheme: "", fingerprint: "", scrolledToHash: false };
  var AUTO_REFRESH_MS = 30000;
  var TOOL_ROW_PREFIXES = ["[Tool", "Tool "];
  // chat_ui.js getLastSessionKey: the session the Portal opens for an assistant.
  var LAST_SESSION_KEY_PREFIX = "portal-last-session-";
  // Typography the page needs, as code points: the source stays ASCII.
  var ELLIPSIS = String.fromCharCode(0x2026);
  var MIDDLE_DOT = String.fromCharCode(0xB7);
  var EM_DASH = String.fromCharCode(0x2014);
  var FILE_ICON = String.fromCodePoint(0x1F4C4);

  // ----- text helpers --------------------------------------------------------

  function escapeHtml(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }

  function normalizeFenceLanguage(lang) {
    var first = String(lang || "").trim().split(/\s+/)[0] || "";
    return first.toLowerCase().replace(/[^a-z0-9+#._-]/g, "");
  }

  function isMermaidLanguage(lang) {
    var language = normalizeFenceLanguage(lang);
    return language === "mermaid" || language === "mmd";
  }

  function isEfpCardLanguage(lang) {
    var language = normalizeFenceLanguage(lang);
    return language === "efp-review" || language === "efp-evidence" || language === "efp-matrix" || language === "efp-replay";
  }

  function languageOf(code) {
    var match = Array.prototype.find.call(code.classList, function (name) { return name.indexOf("language-") === 0; });
    return match ? match.slice("language-".length) : "";
  }

  function normalizeMarkdownText(text) {
    return String(text || "").replace(/\r\n/g, "\n");
  }

  // Links a viewer may follow from a shared page: the web, and anchors in
  // the page. The chat also accepts workspace: links, which only the Portal
  // can serve; here they stay plain text.
  function safeLink(url) {
    var value = String(url || "").trim().toLowerCase();
    return value.indexOf("https://") === 0 || value.indexOf("http://") === 0 || value.charAt(0) === "#";
  }

  // The chat's markdown-it configuration: html off, links and typography on,
  // fences for diagrams and cards kept as escaped code for the enhancers.
  var md = window.markdownit
    ? window.markdownit({
      html: false,
      linkify: true,
      breaks: true,
      typographer: true,
      highlight: function (str, lang) {
        var language = normalizeFenceLanguage(lang);
        if (isMermaidLanguage(language)) {
          return "<pre><code class=\"language-mermaid\">" + escapeHtml(str) + "</code></pre>";
        }
        if (isEfpCardLanguage(language)) {
          return "<pre><code class=\"language-" + language + "\">" + escapeHtml(str) + "</code></pre>";
        }
        if (language && window.hljs && window.hljs.getLanguage(language)) {
          return "<pre><code class=\"hljs language-" + language + "\">" + window.hljs.highlight(str, { language: language }).value + "</code></pre>";
        }
        var className = language ? " class=\"language-" + language + "\"" : "";
        return "<pre><code" + className + ">" + escapeHtml(str) + "</code></pre>";
      },
    })
    : null;
  if (md) md.validateLink = safeLink;
  // Same CJK-friendly emphasis as the chat (static/js/markdown_cjk_friendly.js).
  if (md && typeof window.markdownItCjkFriendly === "function") md.use(window.markdownItCjkFriendly);

  function renderMarkdown(text) {
    var normalized = normalizeMarkdownText(text);
    if (!normalized.trim()) return "";
    if (md) return md.render(normalized);
    return "<pre>" + escapeHtml(normalized) + "</pre>";
  }

  function renderIcons() {
    if (window.lucide && typeof window.lucide.createIcons === "function") {
      try { window.lucide.createIcons(); } catch (e) { /* icons are decoration */ }
    }
  }

  // ----- display blocks -------------------------------------------------------

  function blockText(block) {
    var keys = ["content", "text", "message", "output", "result", "value"];
    for (var i = 0; i < keys.length; i += 1) {
      if (typeof block[keys[i]] === "string" && block[keys[i]].trim()) return block[keys[i]];
    }
    return "";
  }

  function blockCode(block) {
    var keys = ["code", "content", "text", "message", "output", "result", "value"];
    for (var i = 0; i < keys.length; i += 1) {
      if (typeof block[keys[i]] === "string" && block[keys[i]].trim()) return block[keys[i]];
    }
    return "";
  }

  function tableParts(block) {
    var headers = Array.isArray(block.headers) ? block.headers : (Array.isArray(block.columns) ? block.columns : []);
    var rows = Array.isArray(block.rows) ? block.rows : [];
    return { headers: headers, rows: rows.map(function (row) { return Array.isArray(row) ? row : [row]; }) };
  }

  function renderCodeBlock(block) {
    var language = String(block.lang || block.language || "").trim().toLowerCase();
    var code = blockCode(block);
    if (isMermaidLanguage(language)) {
      return "<section class=\"message-block message-block-code\"><pre><code class=\"language-mermaid\">" + escapeHtml(code) + "</code></pre></section>";
    }
    var normalized = normalizeFenceLanguage(language);
    var className = normalized ? "language-" + normalized : "";
    return "<section class=\"message-block message-block-code\"><pre><code class=\"" + className + "\">" + escapeHtml(code) + "</code></pre></section>";
  }

  function renderTableBlock(block) {
    var table = tableParts(block);
    if (!table.headers.length && !table.rows.length) return "<section class=\"message-block message-block-markdown\">" + renderMarkdown(blockText(block)) + "</section>";
    var head = table.headers.length
      ? "<thead><tr>" + table.headers.map(function (h) { return "<th>" + escapeHtml(h) + "</th>"; }).join("") + "</tr></thead>"
      : "";
    var body = table.rows.length
      ? "<tbody>" + table.rows.map(function (row) {
        return "<tr>" + row.map(function (cell) { return "<td>" + escapeHtml(cell) + "</td>"; }).join("") + "</tr>";
      }).join("") + "</tbody>"
      : "";
    return "<section class=\"message-block message-block-table\"><div class=\"message-table-wrap\"><table>" + head + body + "</table></div></section>";
  }

  function fileBlockName(block) {
    var path = String(block.path || block.file_path || block.workspace_path || "");
    return { path: path, name: String(block.name || block.filename || path.split("/").filter(Boolean).pop() || t("file")) };
  }

  function renderFileBlock(block) {
    var file = fileBlockName(block);
    var description = blockText(block);
    return "<section class=\"message-block message-block-file\"><div class=\"message-file-card\">"
      + "<div class=\"message-file-icon\" aria-hidden=\"true\">" + FILE_ICON + "</div>"
      + "<div class=\"message-file-body\"><div class=\"message-file-name\">" + escapeHtml(file.name) + "</div>"
      + (file.path ? "<div class=\"message-file-meta\">" + escapeHtml(file.path) + "</div>" : "")
      + (description ? "<div class=\"message-file-description\">" + escapeHtml(description) + "</div>" : "")
      + "</div></div></section>";
  }

  function renderBlock(block) {
    if (!block || typeof block !== "object") return "";
    var type = String(block.type || "").toLowerCase();
    if (type === "code") return renderCodeBlock(block);
    if (type === "table") return renderTableBlock(block);
    if (type === "file") return renderFileBlock(block);
    if (type === "callout") {
      var tone = String(block.tone || "info").toLowerCase().replace(/[^a-z]/g, "");
      var title = String(block.title || "").trim();
      return "<section class=\"message-block\"><div class=\"message-callout is-" + tone + "\">"
        + (title ? "<div class=\"message-callout-title\">" + escapeHtml(title) + "</div>" : "")
        + "<div class=\"message-callout-content\">" + renderMarkdown(blockText(block)) + "</div></div></section>";
    }
    if (type === "tool_result") {
      var resultTitle = String(block.title || t("Tool result"));
      var status = String(block.status || "").toLowerCase().replace(/[^a-z_]/g, "");
      return "<section class=\"message-block\"><details class=\"message-tool-result share-tool-result" + (status ? " is-" + status : "") + "\">"
        + "<summary class=\"message-tool-result-title\">" + escapeHtml(resultTitle) + "</summary>"
        + "<div class=\"message-tool-result-content\">" + renderMarkdown(blockText(block)) + "</div></details></section>";
    }
    return "<section class=\"message-block message-block-markdown\">" + renderMarkdown(blockText(block)) + "</section>";
  }

  function renderAssistantBody(markdown, blocks) {
    var html = (blocks || []).map(renderBlock).join("");
    if (html) return html;
    return renderMarkdown(markdown);
  }

  // ----- the transcript ---------------------------------------------------------

  function formatTime(value) {
    if (!value) return "";
    var parsed = new Date(value);
    if (isNaN(parsed.getTime())) return "";
    return parsed.toLocaleString([], { year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" });
  }

  function isToolRow(message) {
    if (message.kind === "tool") return true;
    if (message.kind === "answer") return false;
    var text = String(message.markdown || "").replace(/^\s+/, "");
    return TOOL_ROW_PREFIXES.some(function (prefix) { return text.indexOf(prefix) === 0; });
  }

  // Consecutive assistant answers form one row, like the chat; tool rows stay
  // apart so the toggle can hide them; a member's answers to a question card
  // are their own row.
  function groupMessages(messages) {
    var entries = [];
    var current = null;
    var questionIndex = 0;
    (messages || []).forEach(function (message) {
      if (!message || typeof message !== "object") return;
      if (message.kind === "answer") {
        current = null;
        entries.push({ type: "answer", message: message });
        return;
      }
      if (message.role === "user") {
        current = null;
        questionIndex += 1;
        entries.push({ type: "user", message: message, index: questionIndex });
        return;
      }
      if (message.role !== "assistant") return;
      if (isToolRow(message)) {
        current = null;
        entries.push({ type: "tool", message: message });
        return;
      }
      if (!current) {
        current = { type: "assistant", messages: [] };
        entries.push(current);
      }
      current.messages.push(message);
    });
    return entries;
  }

  function attachmentsHtml(attachments) {
    if (!Array.isArray(attachments) || !attachments.length) return "";
    var token = page.dataset.shareToken;
    var items = attachments.map(function (attachment) {
      var name = String(attachment.name || attachment.file_id || t("attachment"));
      if (attachment.type === "image" && attachment.file_id) {
        var src = "/share/" + encodeURIComponent(token) + "/files/" + encodeURIComponent(attachment.file_id);
        return "<a class=\"share-attachment-image\" href=\"" + escapeHtml(src) + "\" target=\"_blank\" rel=\"noopener\"><img class=\"message-attachment-thumb\" src=\"" + escapeHtml(src) + "\" alt=\"" + escapeHtml(name) + "\" loading=\"lazy\" /></a>";
      }
      return "<span class=\"message-attachment-file\" title=\"" + escapeHtml(name) + "\"><span class=\"message-attachment-body\"><span class=\"message-attachment-name\">" + escapeHtml(name) + "</span></span></span>";
    });
    return "<div class=\"message-attachments\">" + items.join("") + "</div>";
  }

  function initialOf(name) {
    var text = String(name || "").trim();
    return text ? text.charAt(0).toUpperCase() : "A";
  }

  // author, time and (for the assistant) its initial and the model that answered
  function metaHtml(author, when, extra) {
    var avatar = extra && extra.avatar ? "<span class=\"share-avatar\" aria-hidden=\"true\">" + escapeHtml(initialOf(author)) + "</span>" : "";
    return "<div class=\"share-msg-meta\">" + avatar
      + "<span class=\"share-msg-author\">" + escapeHtml(author) + "</span>"
      + (when ? "<time>" + escapeHtml(when) + "</time>" : "")
      + (extra && extra.model ? "<span class=\"share-model\" title=\"" + escapeHtml(t("Model that answered")) + "\">" + escapeHtml(extra.model) + "</span>" : "")
      + "</div>";
  }

  function renderEntries(entries) {
    var assistantName = page.dataset.assistantName || t("Assistant");
    return entries.map(function (entry) {
      if (entry.type === "user") {
        var user = entry.message;
        return "<section class=\"share-msg share-msg--user share-question\" id=\"q-" + entry.index + "\">"
          + metaHtml(user.author || t("User"), formatTime(user.timestamp))
          + "<div class=\"share-bubble\"><div class=\"share-bubble-text\">" + escapeHtml(user.markdown || "") + "</div>"
          + attachmentsHtml(user.attachments) + "</div></section>";
      }
      if (entry.type === "answer") {
        var answer = entry.message;
        var pairs = Array.isArray(answer.pairs) ? answer.pairs : [];
        return "<section class=\"share-msg share-msg--user share-answer\">"
          + metaHtml(answer.author || t("User"), formatTime(answer.timestamp))
          + "<div class=\"share-bubble share-bubble--answer\">"
          + "<div class=\"share-answer-note\"><i data-lucide=\"circle-check\" class=\"share-icon\" aria-hidden=\"true\"></i>" + escapeHtml(t("Answered the assistant's question")) + "</div>"
          + "<dl class=\"share-answer-pairs\">" + pairs.map(function (pair) {
            return "<dt>" + escapeHtml(pair.label || t("Answer")) + "</dt><dd>" + escapeHtml(pair.value || "") + "</dd>";
          }).join("") + "</dl></div></section>";
      }
      if (entry.type === "tool") {
        var tool = entry.message;
        return "<section class=\"share-msg share-tool-row\">"
          + "<div class=\"share-tool\"><i data-lucide=\"wrench\" class=\"share-icon\" aria-hidden=\"true\"></i>"
          + "<div class=\"message-markdown\">" + renderMarkdown(tool.markdown) + "</div></div></section>";
      }
      var first = entry.messages[0] || {};
      var last = entry.messages[entry.messages.length - 1] || first;
      var markdown = entry.messages.map(function (m) { return String(m.markdown || ""); }).filter(function (text) { return text.trim(); }).join("\n\n");
      var blocks = entry.messages.reduce(function (all, m) { return all.concat(Array.isArray(m.display_blocks) ? m.display_blocks : []); }, []);
      return "<section class=\"share-msg share-msg--assistant\">"
        + metaHtml(first.author || assistantName, formatTime(last.timestamp || first.timestamp), { avatar: true, model: String(last.model || first.model || "").trim() })
        + "<div class=\"share-msg-body message-markdown\">" + renderAssistantBody(markdown, blocks) + "</div></section>";
    }).join("");
  }

  function renderToc(entries) {
    if (!dom.toc) return;
    var questions = entries.filter(function (entry) { return entry.type === "user"; });
    if (questions.length < 2) {
      dom.toc.innerHTML = "";
      dom.toc.classList.add("hidden");
      return;
    }
    dom.toc.classList.remove("hidden");
    dom.toc.innerHTML = "<div class=\"share-toc-title\">" + escapeHtml(t("In this conversation")) + "</div><ol class=\"share-toc-list\">"
      + questions.map(function (entry) {
        var text = String(entry.message.markdown || "").replace(/\s+/g, " ").trim();
        if (text.length > 80) text = text.slice(0, 77).trim() + ELLIPSIS;
        return "<li><a href=\"#q-" + entry.index + "\"><span class=\"share-toc-num\">" + entry.index + "</span><span>" + escapeHtml(text || t("(attachment)")) + "</span></a></li>";
      }).join("") + "</ol>";
  }

  function enhance(root) {
    root.querySelectorAll("a").forEach(function (anchor) {
      anchor.classList.add("message-link");
      if ((anchor.getAttribute("href") || "").charAt(0) === "#") return;
      anchor.target = "_blank";
      anchor.rel = "noopener noreferrer";
    });
    root.querySelectorAll("table").forEach(function (table) {
      if (table.closest(".message-table-wrap")) return;
      var wrapper = document.createElement("div");
      wrapper.className = "message-table-wrap";
      table.parentNode.insertBefore(wrapper, table);
      wrapper.appendChild(table);
    });
    root.querySelectorAll("pre > code").forEach(function (code) {
      if (code.closest(".message-codeblock") || code.closest(".share-diagram")) return;
      var language = languageOf(code);
      if (isMermaidLanguage(language)) return; // drawn below
      // Cards in their read-only form: no Fix/Replay buttons, nothing that
      // talks to an assistant.
      if (isEfpCardLanguage(language) && window.EfpCards && window.EfpCards.buildFromCode(code, { readonly: true, surface: "share" })) return;
      if (window.hljs && !code.classList.contains("hljs") && language && window.hljs.getLanguage(language)) {
        try { window.hljs.highlightElement(code); } catch (e) { /* keep plain */ }
      }
      var pre = code.parentElement;
      if (!pre) return;
      var wrapper = document.createElement("div");
      wrapper.className = "message-codeblock";
      var toolbar = document.createElement("div");
      toolbar.className = "message-codeblock-toolbar";
      var lang = document.createElement("span");
      lang.className = "message-codeblock-lang";
      lang.textContent = language || "text";
      var copy = document.createElement("button");
      copy.type = "button";
      copy.className = "message-codeblock-copy";
      copy.textContent = t("Copy");
      copy.addEventListener("click", function () {
        copyToClipboard(code.textContent || "").then(function (ok) {
          if (!ok) return;
          copy.textContent = t("Copied");
          setTimeout(function () { copy.textContent = t("Copy"); }, 1400);
        });
      });
      toolbar.appendChild(lang);
      toolbar.appendChild(copy);
      pre.parentNode.insertBefore(wrapper, pre);
      wrapper.appendChild(toolbar);
      wrapper.appendChild(pre);
    });
    renderDiagrams(root);
  }

  // ----- Mermaid ----------------------------------------------------------------

  var mermaidLoad = null;

  function ensureMermaid() {
    if (window.mermaid) return Promise.resolve(window.mermaid);
    if (mermaidLoad) return mermaidLoad;
    var meta = document.querySelector("meta[name=\"portal-mermaid-src\"]");
    var src = meta ? meta.getAttribute("content") : "";
    if (!src) return Promise.reject(new Error("no diagram renderer"));
    mermaidLoad = new Promise(function (resolve, reject) {
      var script = document.createElement("script");
      script.src = src;
      script.async = true;
      script.onload = function () { window.mermaid ? resolve(window.mermaid) : reject(new Error("mermaid missing")); };
      script.onerror = function () { mermaidLoad = null; reject(new Error("mermaid download failed")); };
      document.head.appendChild(script);
    });
    return mermaidLoad;
  }

  // The chat's Mermaid configuration (chat_ui.js mermaidBaseConfig): strict
  // sandboxing, no error bombs painted into the page, the page's own font.
  function initializeMermaid(mermaid) {
    var theme = document.documentElement.getAttribute("data-theme") === "dark" ? "dark" : "default";
    if (state.mermaidTheme === theme) return;
    mermaid.initialize({ startOnLoad: false, securityLevel: "strict", suppressErrorRendering: true, theme: theme, fontFamily: "inherit" });
    state.mermaidTheme = theme;
  }

  function renderDiagrams(root) {
    var blocks = Array.prototype.filter.call(root.querySelectorAll("pre > code"), function (code) {
      return isMermaidLanguage(languageOf(code)) && !code.closest(".share-diagram");
    });
    if (!blocks.length) return;
    ensureMermaid().then(function (mermaid) {
      initializeMermaid(mermaid);
      blocks.forEach(function (code) {
        var source = code.textContent || "";
        state.mermaidSeq += 1;
        var id = "share-diagram-" + state.mermaidSeq;
        Promise.resolve(mermaid.render(id, source)).then(function (result) {
          var figure = document.createElement("figure");
          figure.className = "share-diagram";
          figure.innerHTML = result && result.svg ? result.svg : "";
          var pre = code.parentElement;
          if (pre && pre.parentNode && figure.innerHTML) pre.parentNode.replaceChild(figure, pre);
        }).catch(function () { /* the source stays visible as code */ });
      });
    }).catch(function () { /* the source stays visible as code */ });
  }

  // ----- clipboard --------------------------------------------------------------

  function copyToClipboard(text) {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      return navigator.clipboard.writeText(text).then(function () { return true; }, function () { return fallbackCopy(text); });
    }
    return Promise.resolve(fallbackCopy(text));
  }

  function fallbackCopy(text) {
    var area = document.createElement("textarea");
    area.value = text;
    area.setAttribute("readonly", "");
    area.style.position = "fixed";
    area.style.opacity = "0";
    document.body.appendChild(area);
    area.select();
    var ok = false;
    try { ok = document.execCommand("copy"); } catch (e) { ok = false; }
    document.body.removeChild(area);
    return ok;
  }

  function markdownCell(value) {
    return String(value == null ? "" : value).replace(/\|/g, "\\|").replace(/\s*\n\s*/g, " ");
  }

  function blockAsMarkdown(block) {
    if (!block || typeof block !== "object") return "";
    var type = String(block.type || "").toLowerCase();
    if (type === "code") {
      return "```" + String(block.lang || block.language || "") + "\n" + blockCode(block) + "\n```";
    }
    if (type === "table") {
      var table = tableParts(block);
      if (!table.headers.length && !table.rows.length) return blockText(block).trim();
      var head = table.headers.length ? table.headers : table.rows[0];
      var body = table.headers.length ? table.rows : table.rows.slice(1);
      var line = function (cells) { return "| " + cells.map(markdownCell).join(" | ") + " |"; };
      return [line(head), "| " + head.map(function () { return "---"; }).join(" | ") + " |"]
        .concat(body.map(line)).join("\n");
    }
    if (type === "file") {
      var file = fileBlockName(block);
      var description = blockText(block).trim();
      return t("File: {name}", { name: file.name }) + (file.path && file.path !== file.name ? " (" + file.path + ")" : "") + (description ? " " + EM_DASH + " " + description : "");
    }
    return blockText(block).trim();
  }

  function transcriptAsMarkdown(data, includeTools) {
    var lines = ["# " + String(data.title || t("Shared session")), ""];
    (data.messages || []).forEach(function (message) {
      if (isToolRow(message) && !includeTools) return;
      var author = String(message.author || (message.role === "user" ? t("User") : t("Assistant")));
      if (message.kind === "answer") {
        lines.push(t("{author} answered:", { author: "**" + author + "**" }), "");
        (message.pairs || []).forEach(function (pair) {
          lines.push("- " + String(pair.label || t("Answer")) + ": " + String(pair.value || ""));
        });
        lines.push("");
        return;
      }
      lines.push("**" + author + "**", "");
      var text = String(message.markdown || "").trim();
      if (text) lines.push(text);
      (message.display_blocks || []).forEach(function (block) {
        var body = blockAsMarkdown(block);
        if (body) lines.push("", body);
      });
      var names = (message.attachments || []).map(function (a) { return a.name; }).filter(Boolean);
      if (names.length) lines.push("", t("Attachments: {names}", { names: names.join(", ") }));
      lines.push("");
    });
    return lines.join("\n").replace(/\s+$/, "") + "\n";
  }

  // ----- states and loading -----------------------------------------------------

  function stateCard(icon, title, body) {
    return "<div class=\"share-state\"><i data-lucide=\"" + icon + "\" class=\"share-icon\" aria-hidden=\"true\"></i>"
      + "<div><strong>" + escapeHtml(title) + "</strong>" + escapeHtml(body) + "</div></div>";
  }

  function stateHtml(data) {
    var owner = data.owner_display_name || t("the owner");
    if (data.status === "assistant_stopped") {
      return stateCard("moon", t("The assistant that owns this session is not running."), t("Ask {owner} to start it, then use Refresh. Nothing here starts it.", { owner: owner }));
    }
    if (data.status === "session_missing") {
      return stateCard("file-x", t("This session is gone."), t("It was deleted on the assistant, or it expired."));
    }
    if (data.status === "runtime_unreachable") {
      return stateCard("cloud-off", data.error || t("The assistant did not answer."), t("Try Refresh in a moment."));
    }
    if (!data.messages || !data.messages.length) {
      return stateCard("message-square", t("No messages yet."), t("The conversation shows up here once it starts."));
    }
    return "";
  }

  function transcriptFingerprint(data) {
    try {
      return JSON.stringify([data.title, data.messages]);
    } catch (e) {
      return String(Date.now());
    }
  }

  function applyToolVisibility() {
    if (!dom.transcript) return;
    dom.transcript.classList.toggle("is-tools-hidden", !(dom.showTools && dom.showTools.checked));
  }

  function scrollToHashOnce() {
    if (state.scrolledToHash || !window.location.hash) return;
    state.scrolledToHash = true;
    var target = document.getElementById(window.location.hash.slice(1));
    if (target) target.scrollIntoView({ block: "start" });
  }

  function render() {
    var data = state.data;
    if (!data || !dom.transcript) return;
    var title = String(data.title || t("Shared session"));
    if (dom.title) dom.title.textContent = title;
    document.title = title + " " + MIDDLE_DOT + " " + t("Shared session");
    if (dom.loadedAt) dom.loadedAt.textContent = t("Updated {time}", { time: formatTime(data.loaded_at) });
    if (dom.running) dom.running.classList.toggle("hidden", !data.running);
    if (dom.openPortal) {
      var canOpen = Boolean(data.can_open_in_portal && data.portal_url);
      dom.openPortal.classList.toggle("hidden", !canOpen);
      if (canOpen) {
        dom.openPortal.href = data.portal_url;
        dom.openPortal.dataset.agentId = String(data.agent_id || "");
        dom.openPortal.dataset.sessionId = String(data.session_id || "");
      }
    }
    var notice = stateHtml(data);
    if (notice) {
      dom.transcript.innerHTML = notice;
      if (dom.toc) { dom.toc.innerHTML = ""; dom.toc.classList.add("hidden"); }
      state.fingerprint = "";
      renderIcons();
      return;
    }
    applyToolVisibility();
    var fingerprint = transcriptFingerprint(data);
    if (fingerprint !== state.fingerprint) {
      state.fingerprint = fingerprint;
      var entries = groupMessages(data.messages);
      dom.transcript.innerHTML = renderEntries(entries);
      renderToc(entries);
      enhance(dom.transcript);
    }
    renderIcons();
    scrollToHashOnce();
  }

  function scheduleAutoRefresh() {
    if (state.timer) { clearTimeout(state.timer); state.timer = null; }
    if (!state.data || !state.data.running) return;
    state.timer = setTimeout(function () {
      state.timer = null;
      if (document.visibilityState === "visible") load({ fresh: false });
      else scheduleAutoRefresh();
    }, AUTO_REFRESH_MS);
  }

  function load(options) {
    if (!dataUrl || state.loading) return Promise.resolve();
    state.loading = true;
    if (dom.refresh) dom.refresh.disabled = true;
    var url = dataUrl + (options && options.fresh ? "?fresh=1" : "");
    return fetch(url, { credentials: "same-origin", headers: { Accept: "application/json" } })
      .then(function (response) {
        if (response.status === 401 || response.status === 403) {
          return response.json().catch(function () { return {}; }).then(function (body) {
            window.location.href = body.login_url || "/login";
            return null;
          });
        }
        if (response.status === 404) {
          if (dom.transcript) {
            dom.transcript.innerHTML = stateCard("link-2-off", t("This link is no longer available."), t("It was turned off, or the session was deleted."));
            renderIcons();
          }
          return null;
        }
        return response.json();
      })
      .then(function (data) {
        if (!data) return;
        state.data = data;
        render();
        scheduleAutoRefresh();
      })
      .catch(function () {
        if (dom.transcript && !state.data) {
          dom.transcript.innerHTML = stateCard("wifi-off", t("The conversation could not be loaded."), t("Check your connection and use Refresh."));
          renderIcons();
        }
      })
      .then(function () {
        state.loading = false;
        if (dom.refresh) dom.refresh.disabled = false;
      });
  }

  if (dom.refresh) dom.refresh.addEventListener("click", function () { load({ fresh: true }); });
  if (dom.showTools) dom.showTools.addEventListener("change", applyToolVisibility);
  if (dom.copyMarkdown) {
    dom.copyMarkdown.addEventListener("click", function () {
      if (!state.data) return;
      copyToClipboard(transcriptAsMarkdown(state.data, Boolean(dom.showTools && dom.showTools.checked))).then(function (ok) {
        var label = dom.copyMarkdown.querySelector("span");
        if (!label) return;
        label.textContent = ok ? t("Copied") : t("Copy failed");
        setTimeout(function () { label.textContent = t("Copy as Markdown"); }, 1600);
      });
    });
  }
  if (dom.openPortal) {
    // The Portal opens an assistant on its last session; make it this one.
    dom.openPortal.addEventListener("click", function () {
      var agentId = dom.openPortal.dataset.agentId || "";
      var sessionId = dom.openPortal.dataset.sessionId || "";
      if (!agentId || !sessionId) return;
      try { localStorage.setItem(LAST_SESSION_KEY_PREFIX + agentId, sessionId); } catch (e) { /* the assistant opens on its own last session */ }
    });
  }
  document.addEventListener("visibilitychange", function () {
    if (document.visibilityState === "visible" && state.data && state.data.running && !state.timer) load({ fresh: false });
  });
  // lucide is deferred: the header icons are drawn once it is here, and again
  // with every render.
  window.addEventListener("load", renderIcons);

  load({ fresh: false });
})();
