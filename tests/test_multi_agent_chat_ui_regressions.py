import json
import shutil
import subprocess
from pathlib import Path

import pytest

from _js_extract_helpers import _extract_js_function


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _chat_ui_js_source() -> str:
    chat_ui_path = _repo_root() / "app" / "static" / "js" / "chat_ui.js"
    return chat_ui_path.read_text(encoding="utf-8")


def _extract_render_chat_history_dependencies(js_source: str) -> str:
    format_attachment_meta_text = _extract_js_function(js_source, "formatAttachmentMetaText")
    render_history = _extract_js_function(js_source, "renderChatHistory")
    return f"{format_attachment_meta_text}\n{render_history}"


def test_chat_ui_includes_display_block_renderer_helpers():
    js_source = _chat_ui_js_source()
    assert "function parseDisplayBlocks(" in js_source
    assert "function renderDisplayBlocksToHtml(" in js_source
    assert "function renderSingleDisplayBlock(" in js_source
    assert "function renderCodeBlock(" in js_source
    assert "function renderTableBlock(" in js_source
    assert "function enhanceMarkdownBlock(" in js_source


def test_chat_ui_uses_live_thinking_panel_rendering():
    js_source = _chat_ui_js_source()
    css_source = Path("app/static/css/app.css").read_text(encoding="utf-8")
    assert "renderThinkingPanelFromClientState" in js_source
    assert "scheduleThinkingPanelRefresh" in js_source
    assert "loadPersistedThinkingPanel" in js_source
    assert "View Thinking Process" not in js_source
    assert "attachThinkingToLatestAssistant" not in js_source
    assert "renderThinkingProcess(" not in js_source
    assert "data-thinking-id" not in js_source
    assert ".portal-context-meter" in css_source
    assert ".portal-live-thinking" in css_source


def test_composer_model_selector_keeps_per_agent_model_override_isolated():
    node_bin = shutil.which("node")
    if not node_bin:
        pytest.skip("node is not installed; skipping JS helper behavior test")

    js_file = _chat_ui_js_source()
    create_state = _extract_js_function(js_file, "createDefaultChatState")
    ensure_state = _extract_js_function(js_file, "ensureChatState")
    render_selector = _extract_js_function(js_file, "renderComposerModelSelectorForAgent")

    script = f"""
const managedProviderModels = {{
  openai: [
    {{ value: "gpt-5-mini", label: "GPT-5 mini" }},
    {{ value: "gpt-5", label: "GPT-5" }},
  ],
  anthropic: [
    {{ value: "claude-sonnet-4-20250514", label: "Claude Sonnet 4" }},
    {{ value: "claude-haiku-4-20250514", label: "Claude Haiku 4" }},
  ],
}};
const state = {{ chatStatesByAgent: new Map(), agentSessionIds: new Map() }};
function makeClassList() {{
  const classes = new Set(["hidden"]);
  return {{
    add(name) {{ classes.add(name); }},
    remove(name) {{ classes.delete(name); }},
    contains(name) {{ return classes.has(name); }},
  }};
}}
function makeSelect() {{
  return {{
    options: [],
    _value: "",
    get value() {{ return this._value; }},
    set value(v) {{
      const exists = this.options.some((opt) => opt.value === v);
      this._value = exists ? v : "";
    }},
    set innerHTML(v) {{
      if (v === "") {{
        this.options = [];
        this._value = "";
      }}
    }},
    appendChild(opt) {{ this.options.push(opt); }},
  }};
}}
const dom = {{
  chatModelWrap: {{ classList: makeClassList() }},
  chatModelSelect: makeSelect(),
}};
const document = {{
  createElement(tag) {{
    return {{ tagName: tag, value: "", textContent: "" }};
  }}
}};
{create_state}
{ensure_state}
{render_selector}
const a = ensureChatState("agent-A");
a.profileProvider = "openai";
a.profileDefaultModel = "gpt-5-mini";
a.modelOverride = "gpt-5";
const b = ensureChatState("agent-B");
b.profileProvider = "anthropic";
b.profileDefaultModel = "claude-sonnet-4-20250514";
b.modelOverride = "claude-haiku-4-20250514";
renderComposerModelSelectorForAgent("agent-A");
const selectedA = dom.chatModelSelect.value;
renderComposerModelSelectorForAgent("agent-B");
const selectedB = dom.chatModelSelect.value;
renderComposerModelSelectorForAgent("agent-A");
const selectedA2 = dom.chatModelSelect.value;
console.log(JSON.stringify({{
  selectedA,
  selectedB,
  selectedA2,
  modelOverrideA: ensureChatState("agent-A").modelOverride,
  modelOverrideB: ensureChatState("agent-B").modelOverride,
  hidden: dom.chatModelWrap.classList.contains("hidden"),
}}));
"""
    completed = subprocess.run([node_bin, "-e", script], capture_output=True, text=True, check=True)
    data = json.loads(completed.stdout)
    assert data["selectedA"] == "gpt-5"
    assert data["selectedB"] == "claude-haiku-4-20250514"
    assert data["selectedA2"] == "gpt-5"
    assert data["modelOverrideA"] == "gpt-5"
    assert data["modelOverrideB"] == "claude-haiku-4-20250514"
    assert data["hidden"] is False


def test_composer_model_selector_appends_unknown_current_model_option():
    node_bin = shutil.which("node")
    if not node_bin:
        pytest.skip("node is not installed; skipping JS helper behavior test")

    js_file = _chat_ui_js_source()
    create_state = _extract_js_function(js_file, "createDefaultChatState")
    ensure_state = _extract_js_function(js_file, "ensureChatState")
    render_selector = _extract_js_function(js_file, "renderComposerModelSelectorForAgent")

    script = f"""
const managedProviderModels = {{
  openai: [{{ value: "gpt-5-mini", label: "GPT-5 mini" }}],
}};
const state = {{ chatStatesByAgent: new Map(), agentSessionIds: new Map() }};
function makeClassList() {{
  const classes = new Set(["hidden"]);
  return {{
    add(name) {{ classes.add(name); }},
    remove(name) {{ classes.delete(name); }},
    contains(name) {{ return classes.has(name); }},
  }};
}}
const dom = {{
  chatModelWrap: {{ classList: makeClassList() }},
  chatModelSelect: {{
    options: [],
    _value: "",
    get value() {{ return this._value; }},
    set value(v) {{
      const exists = this.options.some((opt) => opt.value === v);
      this._value = exists ? v : "";
    }},
    set innerHTML(v) {{
      if (v === "") {{
        this.options = [];
        this._value = "";
      }}
    }},
    appendChild(opt) {{ this.options.push(opt); }},
  }},
}};
const document = {{
  createElement(tag) {{
    return {{ tagName: tag, value: "", textContent: "" }};
  }}
}};
{create_state}
{ensure_state}
{render_selector}
const chatState = ensureChatState("agent-X");
chatState.profileProvider = "openai";
chatState.profileDefaultModel = "gpt-unknown-custom";
renderComposerModelSelectorForAgent("agent-X");
console.log(JSON.stringify({{
  selected: dom.chatModelSelect.value,
  options: dom.chatModelSelect.options.map((opt) => [opt.value, opt.textContent]),
  hidden: dom.chatModelWrap.classList.contains("hidden"),
}}));
"""
    completed = subprocess.run([node_bin, "-e", script], capture_output=True, text=True, check=True)
    data = json.loads(completed.stdout)
    assert data["selected"] == "gpt-unknown-custom"
    assert ["gpt-unknown-custom", "gpt-unknown-custom (Current)"] in data["options"]
    assert data["hidden"] is False


def test_refresh_composer_model_profile_ignores_stale_agent_response_for_dom_render():
    node_bin = shutil.which("node")
    if not node_bin:
        pytest.skip("node is not installed; skipping JS helper behavior test")

    js_file = _chat_ui_js_source()
    create_state = _extract_js_function(js_file, "createDefaultChatState")
    ensure_state = _extract_js_function(js_file, "ensureChatState")
    render_selector = _extract_js_function(js_file, "renderComposerModelSelectorForAgent")
    refresh_profile = _extract_js_function(js_file, "refreshComposerModelProfile")

    script = f"""
const managedProviderModels = {{
  openai: [
    {{ value: "gpt-5-mini", label: "GPT-5 mini" }},
    {{ value: "gpt-5", label: "GPT-5" }},
  ],
  anthropic: [
    {{ value: "claude-sonnet-4-20250514", label: "Claude Sonnet 4" }},
    {{ value: "claude-haiku-4-20250514", label: "Claude Haiku 4" }},
  ],
}};
const state = {{ selectedAgentId: "agent-A", chatStatesByAgent: new Map(), agentSessionIds: new Map() }};
function makeClassList() {{
  const classes = new Set(["hidden"]);
  return {{
    add(name) {{ classes.add(name); }},
    remove(name) {{ classes.delete(name); }},
    contains(name) {{ return classes.has(name); }},
  }};
}}
const dom = {{
  chatModelWrap: {{ classList: makeClassList() }},
  chatModelSelect: {{
    options: [],
    _value: "",
    get value() {{ return this._value; }},
    set value(v) {{
      const exists = this.options.some((opt) => opt.value === v);
      this._value = exists ? v : "";
    }},
    set innerHTML(v) {{
      if (v === "") {{
        this.options = [];
        this._value = "";
      }}
    }},
    appendChild(opt) {{ this.options.push(opt); }},
  }},
}};
const document = {{
  createElement(tag) {{
    return {{ tagName: tag, value: "", textContent: "" }};
  }}
}};
const pending = {{}};
function deferred() {{
  let resolve;
  const promise = new Promise((res) => {{ resolve = res; }});
  return {{ promise, resolve }};
}}
async function api(url) {{
  if (!pending[url]) pending[url] = deferred();
  return pending[url].promise;
}}
const console = {{ warn() {{}}, log: globalThis.console.log }};
{create_state}
{ensure_state}
{render_selector}
{refresh_profile}
(async () => {{
  const reqA = refreshComposerModelProfile("agent-A");
  state.selectedAgentId = "agent-B";
  const reqB = refreshComposerModelProfile("agent-B");
  pending["/api/agents/agent-B/chat-model-profile"].resolve({{
    provider: "anthropic",
    current_model: "claude-sonnet-4-20250514",
  }});
  await reqB;
  pending["/api/agents/agent-A/chat-model-profile"].resolve({{
    provider: "openai",
    current_model: "gpt-5-mini",
  }});
  await reqA;
  console.log(JSON.stringify({{
    selectedAgentId: state.selectedAgentId,
    domValue: dom.chatModelSelect.value,
    domOptions: dom.chatModelSelect.options.map((opt) => opt.value),
    providerA: ensureChatState("agent-A").profileProvider,
    providerB: ensureChatState("agent-B").profileProvider,
  }}));
}})();
"""
    completed = subprocess.run([node_bin, "-e", script], capture_output=True, text=True, check=True)
    data = json.loads(completed.stdout)
    assert data["selectedAgentId"] == "agent-B"
    assert data["domValue"] == "claude-sonnet-4-20250514"
    assert data["domOptions"] == ["claude-sonnet-4-20250514", "claude-haiku-4-20250514"]
    assert data["providerA"] == "openai"
    assert data["providerB"] == "anthropic"


def test_background_success_does_not_render_into_current_dom():
    node_bin = shutil.which("node")
    if not node_bin:
        pytest.skip("node is not installed; skipping JS helper behavior test")
    js_file = _chat_ui_js_source()
    create_state = _extract_js_function(js_file, "createDefaultChatState")
    ensure_state = _extract_js_function(js_file, "ensureChatState")
    update_session = _extract_js_function(js_file, "updateAgentSession")
    mark_unread = _extract_js_function(js_file, "markAgentUnread")
    merge_events = _extract_js_function(js_file, "mergeThinkingEvents")
    handle_success = _extract_js_function(js_file, "handleAgentChatSuccess")

    script = f"""
const state = {{
  selectedAgentId: "agent-B",
  mineAgents: [{{id: "agent-A", name: "Agent A"}}, {{id: "agent-B", name: "Agent B"}}],
  chatStatesByAgent: new Map(),
  agentSessionIds: new Map([["agent-B", "s-b"]]),
}};
const dom = {{ messageList: {{ insertAdjacentHTML() {{ throw new Error("should not append"); }} }} }};
function setLastSessionId() {{}}
function syncHiddenSessionInputFromState() {{}}
function ensureEventSocketForSelectedAgent() {{}}
function setChatSubmittingForAgent(agentId, active) {{ ensureChatState(agentId).isSubmitting = active; }}
function removeTemporaryAssistantRows() {{}}
function getLatestOptimisticUserArticle() {{ return null; }}
function buildAssistantMessageArticle() {{ return ""; }}
function attachThinkingToLatestAssistant() {{}}
function setChatStatus() {{}}
function renderMarkdown() {{}}
function decorateToolMessages() {{}}
function renderIcons() {{}}
function scrollToBottom() {{}}
let rendered = 0;
function renderAgentList() {{ rendered += 1; }}
function notifyAgentCompletion() {{}}
{create_state}
{ensure_state}
{update_session}
{mark_unread}
{merge_events}
{handle_success}
const aState = ensureChatState("agent-A");
aState.activeRequest = {{ clientRequestId: "req-a" }};
(async () => {{
  await handleAgentChatSuccess("agent-A", {{ clientRequestId: "req-a", sessionIdAtSend: "s-a" }}, {{ session_id: "s-a2", response: "ok" }});
  console.log(JSON.stringify({{
    unread: ensureChatState("agent-A").unreadCount,
    needsReload: ensureChatState("agent-A").needsReload,
    bSession: state.agentSessionIds.get("agent-B"),
    renderAgentListCalls: rendered,
    inflightThinking: ensureChatState("agent-A").inflightThinking,
    pendingThinkingEvents: ensureChatState("agent-A").pendingThinkingEvents
  }}));
}})();
"""
    completed = subprocess.run([node_bin, "-e", script], capture_output=True, text=True, check=True)
    data = json.loads(completed.stdout)
    assert data["unread"] == 1
    assert data["needsReload"] is True
    assert data["bSession"] == "s-b"
    assert data["renderAgentListCalls"] == 1
    assert data["inflightThinking"] is None
    assert data["pendingThinkingEvents"] is None


def test_selected_agent_hidden_success_notifies_and_merges_events():
    node_bin = shutil.which("node")
    if not node_bin:
        pytest.skip("node is not installed; skipping JS helper behavior test")

    js_file = _chat_ui_js_source()
    create_state = _extract_js_function(js_file, "createDefaultChatState")
    ensure_state = _extract_js_function(js_file, "ensureChatState")
    update_session = _extract_js_function(js_file, "updateAgentSession")
    set_submitting = _extract_js_function(js_file, "setChatSubmittingForAgent")
    merge_events = _extract_js_function(js_file, "mergeThinkingEvents")
    get_selected_assistant_display_name = _extract_js_function(js_file, "getSelectedAssistantDisplayName")
    handle_success = _extract_js_function(js_file, "handleAgentChatSuccess")

    script = f"""
const state = {{
  selectedAgentId: "agent-A",
  mineAgents: [{{id: "agent-A", name: "Agent A"}}],
  chatStatesByAgent: new Map(),
  agentSessionIds: new Map(),
}};
const dom = {{ messageList: {{ insertAdjacentHTML() {{}} }} }};
const document = {{ hidden: true }};
let notifyCalls = 0;
let editCalls = 0;
function setLastSessionId() {{}}
function syncHiddenSessionInputFromState() {{}}
function ensureEventSocketForSelectedAgent() {{}}
function removeTemporaryAssistantRows() {{}}
function getLatestOptimisticUserArticle() {{ return {{ dataset: {{ optimisticUser: "1" }} }}; }}
function buildAssistantMessageArticle() {{ return ""; }}
function setChatStatus() {{}}
function renderMarkdown() {{}}
function decorateToolMessages() {{}}
function renderIcons() {{}}
function scrollToBottom() {{}}
function addEditButtonsToMessages() {{ editCalls += 1; }}
function markAgentUnread() {{}}
function renderAgentList() {{}}
function notifyAgentCompletion() {{ notifyCalls += 1; }}
function loadSessionForAgent() {{ throw new Error("should not reload"); }}
{create_state}
{ensure_state}
{update_session}
{set_submitting}
{merge_events}
{get_selected_assistant_display_name}
{handle_success}
const chatState = ensureChatState("agent-A");
chatState.activeRequest = {{ clientRequestId: "req-a" }};
chatState.inflightThinking = {{ events: [{{type: "execution.started", request_id: "req-a", session_id: "s-a", data: {{ message: "ws" }} }}] }};
(async () => {{
  await handleAgentChatSuccess("agent-A", {{ clientRequestId: "req-a", sessionIdAtSend: "s-a" }}, {{
    session_id: "s-a2",
    response: "done",
    events: [{{type: "tool_result", request_id: "req-a", session_id: "s-a2", data: {{ message: "payload" }} }}]
  }});
  console.log(JSON.stringify({{
    notifyCalls,
    editCalls,
    mergedCount: (ensureChatState("agent-A").lastThinkingSnapshot?.events || []).length,
  }}));
}})();
"""
    completed = subprocess.run([node_bin, "-e", script], capture_output=True, text=True, check=True)
    data = json.loads(completed.stdout)
    assert data["notifyCalls"] == 1
    assert data["editCalls"] == 1
    assert data["mergedCount"] == 2


def test_has_meaningful_context_state_filters_empty_dicts():
    node_bin = shutil.which("node")
    if not node_bin:
        pytest.skip("node is not installed; skipping JS helper behavior test")

    js_file = _chat_ui_js_source()
    has_meaningful = _extract_js_function(js_file, "hasMeaningfulContextState")

    script = f"""
{has_meaningful}
const cases = {{
  empty: hasMeaningfulContextState({{}}),
  scalar: hasMeaningfulContextState({{ summary: "final" }}),
  emptyList: hasMeaningfulContextState({{ constraints: [""] }}),
  list: hasMeaningfulContextState({{ constraints: ["must preserve"] }}),
  budget: hasMeaningfulContextState({{ budget: {{ usage_percent: 42 }} }}),
}};
console.log(JSON.stringify(cases));
"""
    completed = subprocess.run([node_bin, "-e", script], capture_output=True, text=True, check=True)
    data = json.loads(completed.stdout)
    assert data == {
        "empty": False,
        "scalar": True,
        "emptyList": False,
        "list": True,
        "budget": True,
    }


def test_has_meaningful_context_contents_treats_budget_only_as_not_contents():
    node_bin = shutil.which("node")
    if not node_bin:
        pytest.skip("node is not installed; skipping JS helper behavior test")

    js_file = _chat_ui_js_source()
    has_meaningful_contents = _extract_js_function(js_file, "hasMeaningfulContextContents")

    script = f"""
{has_meaningful_contents}
const cases = {{
  empty: hasMeaningfulContextContents({{}}),
  budgetOnly: hasMeaningfulContextContents({{ budget: {{ usage_percent: 42 }} }}),
  summary: hasMeaningfulContextContents({{ summary: "final" }}),
  list: hasMeaningfulContextContents({{ open_loops: ["verify persisted panel"] }}),
}};
console.log(JSON.stringify(cases));
"""
    completed = subprocess.run([node_bin, "-e", script], capture_output=True, text=True, check=True)
    data = json.loads(completed.stdout)
    assert data == {
        "empty": False,
        "budgetOnly": False,
        "summary": True,
        "list": True,
    }


def test_extract_latest_context_state_from_events_prefers_latest_context_contents_over_newer_budget_only():
    node_bin = shutil.which("node")
    if not node_bin:
        pytest.skip("node is not installed; skipping JS helper behavior test")

    js_file = _chat_ui_js_source()
    get_runtime_event_data = _extract_js_function(js_file, "getRuntimeEventData")
    has_meaningful = _extract_js_function(js_file, "hasMeaningfulContextState")
    has_contents = _extract_js_function(js_file, "hasMeaningfulContextContents")
    latest_from_events = _extract_js_function(js_file, "extractLatestContextStateFromEvents")

    script = f"""
{get_runtime_event_data}
{has_meaningful}
{has_contents}
{latest_from_events}

const result = extractLatestContextStateFromEvents([
  {{
    type: "context_snapshot",
    data: {{
      context_state: {{
        summary: "Real final summary",
        next_step: "Keep this",
        budget: {{ usage_percent: 33 }},
      }},
    }},
  }},
  {{
    type: "context_snapshot",
    data: {{
      context_state: {{
        budget: {{ usage_percent: 44 }},
      }},
    }},
  }},
]);

console.log(JSON.stringify(result));
"""
    completed = subprocess.run([node_bin, "-e", script], capture_output=True, text=True, check=True)
    data = json.loads(completed.stdout)
    assert data["summary"] == "Real final summary"
    assert data["next_step"] == "Keep this"
    assert data["budget"]["usage_percent"] == 33


def test_update_thinking_context_from_event_preserves_existing_contents_when_incoming_is_budget_only():
    node_bin = shutil.which("node")
    if not node_bin:
        pytest.skip("node is not installed; skipping JS helper behavior test")

    js_file = _chat_ui_js_source()
    extract_context_budget = _extract_js_function(js_file, "extractContextBudget")
    has_meaningful = _extract_js_function(js_file, "hasMeaningfulContextState")
    has_contents = _extract_js_function(js_file, "hasMeaningfulContextContents")
    update_context = _extract_js_function(js_file, "updateThinkingContextFromEvent")

    script = f"""
{extract_context_budget}
{has_meaningful}
{has_contents}
{update_context}

const thinking = {{
  contextState: {{
    summary: "Existing live summary",
    next_step: "Existing next step",
    budget: {{ usage_percent: 20 }},
  }},
  contextBudget: {{ usage_percent: 20 }},
}};

updateThinkingContextFromEvent(thinking, {{
  type: "context_snapshot",
  data: {{
    context_state: {{
      budget: {{ usage_percent: 55 }},
    }},
  }},
}});

console.log(JSON.stringify(thinking));
"""
    completed = subprocess.run([node_bin, "-e", script], capture_output=True, text=True, check=True)
    data = json.loads(completed.stdout)
    assert data["contextState"]["summary"] == "Existing live summary"
    assert data["contextState"]["next_step"] == "Existing next step"
    assert data["contextBudget"]["usage_percent"] == 55


def test_handle_agent_chat_success_marks_merged_event_context_as_final_response():
    node_bin = shutil.which("node")
    if not node_bin:
        pytest.skip("node is not installed; skipping JS helper behavior test")

    js_file = _chat_ui_js_source()
    create_state = _extract_js_function(js_file, "createDefaultChatState")
    ensure_state = _extract_js_function(js_file, "ensureChatState")
    update_session = _extract_js_function(js_file, "updateAgentSession")
    set_submitting = _extract_js_function(js_file, "setChatSubmittingForAgent")
    merge_events = _extract_js_function(js_file, "mergeThinkingEvents")
    extract_context_budget = _extract_js_function(js_file, "extractContextBudget")
    has_meaningful = _extract_js_function(js_file, "hasMeaningfulContextState")
    has_contents = _extract_js_function(js_file, "hasMeaningfulContextContents")
    pick_meaningful = _extract_js_function(js_file, "pickMeaningfulContextState")
    get_runtime_event_data = _extract_js_function(js_file, "getRuntimeEventData")
    latest_from_events = _extract_js_function(js_file, "extractLatestContextStateFromEvents")
    handle_success = _extract_js_function(js_file, "handleAgentChatSuccess")

    script = f"""
const state = {{
  selectedAgentId: "agent-A",
  mineAgents: [{{id: "agent-A", name: "Agent A"}}],
  chatStatesByAgent: new Map(),
  agentSessionIds: new Map(),
}};
const dom = {{ messageList: {{ insertAdjacentHTML() {{}} }} }};
const document = {{ hidden: false }};
function setLastSessionId() {{}}
function syncHiddenSessionInputFromState() {{}}
function ensureEventSocketForSelectedAgent() {{}}
function removeTemporaryAssistantRows() {{}}
function getLatestOptimisticUserArticle() {{ return {{ dataset: {{ optimisticUser: "1" }} }}; }}
function buildAssistantMessageArticle() {{ return ""; }}
function getSelectedAssistantDisplayName() {{ return "Agent A"; }}
function setChatStatus() {{}}
function renderMarkdown() {{}}
function decorateToolMessages() {{}}
function renderIcons() {{}}
function scrollToBottom() {{}}
function addEditButtonsToMessages() {{}}
function markAgentUnread() {{}}
function renderAgentList() {{}}
function notifyAgentCompletion() {{}}
async function loadSessionForAgent() {{}}
{create_state}
{ensure_state}
{update_session}
{set_submitting}
{merge_events}
{extract_context_budget}
{has_meaningful}
{has_contents}
{pick_meaningful}
{get_runtime_event_data}
{latest_from_events}
{handle_success}
const chatState = ensureChatState("agent-A");
chatState.activeRequest = {{ clientRequestId: "req-a" }};
chatState.inflightThinking = {{
  events: [{{ type: "context_snapshot", request_id: "req-a", session_id: "s-a", data: {{ context_state: {{ summary: "Live summary" }} }} }}],
  contextState: {{ summary: "Live summary", next_step: "Keep me" }},
  contextBudget: {{ usage_percent: 11 }},
}};
(async () => {{
  await handleAgentChatSuccess("agent-A", {{ clientRequestId: "req-a", sessionIdAtSend: "s-a" }}, {{
    session_id: "s-a",
    request_id: "req-a",
    response: "done",
    context_state: {{}},
    runtime_events: [],
  }});
  console.log(JSON.stringify({{
    summary: ensureChatState("agent-A").lastThinkingSnapshot.contextState.summary,
    contextSource: ensureChatState("agent-A").lastThinkingSnapshot.contextSource,
  }}));
}})();
"""
    completed = subprocess.run([node_bin, "-e", script], capture_output=True, text=True, check=True)
    data = json.loads(completed.stdout)
    assert data["summary"] == "Live summary"
    assert data["contextSource"] == "final_response"


def test_handle_agent_chat_success_prefers_event_context_contents_over_budget_only_payload_context():
    node_bin = shutil.which("node")
    if not node_bin:
        pytest.skip("node is not installed; skipping JS helper behavior test")

    js_file = _chat_ui_js_source()
    create_state = _extract_js_function(js_file, "createDefaultChatState")
    ensure_state = _extract_js_function(js_file, "ensureChatState")
    update_session = _extract_js_function(js_file, "updateAgentSession")
    set_submitting = _extract_js_function(js_file, "setChatSubmittingForAgent")
    merge_events = _extract_js_function(js_file, "mergeThinkingEvents")
    extract_context_budget = _extract_js_function(js_file, "extractContextBudget")
    has_meaningful = _extract_js_function(js_file, "hasMeaningfulContextState")
    has_contents = _extract_js_function(js_file, "hasMeaningfulContextContents")
    get_runtime_event_data = _extract_js_function(js_file, "getRuntimeEventData")
    latest_from_events = _extract_js_function(js_file, "extractLatestContextStateFromEvents")
    pick_context_contents_first = _extract_js_function(js_file, "pickContextStateWithContentsFirst")
    pick_context_budget = _extract_js_function(js_file, "pickContextBudget")
    handle_success = _extract_js_function(js_file, "handleAgentChatSuccess")

    script = f"""
const state = {{
  selectedAgentId: "agent-A",
  mineAgents: [{{id: "agent-A", name: "Agent A"}}],
  chatStatesByAgent: new Map(),
  agentSessionIds: new Map(),
}};
const dom = {{ messageList: {{ insertAdjacentHTML() {{}} }} }};
const document = {{ hidden: false }};
function setLastSessionId() {{}}
function syncHiddenSessionInputFromState() {{}}
function ensureEventSocketForSelectedAgent() {{}}
function removeTemporaryAssistantRows() {{}}
function getLatestOptimisticUserArticle() {{ return {{ dataset: {{ optimisticUser: "1" }} }}; }}
function buildAssistantMessageArticle() {{ return ""; }}
function getSelectedAssistantDisplayName() {{ return "Agent A"; }}
function setChatStatus() {{}}
function renderMarkdown() {{}}
function decorateToolMessages() {{}}
function renderIcons() {{}}
function scrollToBottom() {{}}
function addEditButtonsToMessages() {{}}
function markAgentUnread() {{}}
function renderAgentList() {{}}
function notifyAgentCompletion() {{}}
async function loadSessionForAgent() {{}}
{create_state}
{ensure_state}
{update_session}
{set_submitting}
{merge_events}
{extract_context_budget}
{has_meaningful}
{has_contents}
{get_runtime_event_data}
{latest_from_events}
{pick_context_contents_first}
{pick_context_budget}
{handle_success}
const chatState = ensureChatState("agent-A");
chatState.activeRequest = {{ clientRequestId: "req-a" }};
chatState.inflightThinking = {{ events: [], contextState: null, contextBudget: {{ usage_percent: 7 }} }};
(async () => {{
  await handleAgentChatSuccess("agent-A", {{ clientRequestId: "req-a", sessionIdAtSend: "s-a" }}, {{
    session_id: "s-a",
    request_id: "req-a",
    response: "done",
    context_state: {{ budget: {{ usage_percent: 11 }} }},
    runtime_events: [
      {{
        type: "context_snapshot",
        event_type: "context_snapshot",
        data: {{
          stage: "post_turn",
          terminal: true,
          context_state: {{
            summary: "Final event summary",
            next_step: "Keep final context",
            budget: {{ usage_percent: 33 }}
          }}
        }}
      }}
    ],
  }});
  const snapshot = ensureChatState("agent-A").lastThinkingSnapshot;
  console.log(JSON.stringify({{
    summary: snapshot.contextState.summary,
    nextStep: snapshot.contextState.next_step,
    budgetUsagePercent: snapshot.contextBudget.usage_percent,
    contextSource: snapshot.contextSource,
  }}));
}})();
"""
    completed = subprocess.run([node_bin, "-e", script], capture_output=True, text=True, check=True)
    data = json.loads(completed.stdout)
    assert data["summary"] == "Final event summary"
    assert data["nextStep"] == "Keep final context"
    assert data["budgetUsagePercent"] == 33
    assert data["contextSource"] == "final_response"


def test_handle_agent_chat_success_renders_final_snapshot_when_panel_is_open():
    node_bin = shutil.which("node")
    if not node_bin:
        pytest.skip("node is not installed; skipping JS helper behavior test")

    js_file = _chat_ui_js_source()
    create_state = _extract_js_function(js_file, "createDefaultChatState")
    ensure_state = _extract_js_function(js_file, "ensureChatState")
    update_session = _extract_js_function(js_file, "updateAgentSession")
    set_submitting = _extract_js_function(js_file, "setChatSubmittingForAgent")
    merge_events = _extract_js_function(js_file, "mergeThinkingEvents")
    extract_context_budget = _extract_js_function(js_file, "extractContextBudget")
    has_meaningful = _extract_js_function(js_file, "hasMeaningfulContextState")
    has_contents = _extract_js_function(js_file, "hasMeaningfulContextContents")
    get_runtime_event_data = _extract_js_function(js_file, "getRuntimeEventData")
    latest_from_events = _extract_js_function(js_file, "extractLatestContextStateFromEvents")
    pick_context_contents_first = _extract_js_function(js_file, "pickContextStateWithContentsFirst")
    pick_context_budget = _extract_js_function(js_file, "pickContextBudget")
    get_active_snapshot = _extract_js_function(js_file, "getActiveThinkingSnapshot")
    handle_success = _extract_js_function(js_file, "handleAgentChatSuccess")

    script = f"""
const state = {{
  selectedAgentId: "agent-A",
  mineAgents: [{{id: "agent-A", name: "Agent A"}}],
  chatStatesByAgent: new Map(),
  agentSessionIds: new Map(),
}};
let renderedSnapshot = null;
const dom = {{
  messageList: {{ insertAdjacentHTML() {{}} }},
}};
const document = {{ hidden: false }};
function isThinkingPanelActiveForAgent(agentId) {{ return agentId === "agent-A"; }}
function renderThinkingPanelFromClientState(chatState) {{
  const snapshot = getActiveThinkingSnapshot(chatState);
  renderedSnapshot = {{
    summary: snapshot?.contextState?.summary || "",
    nextStep: snapshot?.contextState?.next_step || "",
    completed: snapshot?.completed === true,
    contextSource: snapshot?.contextSource || "",
  }};
}}
async function loadPersistedThinkingPanel() {{ return false; }}
function setLastSessionId() {{}}
function syncHiddenSessionInputFromState() {{}}
function ensureEventSocketForSelectedAgent() {{}}
function removeTemporaryAssistantRows() {{}}
function getLatestOptimisticUserArticle() {{ return {{ dataset: {{ optimisticUser: "1" }} }}; }}
function buildAssistantMessageArticle() {{ return ""; }}
function getSelectedAssistantDisplayName() {{ return "Agent A"; }}
function setChatStatus() {{}}
function renderMarkdown() {{}}
function decorateToolMessages() {{}}
function renderIcons() {{}}
function scrollToBottom() {{}}
function addEditButtonsToMessages() {{}}
function markAgentUnread() {{}}
function renderAgentList() {{}}
function notifyAgentCompletion() {{}}
async function loadSessionForAgent() {{}}
{create_state}
{ensure_state}
{update_session}
{set_submitting}
{merge_events}
{extract_context_budget}
{has_meaningful}
{has_contents}
{get_runtime_event_data}
{latest_from_events}
{pick_context_contents_first}
{pick_context_budget}
{get_active_snapshot}
{handle_success}
const chatState = ensureChatState("agent-A");
chatState.activeRequest = {{ clientRequestId: "req-a" }};
chatState.inflightThinking = {{
  requestId: "req-a",
  sessionId: "s-a",
  completed: false,
  events: [],
  contextState: {{ summary: "Stale live summary", next_step: "Still running" }},
  contextBudget: {{ usage_percent: 11 }},
}};
(async () => {{
  await handleAgentChatSuccess(
    "agent-A",
    {{ clientRequestId: "req-a", sessionIdAtSend: "s-a" }},
    {{
      session_id: "s-a",
      request_id: "req-a",
      response: "done",
      context_state: {{
        summary: "Final payload summary",
        next_step: "Final next step",
        budget: {{ usage_percent: 22 }},
      }},
      runtime_events: [],
    }}
  );
  console.log(JSON.stringify({{
    rendered: renderedSnapshot,
    inflightIsNull: ensureChatState("agent-A").inflightThinking === null,
    lastSummary: ensureChatState("agent-A").lastThinkingSnapshot?.contextState?.summary || "",
  }}));
}})();
"""
    completed = subprocess.run([node_bin, "-e", script], capture_output=True, text=True, check=True)
    data = json.loads(completed.stdout)
    assert data["rendered"]["summary"] == "Final payload summary"
    assert data["rendered"]["nextStep"] == "Final next step"
    assert data["rendered"]["completed"] is True
    assert data["rendered"]["contextSource"] == "final_response"
    assert data["inflightIsNull"] is True
    assert data["lastSummary"] == "Final payload summary"


def test_handle_agent_event_message_merges_late_event_for_completed_request_into_last_snapshot():
    node_bin = shutil.which("node")
    if not node_bin:
        pytest.skip("node is not installed; skipping JS helper behavior test")

    js_file = _chat_ui_js_source()
    create_state = _extract_js_function(js_file, "createDefaultChatState")
    ensure_state = _extract_js_function(js_file, "ensureChatState")
    normalize_event = _extract_js_function(js_file, "normalizeRuntimeEvent")
    completion_state = _extract_js_function(js_file, "isCompletionRuntimeState")
    is_trackable = _extract_js_function(js_file, "isTrackableThinkingEvent")
    merge_events = _extract_js_function(js_file, "mergeThinkingEvents")
    extract_context_budget = _extract_js_function(js_file, "extractContextBudget")
    has_meaningful = _extract_js_function(js_file, "hasMeaningfulContextState")
    has_contents = _extract_js_function(js_file, "hasMeaningfulContextContents")
    update_context = _extract_js_function(js_file, "updateThinkingContextFromEvent")
    handle_event = _extract_js_function(js_file, "handleAgentEventMessage")

    script = f"""
const state = {{
  selectedAgentId: "agent-A",
  chatStatesByAgent: new Map(),
  agentSessionIds: new Map(),
}};
const COMPLETION_RUNTIME_STATES = new Set(["completed", "success", "failed", "error", "cancelled", "canceled"]);
const dom = {{ chatSessionId: {{ value: "" }} }};
function isThinkingPanelActiveForAgent() {{ return false; }}
function scheduleThinkingPanelRefresh() {{}}
{create_state}
{ensure_state}
{normalize_event}
{completion_state}
{is_trackable}
{merge_events}
{extract_context_budget}
{has_meaningful}
{has_contents}
{update_context}
{handle_event}
const chatState = ensureChatState("agent-A");
chatState.activeRequest = null;
chatState.lastCompletedRequestId = "req-a";
chatState.lastThinkingSnapshot = {{
  requestId: "req-a",
  sessionId: "s-a",
  completed: true,
  events: [],
  contextState: {{
    summary: "Existing final summary",
    next_step: "Existing next step",
  }},
  contextBudget: {{ usage_percent: 10 }},
}};
handleAgentEventMessage(JSON.stringify({{
  type: "context_snapshot",
  event_type: "context_snapshot",
  request_id: "req-a",
  session_id: "s-a",
  data: {{
    stage: "post_turn",
    terminal: true,
    context_state: {{
      summary: "Late final summary",
      next_step: "Late next step",
      budget: {{ usage_percent: 44 }},
    }},
  }},
}}), {{ agentId: "agent-A", sessionId: "s-a" }});
console.log(JSON.stringify({{
  inflightIsNull: chatState.inflightThinking === null,
  eventCount: chatState.lastThinkingSnapshot.events.length,
  summary: chatState.lastThinkingSnapshot.contextState.summary,
  nextStep: chatState.lastThinkingSnapshot.contextState.next_step,
  budgetUsage: chatState.lastThinkingSnapshot.contextBudget.usage_percent,
}}));
"""
    completed = subprocess.run([node_bin, "-e", script], capture_output=True, text=True, check=True)
    data = json.loads(completed.stdout)
    assert data["inflightIsNull"] is True
    assert data["eventCount"] == 1
    assert data["summary"] == "Late final summary"
    assert data["nextStep"] == "Late next step"
    assert data["budgetUsage"] == 44


def test_load_persisted_thinking_panel_preserves_local_context_when_persisted_has_no_context():
    node_bin = shutil.which("node")
    if not node_bin:
        pytest.skip("node is not installed; skipping JS helper behavior test")

    js_file = _chat_ui_js_source()
    create_state = _extract_js_function(js_file, "createDefaultChatState")
    ensure_state = _extract_js_function(js_file, "ensureChatState")
    get_active_snapshot = _extract_js_function(js_file, "getActiveThinkingSnapshot")
    has_meaningful_contents = _extract_js_function(js_file, "hasMeaningfulContextContents")
    load_persisted = _extract_js_function(js_file, "loadPersistedThinkingPanel")

    script = f"""
const state = {{
  selectedAgentId: "agent-A",
  chatStatesByAgent: new Map(),
  agentSessionIds: new Map(),
}};
const dom = {{ toolPanelBody: {{ innerHTML: "<div>LOCAL SNAPSHOT</div>" }} }};
const document = {{
  createElement(tag) {{
    if (tag !== "template") return {{}};
    return {{
      _html: "",
      set innerHTML(value) {{ this._html = value; }},
      get content() {{
        const html = this._html || "";
        const extract = (name) => {{
          const pattern = new RegExp(`${{name}}="([^"]*)"`);
          const match = html.match(pattern);
          return match ? match[1] : undefined;
        }};
        return {{
          querySelector(selector) {{
            if (selector !== "[data-thinking-panel-root]") return null;
            if (!html.includes("data-thinking-panel-root")) return null;
            return {{
              dataset: {{
                thinkingHasData: extract("data-thinking-has-data"),
                thinkingHasContext: extract("data-thinking-has-context"),
                thinkingRequestId: extract("data-thinking-request-id"),
              }},
            }};
          }},
        }};
      }},
    }};
  }},
}};
function renderIcons() {{}}
function setToolPanel() {{}}
{create_state}
{ensure_state}
{get_active_snapshot}
{has_meaningful_contents}
{load_persisted}
const chatState = ensureChatState("agent-A");
chatState.lastThinkingSnapshot = {{
  completed: true,
  requestId: "req-a",
  sessionId: "s-a",
  contextState: {{ summary: "Local final context" }},
  events: [],
}};
let first = true;
global.fetch = async () => ({{
  ok: true,
  text: async () => first
    ? `<div data-thinking-panel-root="1" data-thinking-has-data="1" data-thinking-has-context="0" data-thinking-request-id="req-a"><div>No context snapshot was captured for this run.</div></div>`
    : `<div data-thinking-panel-root="1" data-thinking-has-data="1" data-thinking-has-context="1" data-thinking-request-id="req-a"><div>Persisted Context</div></div>`,
}});
(async () => {{
  const before = dom.toolPanelBody.innerHTML;
  const changedFirst = await loadPersistedThinkingPanel("s-a", {{
    preserveLiveOnFailure: true,
    preserveLiveIfEmpty: true,
    preserveLiveIfNoContext: true,
    expectedRequestId: "req-a",
  }});
  const afterFirst = dom.toolPanelBody.innerHTML;
  first = false;
  const changedSecond = await loadPersistedThinkingPanel("s-a", {{
    preserveLiveOnFailure: true,
    preserveLiveIfEmpty: true,
    preserveLiveIfNoContext: true,
    expectedRequestId: "req-a",
  }});
  const afterSecond = dom.toolPanelBody.innerHTML;
  console.log(JSON.stringify({{
    changedFirst,
    before,
    afterFirst,
    changedSecond,
    afterSecond,
  }}));
}})();
"""
    completed = subprocess.run([node_bin, "-e", script], capture_output=True, text=True, check=True)
    data = json.loads(completed.stdout)
    assert data["changedFirst"] is False
    assert data["before"] == data["afterFirst"]
    assert data["changedSecond"] is True
    assert "Persisted Context" in data["afterSecond"]


def test_success_without_optimistic_row_reloads_session():
    node_bin = shutil.which("node")
    if not node_bin:
        pytest.skip("node is not installed; skipping JS helper behavior test")

    js_file = _chat_ui_js_source()
    create_state = _extract_js_function(js_file, "createDefaultChatState")
    ensure_state = _extract_js_function(js_file, "ensureChatState")
    update_session = _extract_js_function(js_file, "updateAgentSession")
    set_submitting = _extract_js_function(js_file, "setChatSubmittingForAgent")
    merge_events = _extract_js_function(js_file, "mergeThinkingEvents")
    handle_success = _extract_js_function(js_file, "handleAgentChatSuccess")

    script = f"""
const state = {{
  selectedAgentId: "agent-A",
  mineAgents: [{{id: "agent-A", name: "Agent A"}}],
  chatStatesByAgent: new Map(),
  agentSessionIds: new Map(),
}};
const dom = {{ messageList: {{ insertAdjacentHTML() {{ throw new Error("must not append"); }} }} }};
const document = {{ hidden: false }};
let reloadCalls = [];
function setLastSessionId() {{}}
function syncHiddenSessionInputFromState() {{}}
function ensureEventSocketForSelectedAgent() {{}}
function removeTemporaryAssistantRows() {{}}
function getLatestOptimisticUserArticle() {{ return null; }}
function attachThinkingToLatestAssistant() {{}}
function setChatStatus() {{}}
function renderMarkdown() {{}}
function decorateToolMessages() {{}}
function renderIcons() {{}}
function scrollToBottom() {{}}
function addEditButtonsToMessages() {{}}
function markAgentUnread() {{}}
function renderAgentList() {{}}
function notifyAgentCompletion() {{}}
async function loadSessionForAgent(agentId, sessionId, options) {{ reloadCalls.push([agentId, sessionId, options?.render]); }}
{create_state}
{ensure_state}
{update_session}
{set_submitting}
{merge_events}
{handle_success}
const chatState = ensureChatState("agent-A");
chatState.activeRequest = {{ clientRequestId: "req-a" }};
(async () => {{
  await handleAgentChatSuccess("agent-A", {{ clientRequestId: "req-a", sessionIdAtSend: "s-a" }}, {{ session_id: "s-a2", response: "done" }});
  console.log(JSON.stringify({{ reloadCalls }}));
}})();
"""
    completed = subprocess.run([node_bin, "-e", script], capture_output=True, text=True, check=True)
    data = json.loads(completed.stdout)
    assert data["reloadCalls"] == [["agent-A", "s-a2", True]]


def test_failure_restores_hidden_attachments_and_hidden_tab_notifies():
    node_bin = shutil.which("node")
    if not node_bin:
        pytest.skip("node is not installed; skipping JS helper behavior test")

    js_file = _chat_ui_js_source()
    create_state = _extract_js_function(js_file, "createDefaultChatState")
    ensure_state = _extract_js_function(js_file, "ensureChatState")
    set_submitting = _extract_js_function(js_file, "setChatSubmittingForAgent")
    handle_failure = _extract_js_function(js_file, "handleAgentChatFailure")

    script = f"""
const state = {{
  selectedAgentId: "agent-A",
  mineAgents: [{{id: "agent-A", name: "Agent A"}}],
  chatStatesByAgent: new Map(),
  agentSessionIds: new Map(),
}};
const dom = {{
  chatInput: {{ value: "" }},
  messageList: {{ insertAdjacentHTML() {{}} }},
}};
const attachmentNode = {{ value: "" }};
const document = {{
  hidden: true,
  getElementById(id) {{ return id === "chat-attachments" ? attachmentNode : null; }},
}};
let notifyCalls = 0;
function removeTemporaryAssistantRows() {{}}
function removeLatestOptimisticUserRow() {{}}
function renderInputPreview() {{}}
function syncChatInputHeight() {{}}
function setChatStatus() {{}}
function safe(v) {{ return String(v); }}
function scrollToBottom() {{}}
function renderIcons() {{}}
function markAgentUnread() {{}}
function renderAgentList() {{}}
function notifyAgentCompletion() {{ notifyCalls += 1; }}
{create_state}
{ensure_state}
{set_submitting}
{handle_failure}
const chatState = ensureChatState("agent-A");
chatState.activeRequest = {{ clientRequestId: "req-a" }};
handleAgentChatFailure("agent-A", {{
  clientRequestId: "req-a",
  attachments: ["file-1", "file-2"],
  backupFiles: [],
  backupMessage: "msg"
}}, new Error("boom"));
console.log(JSON.stringify({{
  attachmentsValue: attachmentNode.value,
  draftAttachmentsValue: ensureChatState("agent-A").draftAttachmentsValue,
  notifyCalls
}}));
"""
    completed = subprocess.run([node_bin, "-e", script], capture_output=True, text=True, check=True)
    data = json.loads(completed.stdout)
    assert data["attachmentsValue"] == '["file-1","file-2"]'
    assert data["draftAttachmentsValue"] == '["file-1","file-2"]'
    assert data["notifyCalls"] == 1


def test_background_failure_restores_original_agent_draft_state_only():
    node_bin = shutil.which("node")
    if not node_bin:
        pytest.skip("node is not installed; skipping JS helper behavior test")

    js_file = _chat_ui_js_source()
    create_state = _extract_js_function(js_file, "createDefaultChatState")
    ensure_state = _extract_js_function(js_file, "ensureChatState")
    set_submitting = _extract_js_function(js_file, "setChatSubmittingForAgent")
    mark_unread = _extract_js_function(js_file, "markAgentUnread")
    handle_failure = _extract_js_function(js_file, "handleAgentChatFailure")

    script = f"""
const state = {{
  selectedAgentId: "agent-B",
  mineAgents: [{{id: "agent-A", name: "Agent A"}}, {{id: "agent-B", name: "Agent B"}}],
  chatStatesByAgent: new Map(),
  agentSessionIds: new Map(),
}};
const dom = {{
  chatInput: {{ value: "" }},
  messageList: {{ insertAdjacentHTML() {{ throw new Error("must not touch current DOM"); }} }},
}};
const document = {{
  hidden: false,
  getElementById() {{ throw new Error("must not read selected DOM attachments in background branch"); }},
}};
let renderCalls = 0;
function removeTemporaryAssistantRows() {{}}
function removeLatestOptimisticUserRow() {{}}
function renderInputPreview() {{}}
function syncChatInputHeight() {{}}
function setChatStatus() {{}}
function safe(v) {{ return String(v); }}
function scrollToBottom() {{}}
function renderIcons() {{}}
function markAgentUnread(agentId, status) {{
  const chatState = ensureChatState(agentId);
  chatState.unreadCount += 1;
  chatState.backgroundStatus = status;
}}
function renderAgentList() {{ renderCalls += 1; }}
function notifyAgentCompletion() {{}}
{create_state}
{ensure_state}
{set_submitting}
{mark_unread}
{handle_failure}
const chatStateA = ensureChatState("agent-A");
chatStateA.activeRequest = {{ clientRequestId: "req-a" }};
chatStateA.attachmentHistory = [["old-1"], ["new-failed"]];
chatStateA.didAppendAttachmentHistoryForPendingSend = true;
handleAgentChatFailure("agent-A", {{
  clientRequestId: "req-a",
  backupMessage: "fix this",
  backupFiles: [{{id: "pf-1"}}],
  attachments: ["file-1", "file-2"],
}}, new Error("failed"));
console.log(JSON.stringify({{
  draftText: ensureChatState("agent-A").draftText,
  draftAttachmentsValue: ensureChatState("agent-A").draftAttachmentsValue,
  pendingFilesLen: ensureChatState("agent-A").pendingFiles.length,
  attachmentHistory: ensureChatState("agent-A").attachmentHistory,
  backgroundStatus: ensureChatState("agent-A").backgroundStatus,
  needsReload: ensureChatState("agent-A").needsReload,
  renderCalls
}}));
"""
    completed = subprocess.run([node_bin, "-e", script], capture_output=True, text=True, check=True)
    data = json.loads(completed.stdout)
    assert data["draftText"] == "fix this"
    assert data["draftAttachmentsValue"] == '["file-1","file-2"]'
    assert data["pendingFilesLen"] == 1
    assert data["attachmentHistory"] == [["old-1"]]
    assert data["backgroundStatus"] == "error"
    assert data["needsReload"] is False
    assert data["renderCalls"] == 1


def test_render_chat_history_rebuilds_attachment_history_for_selected_agent():
    node_bin = shutil.which("node")
    if not node_bin:
        pytest.skip("node is not installed; skipping JS helper behavior test")

    js_file = _chat_ui_js_source()
    get_non_blank_author_name = _extract_js_function(js_file, "getNonBlankAuthorName")
    get_current_user_display_name = _extract_js_function(js_file, "getCurrentUserDisplayName")
    get_selected_assistant_display_name = _extract_js_function(js_file, "getSelectedAssistantDisplayName")
    get_history_message_display_name = _extract_js_function(js_file, "getHistoryMessageDisplayName")
    render_history_dependencies = _extract_render_chat_history_dependencies(js_file)

    script = f"""
const state = {{
  selectedAgentId: "agent-A",
  selectedAgentName: "Agent A",
  chatStatesByAgent: new Map([["agent-A", {{ attachmentHistory: [["dirty-old"]] }}]]),
}};
const dom = {{
  messageList: {{
    innerHTML: "",
    appendChild() {{}},
  }},
}};
function getChatState() {{ return state.chatStatesByAgent.get("agent-A"); }}
function clearMessageListToWelcome() {{ dom.messageList.innerHTML = "WELCOME"; }}
function renderMarkdown() {{}}
function decorateToolMessages() {{}}
function attachThinkingToLatestAssistant() {{}}
function scrollToBottom() {{}}
const document = {{
  createElement(tag) {{
    return {{
      tag,
      className: "",
      dataset: {{}},
      textContent: "",
      appendChild() {{}},
    }};
  }},
}};
{get_non_blank_author_name}
{get_current_user_display_name}
{get_selected_assistant_display_name}
{get_history_message_display_name}
{render_history_dependencies}
renderChatHistory([
  {{ role: "user", content: "u1", attachments: ["file-1"] }},
  {{ role: "assistant", content: "a1" }},
  {{ role: "user", content: "u2", attachments: [] }},
], {{}});
console.log(JSON.stringify({{
  attachmentHistory: state.chatStatesByAgent.get("agent-A").attachmentHistory
}}));
"""
    completed = subprocess.run([node_bin, "-e", script], capture_output=True, text=True, check=True)
    data = json.loads(completed.stdout)
    assert data["attachmentHistory"] == [["file-1"], []]


def test_render_chat_history_empty_clears_attachment_history():
    node_bin = shutil.which("node")
    if not node_bin:
        pytest.skip("node is not installed; skipping JS helper behavior test")

    js_file = _chat_ui_js_source()
    render_history_dependencies = _extract_render_chat_history_dependencies(js_file)

    script = f"""
const state = {{
  selectedAgentId: "agent-A",
  selectedAgentName: "Agent A",
  chatStatesByAgent: new Map([["agent-A", {{ attachmentHistory: [["dirty-old"]] }}]]),
}};
const dom = {{
  messageList: {{
    innerHTML: "",
    appendChild() {{}},
  }},
}};
function getChatState() {{ return state.chatStatesByAgent.get("agent-A"); }}
function clearMessageListToWelcome() {{ dom.messageList.innerHTML = "WELCOME"; }}
function renderMarkdown() {{}}
function decorateToolMessages() {{}}
function attachThinkingToLatestAssistant() {{}}
function scrollToBottom() {{}}
const document = {{
  createElement() {{
    return {{ className: "", dataset: {{}}, textContent: "", appendChild() {{}} }};
  }},
}};
{render_history_dependencies}
renderChatHistory([], {{}});
console.log(JSON.stringify({{
  attachmentHistory: state.chatStatesByAgent.get("agent-A").attachmentHistory,
  messageListHtml: dom.messageList.innerHTML
}}));
"""
    completed = subprocess.run([node_bin, "-e", script], capture_output=True, text=True, check=True)
    data = json.loads(completed.stdout)
    assert data["attachmentHistory"] == []
    assert data["messageListHtml"] == "WELCOME"


def test_build_user_message_article_uses_current_user_display_name():
    node_bin = shutil.which("node")
    if not node_bin:
        pytest.skip("node is not installed; skipping JS helper behavior test")

    js_file = _chat_ui_js_source()
    get_current_user_display_name = _extract_js_function(js_file, "getCurrentUserDisplayName")
    build_user_message_article = _extract_js_function(js_file, "buildUserMessageArticle")

    script = f"""
const state = {{ currentUserName: "Alice" }};
function safe(value) {{ return String(value || ""); }}
function escapeHtml(value) {{ return String(value || ""); }}
function escapeHtmlAttr(value) {{ return String(value || ""); }}
{get_current_user_display_name}
{build_user_message_article}
const html = buildUserMessageArticle("hello", []);
console.log(JSON.stringify({{ html }}));
"""
    completed = subprocess.run([node_bin, "-e", script], capture_output=True, text=True, check=True)
    data = json.loads(completed.stdout)
    assert 'message-author">Alice<' in data["html"]


def test_render_chat_history_prefers_author_name_for_user_and_assistant():
    node_bin = shutil.which("node")
    if not node_bin:
        pytest.skip("node is not installed; skipping JS helper behavior test")

    js_file = _chat_ui_js_source()
    get_non_blank_author_name = _extract_js_function(js_file, "getNonBlankAuthorName")
    get_current_user_display_name = _extract_js_function(js_file, "getCurrentUserDisplayName")
    get_selected_assistant_display_name = _extract_js_function(js_file, "getSelectedAssistantDisplayName")
    get_history_message_display_name = _extract_js_function(js_file, "getHistoryMessageDisplayName")
    render_history_dependencies = _extract_render_chat_history_dependencies(js_file)

    script = f"""
const state = {{
  selectedAgentId: "agent-A",
  selectedAgentName: "Agent A",
  currentUserName: "Portal User",
  chatStatesByAgent: new Map([["agent-A", {{ attachmentHistory: [] }}]]),
}};
const appendedRows = [];
const dom = {{
  messageList: {{
    innerHTML: "",
    appendChild(node) {{ appendedRows.push(node); }},
  }},
}};
function getChatState() {{ return state.chatStatesByAgent.get("agent-A"); }}
function clearMessageListToWelcome() {{}}
function renderMarkdown() {{}}
function decorateToolMessages() {{}}
function attachThinkingToLatestAssistant() {{}}
function scrollToBottom() {{}}
function isTrackableThinkingEvent() {{ return false; }}
const document = {{
  createElement(tag) {{
    return {{
      tag,
      className: "",
      dataset: {{}},
      textContent: "",
      children: [],
      appendChild(child) {{ this.children.push(child); }},
    }};
  }},
}};
{get_non_blank_author_name}
{get_current_user_display_name}
{get_selected_assistant_display_name}
{get_history_message_display_name}
{render_history_dependencies}
renderChatHistory([
  {{ role: "user", content: "u", author_name: "Alice" }},
  {{ role: "assistant", content: "a", author_name: "Portal Agent" }},
], {{}});
const authorLabels = appendedRows.map((row) => row.children[0].children[0].textContent);
console.log(JSON.stringify({{ authorLabels }}));
"""
    completed = subprocess.run([node_bin, "-e", script], capture_output=True, text=True, check=True)
    data = json.loads(completed.stdout)
    assert data["authorLabels"] == ["Alice", "Portal Agent"]


def test_render_chat_history_assistant_falls_back_to_selected_agent_name():
    node_bin = shutil.which("node")
    if not node_bin:
        pytest.skip("node is not installed; skipping JS helper behavior test")

    js_file = _chat_ui_js_source()
    get_non_blank_author_name = _extract_js_function(js_file, "getNonBlankAuthorName")
    get_current_user_display_name = _extract_js_function(js_file, "getCurrentUserDisplayName")
    get_selected_assistant_display_name = _extract_js_function(js_file, "getSelectedAssistantDisplayName")
    get_history_message_display_name = _extract_js_function(js_file, "getHistoryMessageDisplayName")
    render_history_dependencies = _extract_render_chat_history_dependencies(js_file)

    script = f"""
const state = {{
  selectedAgentId: "agent-A",
  selectedAgentName: "Agent A",
  currentUserName: "Portal User",
  chatStatesByAgent: new Map([["agent-A", {{ attachmentHistory: [] }}]]),
}};
const appendedRows = [];
const dom = {{
  messageList: {{
    innerHTML: "",
    appendChild(node) {{ appendedRows.push(node); }},
  }},
}};
function getChatState() {{ return state.chatStatesByAgent.get("agent-A"); }}
function clearMessageListToWelcome() {{}}
function renderMarkdown() {{}}
function decorateToolMessages() {{}}
function attachThinkingToLatestAssistant() {{}}
function scrollToBottom() {{}}
function isTrackableThinkingEvent() {{ return false; }}
const document = {{
  createElement(tag) {{
    return {{
      tag,
      className: "",
      dataset: {{}},
      textContent: "",
      children: [],
      appendChild(child) {{ this.children.push(child); }},
    }};
  }},
}};
{get_non_blank_author_name}
{get_current_user_display_name}
{get_selected_assistant_display_name}
{get_history_message_display_name}
{render_history_dependencies}
renderChatHistory([
  {{ role: "assistant", content: "a" }},
], {{}});
const authorLabel = appendedRows[0].children[0].children[0].textContent;
console.log(JSON.stringify({{ authorLabel }}));
"""
    completed = subprocess.run([node_bin, "-e", script], capture_output=True, text=True, check=True)
    data = json.loads(completed.stdout)
    assert data["authorLabel"] == "Agent A"


def test_render_chat_history_blank_author_name_falls_back_to_current_names():
    node_bin = shutil.which("node")
    if not node_bin:
        pytest.skip("node is not installed; skipping JS helper behavior test")

    js_file = _chat_ui_js_source()
    get_non_blank_author_name = _extract_js_function(js_file, "getNonBlankAuthorName")
    get_current_user_display_name = _extract_js_function(js_file, "getCurrentUserDisplayName")
    get_selected_assistant_display_name = _extract_js_function(js_file, "getSelectedAssistantDisplayName")
    get_history_message_display_name = _extract_js_function(js_file, "getHistoryMessageDisplayName")
    render_history_dependencies = _extract_render_chat_history_dependencies(js_file)

    script = f"""
const state = {{
  selectedAgentId: "agent-A",
  selectedAgentName: "Portal Agent",
  currentUserName: "Alice",
  chatStatesByAgent: new Map([["agent-A", {{ attachmentHistory: [] }}]]),
}};
const appendedRows = [];
const dom = {{
  messageList: {{
    innerHTML: "",
    appendChild(node) {{ appendedRows.push(node); }},
  }},
}};
function getChatState() {{ return state.chatStatesByAgent.get("agent-A"); }}
function clearMessageListToWelcome() {{}}
function renderMarkdown() {{}}
function decorateToolMessages() {{}}
function attachThinkingToLatestAssistant() {{}}
function scrollToBottom() {{}}
function isTrackableThinkingEvent() {{ return false; }}
const document = {{
  createElement(tag) {{
    return {{
      tag,
      className: "",
      dataset: {{}},
      textContent: "",
      children: [],
      appendChild(child) {{ this.children.push(child); }},
    }};
  }},
}};
{get_non_blank_author_name}
{get_current_user_display_name}
{get_selected_assistant_display_name}
{get_history_message_display_name}
{render_history_dependencies}
renderChatHistory([
  {{ role: "user", content: "u", author_name: "   " }},
  {{ role: "assistant", content: "a", author_name: "   " }},
], {{}});
const authorLabels = appendedRows.map((row) => row.children[0].children[0].textContent);
console.log(JSON.stringify({{ authorLabels }}));
"""
    completed = subprocess.run([node_bin, "-e", script], capture_output=True, text=True, check=True)
    data = json.loads(completed.stdout)
    assert data["authorLabels"] == ["Alice", "Portal Agent"]


def test_handle_agent_chat_success_passes_selected_assistant_name_to_final_message_builder():
    node_bin = shutil.which("node")
    if not node_bin:
        pytest.skip("node is not installed; skipping JS helper behavior test")

    js_file = _chat_ui_js_source()
    create_state = _extract_js_function(js_file, "createDefaultChatState")
    ensure_state = _extract_js_function(js_file, "ensureChatState")
    update_session = _extract_js_function(js_file, "updateAgentSession")
    set_submitting = _extract_js_function(js_file, "setChatSubmittingForAgent")
    merge_events = _extract_js_function(js_file, "mergeThinkingEvents")
    get_selected_assistant_display_name = _extract_js_function(js_file, "getSelectedAssistantDisplayName")
    handle_success = _extract_js_function(js_file, "handleAgentChatSuccess")

    script = f"""
const state = {{
  selectedAgentId: "agent-A",
  selectedAgentName: "Portal Agent",
  mineAgents: [{{id: "agent-A", name: "Portal Agent"}}],
  chatStatesByAgent: new Map(),
  agentSessionIds: new Map(),
}};
const dom = {{ messageList: {{ insertAdjacentHTML() {{}} }} }};
const document = {{ hidden: false }};
let capturedAuthorName = null;
function setLastSessionId() {{}}
function syncHiddenSessionInputFromState() {{}}
function ensureEventSocketForSelectedAgent() {{}}
function removeTemporaryAssistantRows() {{}}
function getLatestOptimisticUserArticle() {{ return {{ dataset: {{ optimisticUser: "1" }} }}; }}
function buildAssistantMessageArticle(_content, _blocks, authorName) {{
  capturedAuthorName = authorName;
  return "";
}}
function attachThinkingToLatestAssistant() {{}}
function setChatStatus() {{}}
function renderMarkdown() {{}}
function decorateToolMessages() {{}}
function renderIcons() {{}}
function scrollToBottom() {{}}
function addEditButtonsToMessages() {{}}
function markAgentUnread() {{}}
function renderAgentList() {{}}
function notifyAgentCompletion() {{}}
function loadSessionForAgent() {{ throw new Error("should not reload"); }}
{create_state}
{ensure_state}
{update_session}
{set_submitting}
{merge_events}
{get_selected_assistant_display_name}
{handle_success}
const chatState = ensureChatState("agent-A");
chatState.activeRequest = {{ clientRequestId: "req-a" }};
(async () => {{
  await handleAgentChatSuccess("agent-A", {{ clientRequestId: "req-a", sessionIdAtSend: "s-a" }}, {{
    session_id: "s-a2",
    response: "done",
    display_blocks: [],
    author_name: "Runtime Alias"
  }});
  console.log(JSON.stringify({{ capturedAuthorName }}));
}})();
"""
    completed = subprocess.run([node_bin, "-e", script], capture_output=True, text=True, check=True)
    data = json.loads(completed.stdout)
    assert data["capturedAuthorName"] == "Portal Agent"


def test_ensure_event_socket_for_selected_agent_uses_active_request_id():
    node_bin = shutil.which("node")
    if not node_bin:
        pytest.skip("node is not installed; skipping JS helper behavior test")

    js_file = _chat_ui_js_source()
    ensure_socket_for_agent_fn = _extract_js_function(js_file, "ensureEventSocketForAgent")
    ensure_socket_fn = _extract_js_function(js_file, "ensureEventSocketForSelectedAgent")

    script = f"""
{ensure_socket_for_agent_fn}
{ensure_socket_fn}
const CONNECTING = 0;
const OPEN = 1;
const window = {{ location: {{ protocol: "https:", host: "portal.test" }} }};
let createdUrl = "";
const state = {{
  selectedAgentId: "agent-A",
  eventWs: null,
  eventWsAgentId: null,
  eventWsSessionId: null,
  eventWsRequestId: null,
}};
function currentSessionIdForSelectedAgent() {{ return "s-1"; }}
function ensureChatState() {{ return {{ activeRequest: {{ clientRequestId: "req-live-1" }} }}; }}
function disconnectEventSocket() {{}}
function handleAgentEventMessage() {{}}
class FakeWebSocket {{
  constructor(url) {{ this.url = url; this.readyState = CONNECTING; createdUrl = url; }}
}}
FakeWebSocket.CONNECTING = CONNECTING;
FakeWebSocket.OPEN = OPEN;
globalThis.WebSocket = FakeWebSocket;
ensureEventSocketForSelectedAgent();
console.log(JSON.stringify({{ createdUrl }}));
"""
    completed = subprocess.run([node_bin, "-e", script], capture_output=True, text=True, check=True)
    data = json.loads(completed.stdout)
    assert "session_id=s-1" in data["createdUrl"]
    assert "request_id=req-live-1" in data["createdUrl"]


def test_chat_ui_event_socket_replaces_stale_connecting_session():
    node_bin = shutil.which("node")
    if not node_bin:
        pytest.skip("node is not installed; skipping JS helper behavior test")

    js_file = _chat_ui_js_source()
    ensure_socket_for_agent_fn = _extract_js_function(js_file, "ensureEventSocketForAgent")
    ensure_socket_fn = _extract_js_function(js_file, "ensureEventSocketForSelectedAgent")

    script = f"""
{ensure_socket_for_agent_fn}
{ensure_socket_fn}

const events = [];
const CLOSED = 3;
const CONNECTING = 0;
const OPEN = 1;
let currentSession = "new-session";
let websocketCreated = 0;

const window = {{
  location: {{
    protocol: "https:",
    host: "portal.test",
  }}
}};

const state = {{
  selectedAgentId: "agent-A",
  eventWs: {{
    readyState: CONNECTING,
    close: () => {{
      events.push("closed:old");
      state.eventWs.readyState = CLOSED;
    }},
  }},
  eventWsAgentId: "agent-A",
  eventWsSessionId: "old-session",
  eventWsRequestId: "old-req",
}};

function ensureChatState() {{ return {{ sessionId: currentSession }}; }}

function currentSessionIdForSelectedAgent() {{
  return currentSession;
}}

function disconnectEventSocket() {{
  if (state.eventWs) state.eventWs.close();
  state.eventWs = null;
  state.eventWsAgentId = null;
  state.eventWsSessionId = null;
  state.eventWsRequestId = null;
}}

class FakeWebSocket {{
  constructor(url) {{
    this.url = url;
    this.readyState = CONNECTING;
    websocketCreated += 1;
    events.push("opened:" + url);
  }}
  close() {{
    this.readyState = CLOSED;
    events.push("closed:new");
  }}
}}
FakeWebSocket.CONNECTING = CONNECTING;
FakeWebSocket.OPEN = OPEN;
globalThis.WebSocket = FakeWebSocket;

ensureEventSocketForAgent("agent-A", "s-1", "req-1");
const directUrl = state.eventWs?.url || null;

ensureEventSocketForSelectedAgent();
const firstSocket = state.eventWs;
const firstUrl = firstSocket?.url || null;
const firstSession = state.eventWsSessionId;
const firstCreated = websocketCreated;

ensureEventSocketForSelectedAgent();
const secondSocket = state.eventWs;
const secondUrl = secondSocket?.url || null;
const secondSession = state.eventWsSessionId;
const secondCreated = websocketCreated;

console.log(JSON.stringify({{
  events,
  firstUrl,
  firstSession,
  firstCreated,
  secondUrl,
  secondSession,
  secondCreated,
  sameSocketOnSecondCall: firstSocket === secondSocket,
  directUrl,
}}));
"""

    completed = subprocess.run(
        [node_bin, "-e", script],
        capture_output=True,
        text=True,
        check=True,
    )
    data = json.loads(completed.stdout)

    assert "closed:old" in data["events"]
    assert data["directUrl"] == "wss://portal.test/a/agent-A/api/events?session_id=s-1&request_id=req-1"
    assert data["firstUrl"] == "wss://portal.test/a/agent-A/api/events?session_id=new-session"
    assert data["firstSession"] == "new-session"
    assert data["firstCreated"] == 2
    assert data["secondCreated"] == 2
    assert data["secondUrl"] == data["firstUrl"]
    assert data["secondSession"] == "new-session"
    assert data["sameSocketOnSecondCall"] is True


def test_switching_to_b_while_a_submitting_reenables_send_for_b():
    node_bin = shutil.which("node")
    if not node_bin:
        pytest.skip("node is not installed; skipping JS helper behavior test")

    js_file = _chat_ui_js_source()
    create_state = _extract_js_function(js_file, "createDefaultChatState")
    ensure_state = _extract_js_function(js_file, "ensureChatState")
    has_active = _extract_js_function(js_file, "hasActiveChatRequestForAgent")
    sync_controls = _extract_js_function(js_file, "syncSelectedAgentChatActionControls")
    set_submitting = _extract_js_function(js_file, "setChatSubmittingForAgent")
    restore_composer = _extract_js_function(js_file, "restoreComposerForAgent")

    script = f"""
const state = {{
  selectedAgentId: "agent-A",
  chatStatesByAgent: new Map(),
  agentSessionIds: new Map(),
}};
const dom = {{
  sendChatBtn: {{ disabled: true }},
  chatInput: {{ value: "" }},
}};
const attachmentsNode = {{ value: "" }};
const document = {{
  getElementById(id) {{
    if (id === "chat-attachments") return attachmentsNode;
    if (id === "btn-sessions") return null;
    return null;
  }},
}};
function setButtonDisabled() {{}}
function getSelectedAgentStatus() {{ return "running"; }}
function syncChatInputHeight() {{}}
function renderInputPreview() {{}}
function renderComposerModelSelectorForAgent() {{}}
{create_state}
{ensure_state}
{has_active}
{sync_controls}
{set_submitting}
{restore_composer}
ensureChatState("agent-A");
ensureChatState("agent-B");
setChatSubmittingForAgent("agent-A", true);
setChatSubmittingForAgent("agent-B", false);
state.selectedAgentId = "agent-B";
restoreComposerForAgent("agent-B");
console.log(JSON.stringify({{ disabled: dom.sendChatBtn.disabled }}));
"""
    completed = subprocess.run([node_bin, "-e", script], capture_output=True, text=True, check=True)
    data = json.loads(completed.stdout)
    assert data["disabled"] is False


def test_start_new_chat_is_blocked_while_selected_agent_request_active():
    # Regression guard: prevent New Chat from clearing/rewiring session during active request.
    node_bin = shutil.which("node")
    if not node_bin:
        pytest.skip("node is not installed; skipping JS helper behavior test")

    js_file = _chat_ui_js_source()
    create_state = _extract_js_function(js_file, "createDefaultChatState")
    ensure_state = _extract_js_function(js_file, "ensureChatState")
    has_active = _extract_js_function(js_file, "hasActiveChatRequestForAgent")
    active_message = _extract_js_function(js_file, "activeChatRequestMessage")
    guard_active = _extract_js_function(js_file, "guardNoActiveChatRequestForAgent")
    start_new_chat = _extract_js_function(js_file, "startNewChatForSelectedAgent")

    script = f"""
const state = {{
  selectedAgentId: "agent-A",
  chatStatesByAgent: new Map(),
  agentSessionIds: new Map(),
}};
const calls = {{
  updatedSession: 0,
  setChatSubmittingFalse: 0,
  toast: [],
  status: [],
}};
const dom = {{
  chatInput: {{ focus() {{}} }},
}};
function ensureRunningSelectedAssistant() {{ return true; }}
function showToast(message) {{ calls.toast.push(message); }}
function setChatStatus(message, isError) {{ calls.status.push([message, !!isError]); }}
function updateSelectedAgentSession() {{ calls.updatedSession += 1; }}
function closeSessionsDrawer() {{}}
function removeTemporaryAssistantRows() {{}}
function clearMessageListToWelcome() {{}}
function setChatSubmitting(active) {{ if (active === false) calls.setChatSubmittingFalse += 1; }}
function resetChatInputHeight() {{}}
function getChatState() {{ return ensureChatState(state.selectedAgentId); }}
{create_state}
{ensure_state}
{has_active}
{active_message}
{guard_active}
{start_new_chat}
const chatState = ensureChatState("agent-A");
chatState.activeRequest = {{ clientRequestId: "req-a" }};
chatState.sessionId = "s-a";
(async () => {{
  await startNewChatForSelectedAgent();
  console.log(JSON.stringify({{
    updatedSession: calls.updatedSession,
    setChatSubmittingFalse: calls.setChatSubmittingFalse,
    sessionId: chatState.sessionId,
    toast: calls.toast,
    status: calls.status,
  }}));
}})();
"""
    completed = subprocess.run([node_bin, "-e", script], capture_output=True, text=True, check=True)
    data = json.loads(completed.stdout)
    assert data["updatedSession"] == 0
    assert data["setChatSubmittingFalse"] == 0
    assert data["sessionId"] == "s-a"
    assert any("still working" in msg for msg in data["toast"])
    assert any("still working" in item[0] and item[1] for item in data["status"])


def test_submit_chat_has_active_request_guard_regression():
    # Source-level guard to prevent accidental rollback to silent duplicate-send behavior.
    js_source = _chat_ui_js_source()
    submit_start = js_source.find("async function submitChatForSelectedAgent()")
    assert submit_start >= 0
    submit_slice = js_source[submit_start: submit_start + 1500]
    assert 'guardNoActiveChatRequestForAgent(agentIdAtSend, "send another message")' in submit_slice


def test_derive_session_recovery_notice_for_failed_metadata():
    # Recovery notice must stay explicit for failed/error metadata after refresh.
    node_bin = shutil.which("node")
    if not node_bin:
        pytest.skip("node is not installed; skipping JS helper behavior test")

    js_file = _chat_ui_js_source()
    derive_notice = _extract_js_function(js_file, "deriveSessionRecoveryNotice")

    script = f"""
{derive_notice}
const byType = deriveSessionRecoveryNotice({{ latest_event_type: "chat.failed" }});
const byState = deriveSessionRecoveryNotice({{ latest_event_state: "error" }});
console.log(JSON.stringify({{ byType, byState }}));
"""
    completed = subprocess.run([node_bin, "-e", script], capture_output=True, text=True, check=True)
    data = json.loads(completed.stdout)
    assert data["byType"]["level"] == "error"
    assert "failed" in data["byType"]["message"].lower()
    assert data["byState"]["level"] == "error"
    assert "failed" in data["byState"]["message"].lower()


def test_derive_session_recovery_notice_for_running_metadata():
    # Running notice is intentionally conservative and must not imply restored live progress.
    node_bin = shutil.which("node")
    if not node_bin:
        pytest.skip("node is not installed; skipping JS helper behavior test")

    js_file = _chat_ui_js_source()
    derive_notice = _extract_js_function(js_file, "deriveSessionRecoveryNotice")

    script = f"""
{derive_notice}
const byType = deriveSessionRecoveryNotice({{ latest_event_type: "chat.started" }});
const byState = deriveSessionRecoveryNotice({{ latest_event_state: "running" }});
console.log(JSON.stringify({{ byType, byState }}));
"""
    completed = subprocess.run([node_bin, "-e", script], capture_output=True, text=True, check=True)
    data = json.loads(completed.stdout)
    assert data["byType"]["level"] == "warning"
    assert ("interrupted" in data["byType"]["message"].lower()) or ("still be running" in data["byType"]["message"].lower())
    assert "cannot be resumed" in data["byType"]["message"].lower()
    assert data["byState"]["level"] == "warning"
    assert ("interrupted" in data["byState"]["message"].lower()) or ("still be running" in data["byState"]["message"].lower())
    assert "cannot be resumed" in data["byState"]["message"].lower()


def test_load_session_uses_recovery_notice_only_when_not_locally_active():
    # Source-level regression: avoid overwriting local live status while active request exists.
    js_source = _chat_ui_js_source()
    assert "function deriveSessionRecoveryNotice(" in js_source
    load_start = js_source.find("async function loadSessionForAgent(")
    assert load_start >= 0
    load_slice = js_source[load_start: load_start + 2200]
    assert "!hasActiveChatRequestForAgent(agentId)" in load_slice
    assert "setChatStatus(recoveryNotice.message, recoveryNotice.level === \"error\")" in load_slice


def test_success_completion_clears_active_request_before_resyncing_controls():
    # Order regression: controls should re-sync only after active/inflight flags are cleared.
    js_source = _chat_ui_js_source()
    success_start = js_source.find("async function handleAgentChatSuccess")
    assert success_start >= 0
    success_slice = js_source[success_start: success_start + 4500]

    idx_clear_active = success_slice.find("chatState.activeRequest = null;")
    idx_clear_inflight = success_slice.find("chatState.inflightThinking = null;")
    idx_clear_pending = success_slice.find("chatState.pendingThinkingEvents = null;")
    idx_resync_controls = success_slice.find("setChatSubmittingForAgent(agentIdAtSend, false);")

    assert idx_clear_active >= 0
    assert idx_clear_inflight >= 0
    assert idx_clear_pending >= 0
    assert idx_resync_controls >= 0
    assert idx_clear_active < idx_resync_controls
    assert idx_clear_inflight < idx_resync_controls
    assert idx_clear_pending < idx_resync_controls


def test_failure_completion_marks_inflight_done_before_resyncing_controls():
    # Order regression for failure path mirrors success-path control re-sync guarantees.
    js_source = _chat_ui_js_source()
    failure_start = js_source.find("function handleAgentChatFailure")
    assert failure_start >= 0
    failure_slice = js_source[failure_start: failure_start + 3000]

    idx_clear_active = failure_slice.find("chatState.activeRequest = null;")
    idx_mark_done = failure_slice.find("chatState.inflightThinking.completed = true;")
    idx_resync_controls = failure_slice.find("setChatSubmittingForAgent(agentIdAtSend, false);")

    assert idx_clear_active >= 0
    assert idx_mark_done >= 0
    assert idx_resync_controls >= 0
    assert idx_clear_active < idx_resync_controls
    assert idx_mark_done < idx_resync_controls


def test_event_message_does_not_claim_empty_session_without_matching_request():
    # Core stale-event guard: no claim + no false-busy side effects on unmatched old events.
    node_bin = shutil.which("node")
    if not node_bin:
        pytest.skip("node is not installed; skipping JS helper behavior test")

    js_file = _chat_ui_js_source()
    create_state = _extract_js_function(js_file, "createDefaultChatState")
    ensure_state = _extract_js_function(js_file, "ensureChatState")
    normalize_runtime_event = _extract_js_function(js_file, "normalizeRuntimeEvent")
    is_completion = _extract_js_function(js_file, "isCompletionRuntimeState")
    is_trackable = _extract_js_function(js_file, "isTrackableThinkingEvent")
    merge_events = _extract_js_function(js_file, "mergeThinkingEvents")
    extract_budget = _extract_js_function(js_file, "extractContextBudget")
    has_meaningful = _extract_js_function(js_file, "hasMeaningfulContextState")
    has_contents = _extract_js_function(js_file, "hasMeaningfulContextContents")
    update_context = _extract_js_function(js_file, "updateThinkingContextFromEvent")
    has_active = _extract_js_function(js_file, "hasActiveChatRequestForAgent")
    handle_event = _extract_js_function(js_file, "handleAgentEventMessage")

    script = f"""
const state = {{
  selectedAgentId: "agent-A",
  chatStatesByAgent: new Map(),
  agentSessionIds: new Map(),
}};
const COMPLETION_RUNTIME_STATES = new Set(["completed", "complete", "success", "succeeded"]);
const dom = {{ chatSessionId: {{ value: "" }} }};
function isThinkingPanelActiveForAgent() {{ return false; }}
function scheduleThinkingPanelRefresh() {{}}
{create_state}
{ensure_state}
{normalize_runtime_event}
{is_completion}
{is_trackable}
{merge_events}
{extract_budget}
{has_meaningful}
{has_contents}
{update_context}
{has_active}
{handle_event}
const chatState = ensureChatState("agent-A");
chatState.sessionId = "";
chatState.activeRequest = null;
handleAgentEventMessage(JSON.stringify({{
  type: "execution.started",
  event_type: "execution.started",
  request_id: "old-req",
  session_id: "session-A",
  data: {{ message: "old event" }}
}}), {{ agentId: "agent-A" }});
console.log(JSON.stringify({{
  sessionId: chatState.sessionId,
  hasAgentSession: state.agentSessionIds.has("agent-A"),
  domSession: dom.chatSessionId.value,
  inflightThinking: !!chatState.inflightThinking,
  busy: hasActiveChatRequestForAgent("agent-A"),
}}));
"""
    completed = subprocess.run([node_bin, "-e", script], capture_output=True, text=True, check=True)
    data = json.loads(completed.stdout)
    assert data["sessionId"] == ""
    assert data["hasAgentSession"] is False
    assert data["domSession"] == ""
    assert data["inflightThinking"] is False
    assert data["busy"] is False


def test_event_message_can_claim_empty_session_when_matching_active_request():
    # Positive path: matching active request can still bind new runtime session_id.
    node_bin = shutil.which("node")
    if not node_bin:
        pytest.skip("node is not installed; skipping JS helper behavior test")

    js_file = _chat_ui_js_source()
    create_state = _extract_js_function(js_file, "createDefaultChatState")
    ensure_state = _extract_js_function(js_file, "ensureChatState")
    normalize_runtime_event = _extract_js_function(js_file, "normalizeRuntimeEvent")
    is_completion = _extract_js_function(js_file, "isCompletionRuntimeState")
    is_trackable = _extract_js_function(js_file, "isTrackableThinkingEvent")
    merge_events = _extract_js_function(js_file, "mergeThinkingEvents")
    extract_budget = _extract_js_function(js_file, "extractContextBudget")
    has_meaningful = _extract_js_function(js_file, "hasMeaningfulContextState")
    has_contents = _extract_js_function(js_file, "hasMeaningfulContextContents")
    update_context = _extract_js_function(js_file, "updateThinkingContextFromEvent")
    handle_event = _extract_js_function(js_file, "handleAgentEventMessage")

    script = f"""
const state = {{
  selectedAgentId: "agent-A",
  chatStatesByAgent: new Map(),
  agentSessionIds: new Map(),
}};
const COMPLETION_RUNTIME_STATES = new Set(["completed", "complete", "success", "succeeded"]);
const dom = {{ chatSessionId: {{ value: "" }} }};
function isThinkingPanelActiveForAgent() {{ return false; }}
function scheduleThinkingPanelRefresh() {{}}
{create_state}
{ensure_state}
{normalize_runtime_event}
{is_completion}
{is_trackable}
{merge_events}
{extract_budget}
{has_meaningful}
{has_contents}
{update_context}
{handle_event}
const chatState = ensureChatState("agent-A");
chatState.sessionId = "";
chatState.activeRequest = {{ clientRequestId: "req-new" }};
handleAgentEventMessage(JSON.stringify({{
  type: "execution.started",
  event_type: "execution.started",
  request_id: "req-new",
  session_id: "session-new",
  data: {{ message: "new event" }}
}}), {{ agentId: "agent-A" }});
console.log(JSON.stringify({{
  sessionId: chatState.sessionId,
  mappedSession: state.agentSessionIds.get("agent-A"),
  domSession: dom.chatSessionId.value,
}}));
"""
    completed = subprocess.run([node_bin, "-e", script], capture_output=True, text=True, check=True)
    data = json.loads(completed.stdout)
    assert data["sessionId"] == "session-new"
    assert data["mappedSession"] == "session-new"
    assert data["domSession"] == "session-new"
