import pytest
import shutil
import subprocess
from pathlib import Path
from jinja2 import Environment, FileSystemLoader


def test_app_template_contains_new_portal_shell():
    html = Path("app/templates/app.html").read_text(encoding="utf-8")
    assert "portal-shell" in html
    assert "portal-rail" in html
    assert "portal-secondary-pane" in html
    assert "btn-sessions" in html
    assert "header-new-chat-btn" in html
    assert "bundles-menu-btn" in html
    assert "home-open-bundles-btn" in html
    assert "Ask me anything..." in html
    assert "portal-modal-feedback" in html
    assert "portal-modal-copy" in html
    assert "portal-form-textarea-mono" not in html
    assert "portal-modal-actions-stretch" not in html
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
    assert "data-display-blocks" in partial


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
    assert "data-server-path" in js_source
    assert 'portal-link-inline portal-breadcrumb-link' in js_source
    assert "data-server-path=\"/\"" in js_source
    assert 'portal-breadcrumb-sep' in js_source
    assert 'onclick="loadServerFiles(' not in js_source
    assert 'class="breadcrumb-link"' not in js_source
    assert "data-path=\"/\"" not in js_source
    assert "querySelectorAll('.breadcrumb-link')" not in js_source
    assert 'link.dataset.path' not in js_source
    assert "e.target.closest('.name-cell')" in js_source
    assert "e.stopPropagation();" in js_source
    assert "console.log(" not in js_source
    assert "portal-breadcrumb-link" in js_source
    assert 'root.dataset.actionsBound = "1"' in js_source
    assert "data-settings-action" in js_source
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
    assert "renderDisplayBlocksToHtml" in js_source
    assert "enhanceMarkdownBlock" in js_source
    assert "copyText(" in js_source
    assert "getDisplayBlockText" in js_source
    assert "block.content" in js_source
    assert "block.text" in js_source
    assert "block.message" in js_source
    assert ("block.output" in js_source) or ("block.result" in js_source)
    assert js_source.count("getDisplayBlockText(") >= 3
    assert "data-display-blocks" in js_source or "dataset.displayBlocks" in js_source
    assert ".message-codeblock" in css_source
    assert ".message-codeblock-toolbar" in css_source
    assert ".message-table-wrap" in css_source
    assert ".message-callout" in css_source
    assert ".message-tool-result" in css_source
    assert ".message-tool-result.is-success" in css_source
    assert ".message-tool-result.is-error" in css_source
    assert ".message-tool-result.is-warning" in css_source
    assert "min-width: 1180px;" not in css_source
    assert "block?.columns" in js_source

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
    group_shared_context_list_html = Path("app/templates/partials/group_shared_context_list.html").read_text(encoding="utf-8")
    group_shared_context_detail_html = Path("app/templates/partials/group_shared_context_detail.html").read_text(encoding="utf-8")
    group_task_board_html = Path("app/templates/partials/group_task_board.html").read_text(encoding="utf-8")
    login_html = Path("app/templates/login.html").read_text(encoding="utf-8")
    register_html = Path("app/templates/register.html").read_text(encoding="utf-8")

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
    assert "portal-panel-stack" in group_shared_context_list_html
    assert "portal-panel-stack" in group_shared_context_detail_html
    assert "portal-panel-stack" in group_task_board_html
    assert "portal-data-table" in group_shared_context_list_html
    assert "portal-data-table" in group_task_board_html
    assert "portal-summary-row" in group_task_board_html
    assert "portal-summary-chip" in group_task_board_html
    assert "portal-detail-stack" in group_shared_context_detail_html
    assert "portal-panel-pre" in group_shared_context_detail_html
    assert "text-blue-600" not in group_shared_context_list_html
    assert "border-gray-300" not in group_shared_context_list_html
    assert "border-gray-300" not in group_shared_context_detail_html
    assert "border-gray-300" not in group_task_board_html
    assert "space-y-3 text-sm" not in group_shared_context_list_html
    assert "space-y-3 text-sm" not in group_task_board_html
    assert "space-y-2 text-sm" not in group_shared_context_detail_html
    assert "portal-auth-copy" in login_html
    assert "portal-auth-copy" in register_html
    assert "portal-auth-footnote" in login_html
    assert "portal-auth-footnote" in register_html
    assert "portal-auth-link" in login_html
    assert "portal-auth-link" in register_html
    assert "portal-auth-error" in login_html
    assert "portal-auth-error" in register_html
    assert 'class="error"' not in login_html
    assert 'class="error"' not in register_html
    assert "muted tiny" not in login_html
    assert "muted tiny" not in register_html
    assert 'class="muted"' not in login_html
    assert 'class="muted"' not in register_html
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
    assert "onclick=" not in settings_html
    assert "alert(" not in settings_html
    assert 'data-settings-action="generate-ssh-key"' not in settings_html
    assert "ssh_private_key_path" not in settings_html
    assert "Generate RSA Key" not in settings_html
    assert "GitHub API Base URL" in settings_html
    assert 'placeholder="https://api.github.com"' in settings_html
    assert "Leave blank to use the public GitHub API default." in settings_html
    assert 'data-settings-action="copy-config"' not in settings_html
    assert 'data-settings-action="paste-config"' not in settings_html
    assert 'data-agent-id="{{ agent_id }}"' in settings_html
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
    assert '.portal-breadcrumb-link' in Path("app/static/css/app.css").read_text(encoding="utf-8")
    assert '.portal-breadcrumb-sep' in Path("app/static/css/app.css").read_text(encoding="utf-8")
    assert '.portal-auth-error' in Path("app/static/css/app.css").read_text(encoding="utf-8")
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


def test_frontend_assets_and_templates_are_syntax_valid():
    compile_result = subprocess.run(
        ["python", "-m", "compileall", "-q", "app", "tests"],
        capture_output=True,
        text=True,
    )
    assert compile_result.returncode == 0, compile_result.stderr

    if shutil.which("node"):
        node_result = subprocess.run(
            ["node", "--check", "app/static/js/chat_ui.js"],
            capture_output=True,
            text=True,
        )
        assert node_result.returncode == 0, node_result.stderr

    def data_attr(v):
        return '' if v is None else str(v).replace('&', '&amp;').replace('"', '&quot;').replace("'", '&#39;').replace('<', '&lt;').replace('>', '&gt;')

    env = Environment(loader=FileSystemLoader("app/templates"))
    env.filters["data_attr"] = data_attr
    for template_path in Path("app/templates").rglob("*.html"):
        rel_path = str(template_path.relative_to("app/templates"))
        env.get_template(rel_path)
