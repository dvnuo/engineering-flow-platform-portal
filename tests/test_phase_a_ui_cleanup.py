from pathlib import Path


def test_app_template_contains_new_portal_shell():
    html = Path("app/templates/app.html").read_text(encoding="utf-8")
    assert "portal-shell" in html
    assert "portal-rail" in html
    assert "portal-agent-pane" in html
    assert "sessions-drawer" in html
    assert "sessions-drawer-body" in html
    assert "header-new-chat-btn" in html
    assert "bundles-menu-btn" in html
    assert "home-open-bundles-btn" in html
    assert "Message an assistant" in html


def test_chat_response_partial_contract():
    partial = Path("app/templates/partials/chat_response.html").read_text(encoding="utf-8")
    assert "message-row" in partial
    assert "message-surface-assistant" in partial
    assert "message-timestamp" in partial
    assert "message-markdown" in partial


def test_frontend_assets_include_phase_b_fixups():
    js_source = Path("app/static/js/chat_ui.js").read_text(encoding="utf-8")
    css_source = Path("app/static/css/app.css").read_text(encoding="utf-8")
    web_source = Path("app/web.py").read_text(encoding="utf-8")

    assert "ensureRunningSelectedAssistant" in js_source
    assert "setButtonDisabled" in js_source
    assert "removeTemporaryAssistantRows" in js_source
    assert "removeLatestOptimisticUserRow" in js_source
    assert "didAppendAttachmentHistoryForPendingSend" in js_source
    assert "data-temporary-assistant=\"1\"" in js_source
    assert "closest('.message-row')" in js_source
    assert 'data-optimistic-user="1"' in js_source
    assert "getLatestOptimisticUserArticle" in js_source
    assert "portal-statusline" in js_source
    assert "state.detailOpen = false" in js_source
    assert 'document.documentElement.getAttribute("data-theme")' in js_source
    assert 'toolPanelTitle?.textContent === "Sessions"' not in js_source
    assert ".assistant-header" not in js_source
    assert ".flex.flex-col" not in js_source
    assert "message message-error flex gap-3 py-3" not in js_source
    assert "buildPendingAssistantRowForEvents" in js_source
    assert js_source.count("#sessions-new-chat-btn") == 1
    assert js_source.count("[data-session-id]") == 1
    assert "portal-suggest-item" in js_source
    assert "portal-suggest-desc" in js_source
    assert "hover:bg-slate-700" not in js_source
    assert "bg-slate-700" not in js_source
    assert "modal._keyHandler = null" not in js_source
    assert "button.dataset.defaultTitle" in js_source
    assert "portal-inline-state" in js_source
    assert "portal-bundle-row" in js_source

    detail_meta_block = js_source[js_source.find("function renderAgentMeta"):js_source.find("function renderAgentActions")]
    assert "text-slate-500 uppercase tracking-wide" not in detail_meta_block
    assert "bg-slate-100 dark:bg-slate-800" not in detail_meta_block
    assert "text-blue-500" not in detail_meta_block

    assert ".portal-tool-panel" in css_source
    assert ".portal-tool-panel-head" in css_source
    assert ".portal-tool-panel-body" in css_source
    assert ".portal-detail-card" in css_source
    assert ".portal-detail-stack" in css_source
    assert ".portal-detail-section" in css_source
    assert ".portal-detail-label" in css_source
    assert ".portal-detail-code" in css_source
    assert ".portal-resource-pill" in css_source
    assert ".portal-usage-grid" in css_source
    assert ".portal-inline-error" in css_source
    assert ".portal-link-inline" in css_source
    assert ".portal-suggest-item" in css_source
    assert ".portal-suggest-desc" in css_source
    assert ".portal-inline-state" in css_source
    assert ".portal-detail-note" in css_source
    assert ".portal-toast" in css_source
    assert ".portal-toast-inner" in css_source
    assert ".portal-panel-stack" in css_source
    assert ".portal-panel-section" in css_source
    assert ".portal-panel-pre" in css_source
    assert ".portal-form-input" in css_source
    assert ".portal-form-select" in css_source
    assert ".portal-form-textarea" in css_source
    assert ".portal-btn" in css_source
    assert ".portal-modal-titlebar" in css_source
    assert ".portal-editor-modal-card" in css_source
    assert ".portal-bundle-row" in css_source
    assert ".message-surface-error" in css_source
    assert ".portal-detail-action-grid" in css_source
    assert ".portal-detail-action-btn" in css_source
    assert ".portal-thinking-block" in css_source
    assert ".portal-thinking-toggle" in css_source
    assert ".portal-statusline.is-error" in css_source
    assert ":disabled" in css_source

    assert '"timestamp": datetime.now().strftime("%H:%M")' in web_source


def test_templates_portalized_for_panel_visual_consistency():
    app_html = Path("app/templates/app.html").read_text(encoding="utf-8")
    thinking_html = Path("app/templates/partials/thinking_process_panel.html").read_text(encoding="utf-8")
    tasks_html = Path("app/templates/partials/my_tasks_panel.html").read_text(encoding="utf-8")
    task_detail_html = Path("app/templates/partials/task_detail_panel.html").read_text(encoding="utf-8")
    users_html = Path("app/templates/partials/users_panel.html").read_text(encoding="utf-8")
    skills_html = Path("app/templates/partials/skills_panel.html").read_text(encoding="utf-8")
    files_html = Path("app/templates/partials/files_panel.html").read_text(encoding="utf-8")
    bundles_html = Path("app/templates/partials/requirement_bundles_panel.html").read_text(encoding="utf-8")
    settings_html = Path("app/templates/partials/settings_panel.html").read_text(encoding="utf-8")

    assert "portal-toast" in app_html
    assert "portal-toast-inner" in app_html
    assert "text-xs text-slate-500" not in app_html

    assert "portal-panel-stack" in thinking_html
    assert "portal-panel-stack" in tasks_html
    assert "portal-panel-stack" in task_detail_html
    assert "portal-panel-stack" in users_html
    assert "portal-panel-stack" in skills_html
    assert "portal-panel-stack" in files_html
    assert "portal-panel-stack" in bundles_html
    assert ("portal-form-input" in settings_html) or ("portal-panel-section" in settings_html)
