import pytest
import shutil
import subprocess
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
    assert "portal-modal-feedback" in html
    assert "portal-modal-copy" in html
    assert "portal-form-textarea-mono" in html
    assert "portal-modal-actions-stretch" in html
    assert "muted tiny" not in html
    assert "text-slate-400" not in html
    assert "bg-slate-800" not in html
    assert "bg-purple-600" not in html
    assert "hover:bg-purple-500" not in html


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
    assert "portal-file-browser" in js_source
    assert "portal-file-row" in js_source
    assert "portal-file-preview-header" in js_source
    assert "portal-settings-instance-card" in js_source
    assert "portal-instance-remove" in js_source
    assert "portal-system-prompt-item" in js_source
    assert "portal-system-prompt-check" in js_source
    assert "setModalFeedback" in js_source
    assert "async function clearChat()" in js_source
    assert "\nfunction clearChat() {" not in js_source
    assert "text-xs text-slate-400" not in js_source
    assert "text-red-500" not in js_source
    assert "text-green-500" not in js_source
    assert "text-rose-500" not in js_source
    assert "text-green-400 tiny" not in js_source
    assert "text-red-400 tiny" not in js_source
    assert "muted tiny" not in js_source
    assert "hover:text-blue-600" not in js_source
    assert "border-slate-300" not in js_source

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
    assert ".portal-inline-state.is-success" in css_source
    assert ".portal-modal-copy" in css_source
    assert ".portal-modal-feedback" in css_source
    assert ".portal-form-textarea-mono" in css_source
    assert ".portal-modal-actions-stretch" in css_source
    assert ".portal-settings-section-head" in css_source
    assert ".portal-settings-instance-card" in css_source
    assert ".portal-instance-remove" in css_source
    assert ".portal-password-toggle" in css_source
    assert ".portal-file-browser" in css_source
    assert ".portal-file-row" in css_source
    assert ".portal-file-preview-header" in css_source
    assert ".portal-file-binary-meta" in css_source
    assert ".portal-system-prompt-item" in css_source
    assert ".portal-system-prompt-check" in css_source
    assert ".message-surface-error" in css_source
    assert ".portal-detail-action-grid" in css_source
    assert ".portal-detail-action-btn" in css_source
    assert ".portal-thinking-block" in css_source
    assert ".portal-thinking-toggle" in css_source
    assert ".portal-statusline.is-error" in css_source
    assert ":disabled" in css_source

    assert '"timestamp": datetime.now().strftime("%H:%M")' in web_source


def test_templates_portalized_for_panel_visual_consistency():
    js_source = Path("app/static/js/chat_ui.js").read_text(encoding="utf-8")
    app_html = Path("app/templates/app.html").read_text(encoding="utf-8")
    thinking_html = Path("app/templates/partials/thinking_process_panel.html").read_text(encoding="utf-8")
    tasks_html = Path("app/templates/partials/my_tasks_panel.html").read_text(encoding="utf-8")
    task_detail_html = Path("app/templates/partials/task_detail_panel.html").read_text(encoding="utf-8")
    users_html = Path("app/templates/partials/users_panel.html").read_text(encoding="utf-8")
    skills_html = Path("app/templates/partials/skills_panel.html").read_text(encoding="utf-8")
    files_html = Path("app/templates/partials/files_panel.html").read_text(encoding="utf-8")
    bundles_html = Path("app/templates/partials/requirement_bundles_panel.html").read_text(encoding="utf-8")
    settings_html = Path("app/templates/partials/settings_panel.html").read_text(encoding="utf-8")
    usage_html = Path("app/templates/partials/usage_panel.html").read_text(encoding="utf-8")
    bundles_page_html = Path("app/templates/requirement_bundles.html").read_text(encoding="utf-8")

    assert "portal-toast" in app_html
    assert "class=\"primary\"" not in app_html
    assert "class=\"close-btn\"" not in app_html
    assert "portal-btn is-primary" in app_html
    assert "portal-modal-close" in app_html
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
    assert "portal-settings-section-head" in settings_html
    assert "portal-settings-instance-card" in settings_html
    assert "portal-instance-remove" in settings_html
    assert "portal-link-inline" in settings_html
    assert ("portal-password-toggle" in settings_html) or ("portal-password-toggle" in js_source)
    assert "portal-panel-stack" in usage_html
    assert "portal-usage-grid" in usage_html
    assert "text-slate" not in settings_html
    assert "bg-slate" not in settings_html
    assert "border-slate" not in settings_html
    assert "dark:" not in settings_html
    assert "bg-blue-500" not in settings_html
    assert "text-slate" not in usage_html
    assert "bg-slate" not in usage_html
    assert "border-slate" not in usage_html
    assert "dark:" not in usage_html
    assert 'hx-on::after-request="if(event.detail.successful) { closeToolPanel(); }"' not in settings_html
    assert 'id="settings-status"' in settings_html
    assert "Settings saved!" not in settings_html
    assert "setTimeout(function(){closeToolPanel();}, 500)" not in settings_html
    assert "onclick=\"if(typeof showToast" not in settings_html
    assert "<label class=\"portal-checkbox-row\">" not in settings_html
    assert 'data-settings-status' in settings_html
    assert 'Please select an agent first' not in settings_html
    assert 'Please select an assistant first' in settings_html
    assert 'text-blue-500' not in settings_html
    assert 'portal-note-500' not in settings_html
    assert 'portal-note-800' not in settings_html
    assert 'portal-note-200' not in settings_html
    assert 'portal-accent-bg' not in settings_html
    assert '.border-emerald-600, .border-red-600' not in settings_html
    assert 'input-preview-badge' in js_source
    assert 'bg-yellow-500' not in js_source
    assert 'bg-green-500' not in js_source
    assert 'bg-red-500' not in js_source
    assert '.portal-inline-success' in Path("app/static/css/app.css").read_text(encoding="utf-8")
    assert '.portal-file-icon' in Path("app/static/css/app.css").read_text(encoding="utf-8")
    assert '.input-preview-badge' in Path("app/static/css/app.css").read_text(encoding="utf-8")
    assert '.portal-standalone-page' in Path("app/static/css/app.css").read_text(encoding="utf-8")
    assert '.portal-standalone-page-back' in Path("app/static/css/app.css").read_text(encoding="utf-8")
    assert 'portal-standalone-page' in bundles_page_html
    assert 'portal-standalone-page-back' in bundles_page_html
    assert 'bg-slate-100' not in bundles_page_html
    assert 'dark:bg-slate-950' not in bundles_page_html
    assert 'border-slate-200' not in bundles_page_html


def test_chat_ui_js_parses_when_node_available():
    if not shutil.which("node"):
        pytest.skip("node not available")
    result = subprocess.run(
        ["node", "--check", "app/static/js/chat_ui.js"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
