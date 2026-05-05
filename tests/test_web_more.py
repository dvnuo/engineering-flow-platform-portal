"""Tests for web.py - settings and config."""
import json
import shutil
import subprocess
from pathlib import Path



def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _chat_ui_js_source() -> str:
    chat_ui_path = _repo_root() / "app" / "static" / "js" / "chat_ui.js"
    return chat_ui_path.read_text(encoding="utf-8")
from fastapi.testclient import TestClient
import pytest
from _js_extract_helpers import _extract_js_function, _extract_js_helper_block


def test_agent_settings_panel():
    """Test agent settings panel."""
    from app.main import app
    client = TestClient(app)
    response = client.get("/app/agents/agent-123/settings/panel")
    assert response.status_code in [200, 302, 401, 403, 404]


def test_agent_settings_save():
    """Test agent settings save."""
    from app.main import app
    client = TestClient(app)
    response = client.post("/app/agents/agent-123/settings/save", 
                         json={"llm": {"provider": "openai"}})
    assert response.status_code in [200, 302, 400, 401, 403, 404]



def test_agent_sessions_panel():
    """Test agent sessions panel."""
    from app.main import app
    client = TestClient(app)
    response = client.get("/app/agents/agent-123/sessions/panel")
    assert response.status_code in [200, 302, 401, 403, 404]


def test_agent_skills_panel():
    """Test agent skills panel."""
    from app.main import app
    client = TestClient(app)
    response = client.get("/app/agents/agent-123/skills/panel")
    assert response.status_code in [200, 302, 401, 403, 404]


def test_agent_usage_panel():
    """Test agent usage panel."""
    from app.main import app
    client = TestClient(app)
    response = client.get("/app/agents/agent-123/usage/panel")
    assert response.status_code in [200, 302, 401, 403, 404]


def test_users_panel():
    """Test users panel."""
    from app.main import app
    client = TestClient(app)
    response = client.get("/app/users/panel")
    assert response.status_code in [200, 302, 401, 403]


def test_proxy_agents_usage():
    """Test canonical agents usage proxy route."""
    from app.main import app
    client = TestClient(app)
    response = client.get("/a/agent-123/api/usage")
    assert response.status_code in [401, 403, 404, 409, 502]


def test_proxy_agent_api():
    """Test proxy to agent API."""
    from app.main import app
    client = TestClient(app)
    # Test proxy endpoint
    response = client.post("/a/agent-123/api/chat", 
                         json={"message": "test"})
    assert response.status_code in [400, 401, 403, 404, 500, 502]


def test_proxy_agent_files_list():
    """Test proxy to agent files list."""
    from app.main import app
    client = TestClient(app)
    response = client.get("/a/agent-123/api/files")
    assert response.status_code in [401, 403, 404, 500]


def test_proxy_agent_events():
    """Test proxy to agent events."""
    from app.main import app
    client = TestClient(app)
    response = client.get("/a/agent-123/api/events")
    assert response.status_code in [400, 401, 403, 404]


def test_agent_runtime_destroy():
    """Test agent runtime destroy."""
    from app.main import app
    client = TestClient(app)
    response = client.post("/api/agents/agent-123/destroy")
    assert response.status_code in [200, 401, 403, 404]


def test_agent_runtime_delete():
    """Test agent runtime delete."""
    from app.main import app
    client = TestClient(app)
    response = client.post("/api/agents/agent-123/delete-runtime")
    assert response.status_code in [200, 401, 403, 404]


def test_managed_settings_initializer_hooks_present():
    js = _chat_ui_js_source()
    assert "function initializeManagedSettingsPanels()" in js
    assert 'target?.id === "workspace-detail-content"' in js
    assert "initializeManagedSettingsPanels();" in js
    assert "loadRuntimeProfilePanelContent(profileId)" in js


def test_chat_ui_layout_persistence_source_markers_present():
    js = _chat_ui_js_source()
    assert 'const UI_LAYOUT_PREFS_STORAGE_KEY = "portal-ui-layout-prefs-v1";' in js
    assert "function readUiLayoutPreferences()" in js
    assert "function persistUiLayoutPreferences({" in js
    assert "includeSecondaryPane = true" in js
    assert "includeToolPanel = true" in js
    assert "clearToolPanelPreference = false" in js
    assert "const existing = readUiLayoutPreferences()" in js
    assert "async function restorePinnedToolPanelFromPreferencesOnce()" in js
    assert "async function refreshAll({ preserveLayout = false } = {})" in js
    assert "preserveCollapsed" in js
    assert "includeToolPanel: false" in js
    assert "clearToolPanelPreference: true" in js
    assert "await refreshAll({ preserveLayout: true });" in js
    assert "await restorePinnedToolPanelFromPreferencesOnce();" in js


def test_chat_ui_layout_persistence_calls_present_in_tool_panel_actions():
    js = _chat_ui_js_source()
    toggle_fn = _extract_js_function(js, "toggleToolPanelPinned")
    assert "applyToolPanelState();" in toggle_fn
    assert "persistUiLayoutPreferences({" in toggle_fn
    assert "clearToolPanelPreference: !state.toolPanelPinned" in toggle_fn
    assert toggle_fn.find("applyToolPanelState();") < toggle_fn.find("persistUiLayoutPreferences({")

    close_fn = _extract_js_function(js, "closeToolPanel")
    assert "applyToolPanelState();" in close_fn
    assert "persistUiLayoutPreferences({" in close_fn
    assert "clearToolPanelPreference: true" in close_fn
    assert close_fn.find("applyToolPanelState();") < close_fn.find("persistUiLayoutPreferences({")

    set_panel_fn = _extract_js_function(js, "setToolPanel")
    assert "persistPreference = true" in set_panel_fn
    assert "openToolPanel();" in set_panel_fn
    assert "persistUiLayoutPreferences({ includeSecondaryPane: false, includeToolPanel: true });" in set_panel_fn
    assert set_panel_fn.find("openToolPanel();") < set_panel_fn.find("persistUiLayoutPreferences({")


def test_chat_ui_layout_persistence_merge_behavior_does_not_overwrite_pinned_preferences():
    node_bin = shutil.which("node")
    if not node_bin:
        pytest.skip("node is not installed; skipping JS helper behavior test")

    js = _chat_ui_js_source()
    persist_fn = _extract_js_function(js, "persistUiLayoutPreferences")
    read_fn = _extract_js_function(js, "readUiLayoutPreferences")
    normalize_fn = _extract_js_function(js, "normalizeUtilityPanelKey")

    script = f"""
const UI_LAYOUT_PREFS_STORAGE_KEY = "portal-ui-layout-prefs-v1";
const ALLOWED_UTILITY_PANEL_KEYS = new Set(["details","sessions","thinking","server-files","skills","usage","uploads","users"]);
const store = new Map();
const localStorage = {{
  getItem(key) {{ return store.has(key) ? store.get(key) : null; }},
  setItem(key, value) {{ store.set(key, String(value)); }},
  removeItem(key) {{ store.delete(key); }},
}};
const state = {{
  secondaryPaneCollapsed: false,
  toolPanelOpen: false,
  toolPanelPinned: false,
  activeUtilityPanel: null,
}};
function getCurrentToolPanelWidth() {{ return 777; }}
{normalize_fn}
{read_fn}
{persist_fn}

localStorage.setItem(UI_LAYOUT_PREFS_STORAGE_KEY, JSON.stringify({{
  version: 1,
  secondaryPaneCollapsed: false,
  toolPanelPinned: true,
  activeUtilityPanel: "details",
  toolPanelWidth: 620,
}}));

state.secondaryPaneCollapsed = true;
state.toolPanelOpen = true;
state.toolPanelPinned = false;
state.activeUtilityPanel = null;
persistUiLayoutPreferences({{ includeToolPanel: false }});
const afterSecondary = JSON.parse(localStorage.getItem(UI_LAYOUT_PREFS_STORAGE_KEY));

state.secondaryPaneCollapsed = false;
state.toolPanelOpen = true;
state.toolPanelPinned = false;
state.activeUtilityPanel = "thinking";
persistUiLayoutPreferences({{ includeSecondaryPane: false, includeToolPanel: true }});
const afterOverlay = JSON.parse(localStorage.getItem(UI_LAYOUT_PREFS_STORAGE_KEY));

state.toolPanelOpen = false;
state.toolPanelPinned = false;
persistUiLayoutPreferences({{
  includeSecondaryPane: false,
  includeToolPanel: true,
  clearToolPanelPreference: true,
}});
const afterClear = JSON.parse(localStorage.getItem(UI_LAYOUT_PREFS_STORAGE_KEY));

console.log(JSON.stringify({{ afterSecondary, afterOverlay, afterClear }}));
"""
    completed = subprocess.run([node_bin, "-e", script], capture_output=True, text=True, check=True)
    data = json.loads(completed.stdout)

    assert data["afterSecondary"]["secondaryPaneCollapsed"] is True
    assert data["afterSecondary"]["toolPanelPinned"] is True
    assert data["afterSecondary"]["activeUtilityPanel"] == "details"
    assert data["afterSecondary"]["toolPanelWidth"] == 620

    assert data["afterOverlay"]["toolPanelPinned"] is True
    assert data["afterOverlay"]["activeUtilityPanel"] == "details"

    assert data["afterClear"]["toolPanelPinned"] is False
    assert data["afterClear"]["activeUtilityPanel"] is None


def test_chat_ui_narrow_startup_defer_logic_present_without_nearby_localstorage_write():
    js = _chat_ui_js_source()
    marker = "if (initialUiLayoutPrefs.toolPanelPinned && !isWideEnoughToPinToolPanel()) {"
    idx = js.find(marker)
    assert idx != -1
    snippet = js[idx:idx + 320]
    assert "state.toolPanelOpen = false" in snippet
    assert "state.toolPanelPinned = false" in snippet
    assert "localStorage.setItem" not in snippet


def test_update_model_options_keeps_unknown_initial_but_not_cross_provider_leak():
    node_bin = shutil.which("node")
    if not node_bin:
        pytest.skip("node is not installed; skipping managed settings model behavior test")

    js_file = _chat_ui_js_source()
    update_model_options_fn = _extract_js_function(js_file, "updateModelOptions")

    marker = "const managedProviderModels ="
    start = js_file.find(marker)
    assert start >= 0, "managedProviderModels block not found"
    brace_start = js_file.find("{", start)
    assert brace_start >= 0, "managedProviderModels block start not found"
    depth = 0
    end = -1
    for idx in range(brace_start, len(js_file)):
        char = js_file[idx]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                end = idx
                break
    assert end > brace_start, "managedProviderModels block end not found"
    managed_models_block = js_file[start:end + 2]
    assert '{ value: "gpt-5.4-mini", label: "GPT-5.4 mini" }' in managed_models_block
    assert 'openai' in managed_models_block
    assert 'github_copilot' in managed_models_block
    github_block_start = managed_models_block.index('github_copilot: [')
    github_first_model = managed_models_block[github_block_start:github_block_start + 160]
    assert '{ value: "gpt-5.4-mini", label: "GPT-5.4 mini" }' in github_first_model
    openai_block_start = managed_models_block.index('openai: [')
    openai_first_model = managed_models_block[openai_block_start:openai_block_start + 140]
    assert '{ value: "gpt-5.4-mini", label: "GPT-5.4 mini" }' in openai_first_model

    script = f"""
{managed_models_block}
{update_model_options_fn}

const noop = () => {{}};
function makeOption() {{
  return {{ value: "", textContent: "" }};
}}
const document = {{ createElement: makeOption }};
global.document = document;
let stopCalled = 0;
function stopCopilotPolling(_root) {{ stopCalled += 1; }}

function makeSelect(initialValue = "") {{
  return {{
    value: initialValue,
    innerHTML: "",
    dataset: {{}},
    options: [],
    appendChild(option) {{ this.options.push(option); }},
    classList: {{ toggle: noop, add: noop }},
  }};
}}

function makeRoot(providerValue, modelValue) {{
  const provider = makeSelect(providerValue);
  provider.dataset.initialProvider = providerValue;
  const model = makeSelect(modelValue);
  model.dataset.initialValue = modelValue;
  model.dataset.currentValue = modelValue;
  model.dataset.lastProvider = providerValue;
  const copilotBtn = {{ classList: {{ toggle: noop }} }};
  const authStatus = {{ classList: {{ add: noop }} }};
  return {{
    provider,
    model,
    querySelector(sel) {{
      if (sel === "#llm_provider") return provider;
      if (sel === "#llm_model") return model;
      if (sel === "#copilot_auth_btn") return copilotBtn;
      if (sel === "#copilot_auth_status") return authStatus;
      return null;
    }},
  }};
}}

const rootA = makeRoot("openai", "custom-unknown-model");
updateModelOptions(rootA);
const scenarioA = {{
  selected: rootA.model.value,
  hasCurrent: rootA.model.options.some((o) => o.textContent === "custom-unknown-model (Current)"),
}};

const rootB = makeRoot("openai", "gpt-4.1");
updateModelOptions(rootB);
rootB.provider.value = "anthropic";
updateModelOptions(rootB);
const scenarioB = {{
  selected: rootB.model.value,
  hasLeakedCurrent: rootB.model.options.some((o) => o.textContent === "gpt-4.1 (Current)"),
  options: rootB.model.options.map((o) => o.value),
}};

console.log(JSON.stringify({{ scenarioA, scenarioB }}));
"""
    completed = subprocess.run([node_bin, "-e", script], capture_output=True, text=True, check=True)
    result = json.loads(completed.stdout.strip())

    assert result["scenarioA"]["selected"] == "custom-unknown-model"
    assert result["scenarioA"]["hasCurrent"] is True
    assert result["scenarioB"]["selected"] != "gpt-4.1"
    assert result["scenarioB"]["hasLeakedCurrent"] is False
    assert result["scenarioB"]["selected"] in result["scenarioB"]["options"]


def test_agent_defaults():
    """Test agent defaults endpoint."""
    from app.main import app
    client = TestClient(app)
    response = client.get("/api/agents/defaults")
    assert response.status_code in [200, 401, 403]


def test_proxy_api_chat_stream():
    """Test proxy chat stream endpoint."""
    from app.main import app
    client = TestClient(app)
    response = client.post("/a/agent-123/api/chat/stream",
                         json={"message": "test"})
    assert response.status_code in [400, 401, 403, 404, 500, 502]


def test_chat_ui_display_block_helpers_behavior():
    node_bin = shutil.which("node")
    if not node_bin:
        pytest.skip("node is not installed; skipping display block helper test")

    js_file = _chat_ui_js_source()
    meaningful_text_block = _extract_js_function(js_file, "isMeaningfulText")
    pick_value_block = _extract_js_function(js_file, "pickFirstMeaningfulBlockValue")
    has_renderable_block = _extract_js_function(js_file, "hasRenderableDisplayBlock")
    parse_block = _extract_js_function(js_file, "parseDisplayBlocks")
    text_block = _extract_js_function(js_file, "getDisplayBlockText")
    code_block = _extract_js_function(js_file, "renderCodeBlock")
    table_block = _extract_js_function(js_file, "renderTableBlock")
    single_block = _extract_js_function(js_file, "renderSingleDisplayBlock")
    render_blocks_block = _extract_js_function(js_file, "renderDisplayBlocksToHtml")

    script = f"""
const safe = (v) => String(v ?? "");
const normalizeMarkdownText = (v) => String(v || "");
const escapeHtmlAttr = (v) => String(v ?? "");
const md = {{ render: (v) => `<p>${{v}}</p>` }};
{meaningful_text_block}
{pick_value_block}
{has_renderable_block}
{parse_block}
{text_block}
{code_block}
{table_block}
{single_block}
{render_blocks_block}

const result = {{
  invalidParseLength: parseDisplayBlocks("not-json").length,
  objectInputLength: parseDisplayBlocks({{}}).length,
  arrayInputLength: parseDisplayBlocks([{{ type: "markdown", content: "ok" }}]).length,
  filteredBlankTypeLength: parseDisplayBlocks(JSON.stringify([
    {{ type: "   ", content: "x" }},
    {{ type: "markdown", content: "ok" }},
  ])).length,
  filteredBlankTypedContentLength: parseDisplayBlocks([
    {{ type: "tool_result", content: "   " }},
    {{ type: "markdown", content: "ok" }},
  ]).length,
  columnsTable: renderTableBlock({{ columns: ["A"], rows: [["1"]] }}),
  fallbackOnly: renderTableBlock({{ content: "fallback only" }}),
  toolResult: renderSingleDisplayBlock({{
    type: "tool_result",
    status: "success",
    title: "Bash",
    content: "Done",
  }}),
  codeFromText: renderSingleDisplayBlock({{
    type: "code",
    lang: "python",
    text: "print(1)",
  }}),
  toolResultFromOutput: renderSingleDisplayBlock({{
    type: "tool_result",
    title: "Bash",
    status: "success",
    output: "done from output",
  }}),
  blankContentFallsBackToOutput: renderSingleDisplayBlock({{
    type: "tool_result",
    title: "Bash",
    status: "success",
    content: "   ",
    output: "done from output",
  }}),
  toolResultFromResult: renderSingleDisplayBlock({{
    type: "tool_result",
    title: "Bash",
    status: "success",
    result: "done from result",
  }}),
  calloutFromMessage: renderSingleDisplayBlock({{
    type: "callout",
    tone: "warning",
    title: "注意",
    message: "需要确认",
  }}),
  markdownFromValue: renderSingleDisplayBlock({{
    type: "markdown",
    value: "hello from value",
  }}),
  blankCodeContentFallsBackToText: renderSingleDisplayBlock({{
    type: "code",
    lang: "python",
    content: "   ",
    text: "print(1)",
  }}),
  blankCodeFieldFallsBackToText: renderSingleDisplayBlock({{
    type: "code",
    lang: "python",
    code: "   ",
    text: "print(1)",
  }}),
  codeOnly: renderCodeBlock({{
    type: "code",
    code: "print(1)",
    language: "python",
  }}),
  renderCodeFromCodeField: renderCodeBlock({{
    type: "code",
    code: "print(1)",
    language: "python",
  }}),
  renderCodeBlankContentFallback: renderCodeBlock({{
    type: "code",
    content: "   ",
    text: "x = 1",
    language: "python",
  }}),
  calloutFromEnglishMessage: renderSingleDisplayBlock({{
    type: "callout",
    tone: "warning",
    title: "Note",
    message: "Heads up",
  }}),
  bodylessBlocksFallbackPlaceholder: renderDisplayBlocksToHtml([
    {{ type: "tool_result", title: "Bash", content: "   " }},
  ], ""),
}};
console.log(JSON.stringify(result));
"""

    completed = subprocess.run(
        [node_bin, "-e", script],
        capture_output=True,
        text=True,
        check=True,
    )
    data = json.loads(completed.stdout)

    assert data["invalidParseLength"] == 0
    assert data["objectInputLength"] == 0
    assert data["arrayInputLength"] == 1
    assert data["filteredBlankTypeLength"] == 1
    assert data["filteredBlankTypedContentLength"] == 1
    assert "<th>A</th>" in data["columnsTable"]
    assert "<table>" not in data["fallbackOnly"]
    assert "<p>fallback only</p>" in data["fallbackOnly"]
    assert "message-tool-result is-success" in data["toolResult"]
    assert "print(1)" in data["codeFromText"]
    assert "language-python" in data["codeFromText"]
    assert "message-tool-result is-success" in data["toolResultFromOutput"]
    assert "done from output" in data["toolResultFromOutput"]
    assert "done from output" in data["blankContentFallsBackToOutput"]
    assert "done from result" in data["toolResultFromResult"]
    assert "message-callout is-warning" in data["calloutFromMessage"]
    assert "注意" in data["calloutFromMessage"]
    assert "需要确认" in data["calloutFromMessage"]
    assert "hello from value" in data["markdownFromValue"]
    assert "print(1)" in data["blankCodeContentFallsBackToText"]
    assert "print(1)" in data["blankCodeFieldFallsBackToText"]
    assert "print(1)" in data["codeOnly"]
    assert "print(1)" in data["renderCodeFromCodeField"]
    assert "language-python" in data["renderCodeFromCodeField"]
    assert "Copy" in data["renderCodeFromCodeField"]
    assert "x = 1" in data["renderCodeBlankContentFallback"]
    assert "Heads up" in data["calloutFromEnglishMessage"]
    assert "(empty response)" in data["bodylessBlocksFallbackPlaceholder"]


def test_chat_ui_runtime_event_helpers_behavior():
    """Behavior-level coverage for runtime event normalization and completion states."""
    node_bin = shutil.which("node")
    if not node_bin:
        pytest.skip("node is not installed; skipping JS helper behavior test")

    js_file = _chat_ui_js_source()
    normalize_block = _extract_js_helper_block(js_file, "normalizeRuntimeEvent")
    completion_block = _extract_js_helper_block(js_file, "completionRuntimeState")

    script = f"""
{completion_block}
{normalize_block}

const legacy = normalizeRuntimeEvent({{
  type: "tool_result",
  data: {{ tool: "search", message: "done" }},
  ts: 123,
}});

const normalized = normalizeRuntimeEvent({{
  event_type: "tool_result",
  state: "running",
  session_id: "s1",
  request_id: "r1",
  agent_id: "a1",
  summary: "Tool completed",
  detail_payload: {{ tool: "search" }},
  created_at: "2026-04-04T00:00:00Z",
}});

const precedence = normalizeRuntimeEvent({{
  type: "legacy_type",
  event_type: "normalized_type",
}});

const wrapped = normalizeRuntimeEvent({{
  event: {{
    event_type: "llm_thinking",
    summary: "Reasoning",
    created_at: "2026-04-04T00:00:00Z",
  }}
}});

const zeroTs = normalizeRuntimeEvent({{ type: "tool_result", ts: 0, data: {{}} }});
const zeroStringTs = normalizeRuntimeEvent({{ type: "tool_result", ts: "0", data: {{}} }});
const legacyComplete = normalizeRuntimeEvent({{ type: "complete", data: {{ response: "ok" }} }});
const completionState = normalizeRuntimeEvent({{ event_type: "tool_result", state: "completed", data: {{ tool: "search" }} }});
const failedState = normalizeRuntimeEvent({{ event_type: "tool_result", state: "failed", data: {{ error: "boom" }} }});
const failedResult = normalizeRuntimeEvent({{ event_type: "tool_result", detail_payload: {{ success: false, error: "tool failed" }} }});

const result = {{
  legacy,
  normalized,
  precedence,
  wrapped,
  zeroTs,
  zeroStringTs,
  legacyComplete,
  completionState,
  failedState,
  failedResult,
  invalid: [normalizeRuntimeEvent(null), normalizeRuntimeEvent({{}}), normalizeRuntimeEvent({{foo: "bar"}})],
  completionStates: [
    isCompletionRuntimeState("complete"),
    isCompletionRuntimeState("completed"),
    isCompletionRuntimeState("done"),
    isCompletionRuntimeState("finished"),
    isCompletionRuntimeState("running"),
    isCompletionRuntimeState(""),
    isCompletionRuntimeState(null),
  ]
}};
console.log(JSON.stringify(result));
"""

    completed = subprocess.run(
        [node_bin, "-e", script],
        capture_output=True,
        text=True,
        check=True,
    )
    data = json.loads(completed.stdout)

    legacy = data["legacy"]
    assert legacy["type"] == "tool_result"
    assert legacy["data"]["tool"] == "search"
    assert legacy["data"]["message"] == "done"
    assert legacy["ts"] == 123
    assert legacy.get("state", "") == ""

    normalized = data["normalized"]
    assert normalized["type"] == "tool_result"
    assert normalized["data"]["tool"] == "search"
    assert normalized["data"]["message"] == "Tool completed"
    assert normalized["data"]["request_id"] == "r1"
    assert normalized["data"]["session_id"] == "s1"
    assert normalized["data"]["agent_id"] == "a1"
    assert normalized["request_id"] == "r1"
    assert normalized["session_id"] == "s1"
    assert normalized["agent_id"] == "a1"
    assert normalized["state"] == "running"
    assert isinstance(normalized["ts"], (int, float))
    assert data["precedence"]["type"] == "normalized_type"

    wrapped = data["wrapped"]
    assert wrapped["type"] == "llm_thinking"
    assert wrapped["data"]["message"] == "Reasoning"

    assert data["invalid"] == [None, None, None]
    assert data["completionStates"] == [True, True, True, True, False, False, False]


def test_update_agent_session_is_isolated_per_agent():
    node_bin = shutil.which("node")
    if not node_bin:
        pytest.skip("node is not installed; skipping JS helper behavior test")

    js_file = _chat_ui_js_source()
    create_state = _extract_js_function(js_file, "createDefaultChatState")
    ensure_state = _extract_js_function(js_file, "ensureChatState")
    update_session = _extract_js_function(js_file, "updateAgentSession")

    script = f"""
const state = {{
  selectedAgentId: "agent-B",
  chatStatesByAgent: new Map(),
  agentSessionIds: new Map(),
}};
function setLastSessionId() {{}}
function syncHiddenSessionInputFromState() {{}}
function ensureEventSocketForSelectedAgent() {{}}
{create_state}
{ensure_state}
{update_session}
updateAgentSession("agent-A", "s-a");
updateAgentSession("agent-B", "s-b");
updateAgentSession("agent-A", "s-a-2");
console.log(JSON.stringify({{
  a: ensureChatState("agent-A").sessionId,
  b: ensureChatState("agent-B").sessionId,
  mapA: state.agentSessionIds.get("agent-A"),
  mapB: state.agentSessionIds.get("agent-B"),
}}));
"""
    completed = subprocess.run([node_bin, "-e", script], capture_output=True, text=True, check=True)
    data = json.loads(completed.stdout)
    assert data["a"] == "s-a-2"
    assert data["b"] == "s-b"
    assert data["mapA"] == "s-a-2"
    assert data["mapB"] == "s-b"


def test_chat_ui_set_active_nav_section_loads_cached_bundles_without_refreshing():
    node_bin = shutil.which("node")
    if not node_bin:
        pytest.skip("node is not installed; skipping JS helper behavior test")

    js_file = _chat_ui_js_source()
    set_active_nav_section_fn = _extract_js_function(js_file, "setActiveNavSection")

    script = f"""
{set_active_nav_section_fn}

function noop() {{}}
function makeToggleObj() {{
  return {{
    classList: {{
      toggle: noop,
    }},
  }};
}}

const dom = {{
  railAssistantsBtn: makeToggleObj(),
  bundlesMenuBtn: makeToggleObj(),
  tasksMenuBtn: makeToggleObj(),
  assistantsNavSection: makeToggleObj(),
  bundlesNavSection: makeToggleObj(),
  tasksNavSection: makeToggleObj(),
  workspaceDetailContent: {{
    dataset: {{
      workspaceState: "idle",
    }},
  }},
}};

let bundleRefreshCount = 0;
let bundleCacheLoadCount = 0;
let taskRefreshCount = 0;
let bundleCacheResult = {{ hasCache: true, hasItems: true }};
let placeholderMessages = [];
const state = {{}};

function applySecondaryPaneState() {{}}
function renderSecondaryPaneHeader() {{}}
function syncMainHeader() {{}}
function showAssistantDefaultMainView() {{
  dom.workspaceDetailContent.dataset.workspaceState = "assistant-default";
}}
function showBundlesLoadingMainView() {{
  dom.workspaceDetailContent.dataset.workspaceState = "bundles-loading";
}}
function showTasksLoadingMainView() {{
  dom.workspaceDetailContent.dataset.workspaceState = "tasks-loading";
}}
function showBundlesDefaultMainView() {{
  dom.workspaceDetailContent.dataset.workspaceState = "bundles-default";
}}
function showBundlesEmptyMainView() {{
  renderWorkspaceDetailPlaceholder(
    "No bundles found. Click refresh to check again or create a bundle.",
    "bundles-placeholder"
  );
}}
function showTasksDefaultMainView() {{
  dom.workspaceDetailContent.dataset.workspaceState = "tasks-default";
}}
async function refreshRequirementBundles() {{
  bundleRefreshCount += 1;
}}
function renderRequirementBundleList() {{}}
function renderWorkspaceDetailPlaceholder(message, workspaceState) {{
  placeholderMessages.push(message);
  dom.workspaceDetailContent.dataset.workspaceState = workspaceState || "bundles-placeholder";
}}
function loadRequirementBundlesFromCache() {{
  bundleCacheLoadCount += 1;
  return bundleCacheResult;
}}
async function refreshMyTasks() {{
  taskRefreshCount += 1;
}}

async function runScenarioA() {{
  bundleRefreshCount = 0;
  bundleCacheLoadCount = 0;
  taskRefreshCount = 0;
  placeholderMessages = [];
  bundleCacheResult = {{ hasCache: true, hasItems: true }};
  Object.assign(state, {{
    activeNavSection: "bundles",
    secondaryPaneCollapsed: false,
    selectedBundleKey: "bundle-1",
    selectedTaskId: null,
  }});
  dom.workspaceDetailContent.dataset.workspaceState = "bundle-detail";
  await setActiveNavSection("bundles", {{ toggleIfSame: false }});
  return {{
    bundleRefreshCount,
    bundleCacheLoadCount,
    activeNavSection: state.activeNavSection,
    workspaceState: dom.workspaceDetailContent.dataset.workspaceState,
  }};
}}

async function runScenarioB() {{
  bundleRefreshCount = 0;
  bundleCacheLoadCount = 0;
  taskRefreshCount = 0;
  placeholderMessages = [];
  bundleCacheResult = {{ hasCache: true, hasItems: true }};
  Object.assign(state, {{
    activeNavSection: "assistants",
    secondaryPaneCollapsed: false,
    selectedBundleKey: null,
    selectedTaskId: null,
  }});
  dom.workspaceDetailContent.dataset.workspaceState = "assistant-default";
  await setActiveNavSection("bundles", {{ toggleIfSame: false }});
  return {{
    bundleRefreshCount,
    bundleCacheLoadCount,
    activeNavSection: state.activeNavSection,
  }};
}}

async function runScenarioC() {{
  bundleRefreshCount = 0;
  bundleCacheLoadCount = 0;
  taskRefreshCount = 0;
  placeholderMessages = [];
  bundleCacheResult = {{ hasCache: true, hasItems: true }};
  Object.assign(state, {{
    activeNavSection: "bundles",
    secondaryPaneCollapsed: true,
    selectedBundleKey: null,
    selectedTaskId: null,
  }});
  dom.workspaceDetailContent.dataset.workspaceState = "bundle-detail";
  await setActiveNavSection("bundles");
  return {{
    bundleRefreshCount,
    bundleCacheLoadCount,
    secondaryPaneCollapsed: state.secondaryPaneCollapsed,
  }};
}}

async function runScenarioD() {{
  bundleRefreshCount = 0;
  bundleCacheLoadCount = 0;
  taskRefreshCount = 0;
  placeholderMessages = [];
  bundleCacheResult = {{ hasCache: true, hasItems: true }};
  Object.assign(state, {{
    activeNavSection: "tasks",
    secondaryPaneCollapsed: false,
    selectedBundleKey: null,
    selectedTaskId: "task-1",
  }});
  dom.workspaceDetailContent.dataset.workspaceState = "task-detail";
  await setActiveNavSection("tasks", {{ toggleIfSame: false }});
  return {{
    taskRefreshCount,
    bundleCacheLoadCount,
    activeNavSection: state.activeNavSection,
    workspaceState: dom.workspaceDetailContent.dataset.workspaceState,
  }};
}}

async function runScenarioE() {{
  bundleRefreshCount = 0;
  bundleCacheLoadCount = 0;
  taskRefreshCount = 0;
  placeholderMessages = [];
  bundleCacheResult = {{ hasCache: true, hasItems: false }};
  Object.assign(state, {{
    activeNavSection: "assistants",
    secondaryPaneCollapsed: false,
    selectedBundleKey: null,
    selectedTaskId: null,
  }});
  dom.workspaceDetailContent.dataset.workspaceState = "assistant-default";
  await setActiveNavSection("bundles", {{ toggleIfSame: false }});
  return {{
    bundleRefreshCount,
    bundleCacheLoadCount,
    workspaceState: dom.workspaceDetailContent.dataset.workspaceState,
    lastPlaceholder: placeholderMessages[placeholderMessages.length - 1] || "",
  }};
}}

async function runScenarioF() {{
  bundleRefreshCount = 0;
  bundleCacheLoadCount = 0;
  taskRefreshCount = 0;
  placeholderMessages = [];
  bundleCacheResult = {{ hasCache: true, hasItems: true }};
  Object.assign(state, {{
    activeNavSection: "assistants",
    secondaryPaneCollapsed: true,
    selectedBundleKey: null,
    selectedTaskId: null,
  }});
  await setActiveNavSection("assistants", {{ toggleIfSame: false, preserveCollapsed: true }});
  return {{
    secondaryPaneCollapsed: state.secondaryPaneCollapsed,
  }};
}}

(async () => {{
  const result = {{
    scenarioA: await runScenarioA(),
    scenarioB: await runScenarioB(),
    scenarioC: await runScenarioC(),
    scenarioD: await runScenarioD(),
    scenarioE: await runScenarioE(),
    scenarioF: await runScenarioF(),
  }};
  console.log(JSON.stringify(result));
}})().catch((error) => {{
  console.error(error);
  process.exit(1);
}});
"""

    completed = subprocess.run(
        [node_bin, "-e", script],
        capture_output=True,
        text=True,
        check=True,
    )
    data = json.loads(completed.stdout)

    assert data["scenarioA"]["bundleRefreshCount"] == 0
    assert data["scenarioA"]["bundleCacheLoadCount"] == 0
    assert data["scenarioA"]["activeNavSection"] == "bundles"
    assert data["scenarioA"]["workspaceState"] == "bundle-detail"

    assert data["scenarioB"]["bundleRefreshCount"] == 0
    assert data["scenarioB"]["bundleCacheLoadCount"] == 1
    assert data["scenarioB"]["activeNavSection"] == "bundles"

    assert data["scenarioC"]["bundleRefreshCount"] == 0
    assert data["scenarioC"]["bundleCacheLoadCount"] == 1
    assert data["scenarioC"]["secondaryPaneCollapsed"] is False

    assert data["scenarioD"]["taskRefreshCount"] == 0
    assert data["scenarioD"]["bundleCacheLoadCount"] == 0
    assert data["scenarioD"]["activeNavSection"] == "tasks"
    assert data["scenarioD"]["workspaceState"] == "task-detail"

    assert data["scenarioE"]["bundleRefreshCount"] == 0
    assert data["scenarioE"]["bundleCacheLoadCount"] == 1
    assert data["scenarioE"]["workspaceState"] == "bundles-placeholder"
    assert "No bundles found" in data["scenarioE"]["lastPlaceholder"]
    assert "No cached bundles yet" not in data["scenarioE"]["lastPlaceholder"]
    assert data["scenarioF"]["secondaryPaneCollapsed"] is True


def test_chat_ui_set_active_nav_section_runtime_profiles_prefers_default_and_empty_placeholder():
    node_bin = shutil.which("node")
    if not node_bin:
        pytest.skip("node is not installed; skipping JS helper behavior test")

    js_file = _chat_ui_js_source()
    set_active_nav_section_fn = _extract_js_function(js_file, "setActiveNavSection")
    load_runtime_profile_panel_content_fn = _extract_js_function(js_file, "loadRuntimeProfilePanelContent")

    script = f"""
{load_runtime_profile_panel_content_fn}
{set_active_nav_section_fn}

function noop() {{}}
function makeToggleObj() {{
  return {{
    classList: {{
      toggle: noop,
    }},
  }};
}}

const dom = {{
  railAssistantsBtn: makeToggleObj(),
  bundlesMenuBtn: makeToggleObj(),
  tasksMenuBtn: makeToggleObj(),
  runtimeProfilesMenuBtn: makeToggleObj(),
  assistantsNavSection: makeToggleObj(),
  bundlesNavSection: makeToggleObj(),
  tasksNavSection: makeToggleObj(),
  runtimeProfilesNavSection: makeToggleObj(),
  workspaceDetailContent: {{
    dataset: {{
      workspaceState: "idle",
    }},
  }},
}};

const state = {{}};
let renderedProfileListCount = 0;
let refreshedProfileCount = 0;
let loadedProfileIds = [];
let placeholderMessages = [];

function applySecondaryPaneState() {{}}
function renderSecondaryPaneHeader() {{}}
function syncMainHeader() {{}}
function showAssistantDefaultMainView() {{
  dom.workspaceDetailContent.dataset.workspaceState = "assistant-default";
}}
function showBundlesLoadingMainView() {{}}
function showTasksLoadingMainView() {{}}
function loadRequirementBundlesFromCache() {{
  return {{ hasCache: true, hasItems: true }};
}}
function renderRequirementBundleList() {{}}
function showBundlesDefaultMainView() {{}}
function showBundlesEmptyMainView() {{}}
function showTasksDefaultMainView() {{}}
async function refreshMyTasks() {{}}
async function htmxAjax(_method, url) {{
  loadedProfileIds.push(url.split("/")[3]);
}}
const htmx = {{ ajax: htmxAjax }};
function setMainView(_view) {{}}
function renderRuntimeProfileList() {{
  renderedProfileListCount += 1;
}}
function renderWorkspaceDetailPlaceholder(message, workspaceState) {{
  placeholderMessages.push(message);
  dom.workspaceDetailContent.dataset.workspaceState = workspaceState || "runtime-profiles-placeholder";
}}
async function refreshRuntimeProfileList() {{
  refreshedProfileCount += 1;
  renderRuntimeProfileList();
}}

async function runWithProfiles() {{
  renderedProfileListCount = 0;
  refreshedProfileCount = 0;
  loadedProfileIds = [];
  placeholderMessages = [];
  Object.assign(state, {{
    activeNavSection: "assistants",
    secondaryPaneCollapsed: false,
    selectedRuntimeProfileId: "custom-1",
    runtimeProfiles: [
      {{ id: "reviewer-2", name: "Reviewer", is_default: false, revision: 1 }},
      {{ id: "default-1", name: "Default", is_default: true, revision: 3 }},
    ],
  }});
  await setActiveNavSection("runtime-profiles", {{ toggleIfSame: false }});
  return {{
    selectedRuntimeProfileId: state.selectedRuntimeProfileId,
    loadedProfileIds,
    renderedProfileListCount,
    refreshedProfileCount,
    workspaceState: dom.workspaceDetailContent.dataset.workspaceState,
    placeholderMessages,
  }};
}}

async function runEmptyProfiles() {{
  renderedProfileListCount = 0;
  refreshedProfileCount = 0;
  loadedProfileIds = [];
  placeholderMessages = [];
  Object.assign(state, {{
    activeNavSection: "assistants",
    secondaryPaneCollapsed: false,
    selectedRuntimeProfileId: null,
    runtimeProfiles: [],
  }});
  await setActiveNavSection("runtime-profiles", {{ toggleIfSame: false }});
  return {{
    selectedRuntimeProfileId: state.selectedRuntimeProfileId,
    loadedProfileIds,
    renderedProfileListCount,
    refreshedProfileCount,
    workspaceState: dom.workspaceDetailContent.dataset.workspaceState,
    placeholderMessages,
  }};
}}

(async () => {{
  const result = {{
    withProfiles: await runWithProfiles(),
    emptyProfiles: await runEmptyProfiles(),
  }};
  console.log(JSON.stringify(result));
}})().catch((error) => {{
  console.error(error);
  process.exit(1);
}});
"""

    completed = subprocess.run(
        [node_bin, "-e", script],
        capture_output=True,
        text=True,
        check=True,
    )
    data = json.loads(completed.stdout)

    assert data["withProfiles"]["selectedRuntimeProfileId"] == "default-1"
    assert data["withProfiles"]["loadedProfileIds"] == ["default-1"]
    assert data["withProfiles"]["refreshedProfileCount"] == 1
    assert data["withProfiles"]["workspaceState"] == "runtime-profile-detail"
    assert data["withProfiles"]["placeholderMessages"] in ([], ["Loading runtime profiles…"])

    assert data["emptyProfiles"]["selectedRuntimeProfileId"] is None
    assert data["emptyProfiles"]["loadedProfileIds"] == []
    assert data["emptyProfiles"]["workspaceState"] == "runtime-profiles-placeholder"
    assert any("No runtime profiles found." in msg for msg in data["emptyProfiles"]["placeholderMessages"])


def test_chat_ui_runtime_profiles_reopen_prefers_default_profile():
    node_bin = shutil.which("node")
    if not node_bin:
        pytest.skip("node is not installed; skipping JS helper behavior test")

    js_file = _chat_ui_js_source()
    set_active_nav_section_fn = _extract_js_function(js_file, "setActiveNavSection")
    load_runtime_profile_panel_content_fn = _extract_js_function(js_file, "loadRuntimeProfilePanelContent")

    script = f"""
{load_runtime_profile_panel_content_fn}
{set_active_nav_section_fn}

function noop() {{}}
function makeToggleObj() {{
  return {{
    classList: {{
      toggle: noop,
    }},
  }};
}}

const dom = {{
  railAssistantsBtn: makeToggleObj(),
  bundlesMenuBtn: makeToggleObj(),
  tasksMenuBtn: makeToggleObj(),
  runtimeProfilesMenuBtn: makeToggleObj(),
  assistantsNavSection: makeToggleObj(),
  bundlesNavSection: makeToggleObj(),
  tasksNavSection: makeToggleObj(),
  runtimeProfilesNavSection: makeToggleObj(),
  workspaceDetailContent: {{
    dataset: {{
      workspaceState: "idle",
    }},
  }},
}};

const state = {{
  activeNavSection: "runtime-profiles",
  secondaryPaneCollapsed: false,
  selectedRuntimeProfileId: "custom-1",
  runtimeProfiles: [
    {{ id: "reviewer-2", name: "Reviewer", is_default: false, revision: 1 }},
    {{ id: "default-1", name: "Default", is_default: true, revision: 3 }},
  ],
}};
let loadedProfileIds = [];

function applySecondaryPaneState() {{}}
function renderSecondaryPaneHeader() {{}}
function syncMainHeader() {{}}
function showAssistantDefaultMainView() {{}}
function showBundlesLoadingMainView() {{}}
function showTasksLoadingMainView() {{}}
function loadRequirementBundlesFromCache() {{
  return {{ hasCache: true, hasItems: true }};
}}
function renderRequirementBundleList() {{}}
function showBundlesDefaultMainView() {{}}
function showBundlesEmptyMainView() {{}}
function showTasksDefaultMainView() {{}}
async function refreshMyTasks() {{}}
function renderRuntimeProfileList() {{}}
function renderWorkspaceDetailPlaceholder(_message, workspaceState) {{
  dom.workspaceDetailContent.dataset.workspaceState = workspaceState || "runtime-profiles-placeholder";
}}
async function refreshRuntimeProfileList() {{}}
const htmx = {{
  ajax: async function(_method, url) {{
    loadedProfileIds.push(url.split("/")[3]);
  }}
}};
function setMainView(_view) {{}}

async function run() {{
  loadedProfileIds = [];
  await setActiveNavSection("runtime-profiles");
  const afterCollapse = {{
    secondaryPaneCollapsed: state.secondaryPaneCollapsed,
    loadedProfileIds: [...loadedProfileIds],
  }};

  loadedProfileIds = [];
  await setActiveNavSection("runtime-profiles");
  const afterReopen = {{
    secondaryPaneCollapsed: state.secondaryPaneCollapsed,
    selectedRuntimeProfileId: state.selectedRuntimeProfileId,
    loadedProfileIds: [...loadedProfileIds],
    workspaceState: dom.workspaceDetailContent.dataset.workspaceState,
  }};

  console.log(JSON.stringify({{ afterCollapse, afterReopen }}));
}}

run().catch((error) => {{
  console.error(error);
  process.exit(1);
}});
"""

    completed = subprocess.run(
        [node_bin, "-e", script],
        capture_output=True,
        text=True,
        check=True,
    )
    data = json.loads(completed.stdout)

    assert data["afterCollapse"]["secondaryPaneCollapsed"] is True
    assert data["afterCollapse"]["loadedProfileIds"] == []

    assert data["afterReopen"]["secondaryPaneCollapsed"] is False
    assert data["afterReopen"]["selectedRuntimeProfileId"] == "default-1"
    assert data["afterReopen"]["loadedProfileIds"] == ["default-1"]
    assert data["afterReopen"]["workspaceState"] == "runtime-profile-detail"


def test_chat_ui_refresh_requirement_bundles_treats_empty_cached_list_as_cache():
    node_bin = shutil.which("node")
    if not node_bin:
        pytest.skip("node is not installed; skipping JS helper behavior test")

    js_file = _chat_ui_js_source()
    show_bundles_empty_main_view_fn = _extract_js_function(js_file, "showBundlesEmptyMainView")
    refresh_requirement_bundles_fn = _extract_js_function(js_file, "refreshRequirementBundles")

    script = f"""
{show_bundles_empty_main_view_fn}
{refresh_requirement_bundles_fn}

const dom = {{
  bundleNavList: {{ innerHTML: '<div class="portal-bundle-list-state">No bundles found</div>' }},
  refreshBundlesBtn: {{ id: "refresh-bundles-btn" }},
}};

const state = {{
  requirementBundles: [],
  hasRequirementBundlesCache: true,
  activeNavSection: "bundles",
  secondaryPaneCollapsed: false,
  selectedBundleKey: null,
}};

let apiMode = "fail";
let buttonCalls = [];
let toastMessages = [];
let renderCalls = [];
let placeholderMessages = [];
let setRequirementBundlesCalls = [];

function setButtonDisabled(_button, disabled, label = null) {{
  buttonCalls.push({{ disabled, label }});
}}

async function api() {{
  if (apiMode === "fail") {{
    throw new Error("network down");
  }}
  return [];
}}

function setRequirementBundles(items, {{ persist = true, hasCache = true }} = {{}}) {{
  setRequirementBundlesCalls.push({{ items, persist, hasCache }});
  state.requirementBundles = Array.isArray(items) ? items : [];
  state.hasRequirementBundlesCache = !!hasCache;
}}

function renderRequirementBundleList(errorMessage = "") {{
  renderCalls.push(errorMessage);
}}

function showBundlesDefaultMainView() {{
  placeholderMessages.push("DEFAULT");
}}

function renderWorkspaceDetailPlaceholder(message, workspaceState) {{
  placeholderMessages.push(message);
}}

function syncMainHeader() {{}}

function showToast(message) {{
  toastMessages.push(message);
}}

async function runFailureScenario() {{
  buttonCalls = [];
  toastMessages = [];
  renderCalls = [];
  placeholderMessages = [];
  setRequirementBundlesCalls = [];
  state.requirementBundles = [];
  state.hasRequirementBundlesCache = true;
  dom.bundleNavList.innerHTML = '<div class="portal-bundle-list-state">No bundles found</div>';
  apiMode = "fail";

  await refreshRequirementBundles({{ showLoadingState: true, force: true, notifyOnSuccess: false }});

  return {{
    listHtml: dom.bundleNavList.innerHTML,
    renderCalls,
    toastMessages,
    buttonCalls,
    placeholderMessages,
  }};
}}

async function runSuccessScenario() {{
  buttonCalls = [];
  toastMessages = [];
  renderCalls = [];
  placeholderMessages = [];
  setRequirementBundlesCalls = [];
  state.requirementBundles = [];
  state.hasRequirementBundlesCache = true;
  apiMode = "success-empty";

  await refreshRequirementBundles({{ showLoadingState: true, force: true, notifyOnSuccess: true }});

  return {{
    hasRequirementBundlesCache: state.hasRequirementBundlesCache,
    requirementBundlesLength: state.requirementBundles.length,
    renderCalls,
    toastMessages,
    buttonCalls,
    placeholderMessages,
    setRequirementBundlesCalls,
  }};
}}

(async () => {{
  const result = {{
    failure: await runFailureScenario(),
    success: await runSuccessScenario(),
  }};
  console.log(JSON.stringify(result));
}})().catch((error) => {{
  console.error(error);
  process.exit(1);
}});
"""

    completed = subprocess.run(
        [node_bin, "-e", script],
        capture_output=True,
        text=True,
        check=True,
    )
    data = json.loads(completed.stdout)

    assert "Loading bundles…" not in data["failure"]["listHtml"]
    assert all(not call.startswith("Failed to load bundles:") for call in data["failure"]["renderCalls"] if isinstance(call, str))
    assert any(msg == "Failed to refresh bundles: network down" for msg in data["failure"]["toastMessages"])
    assert data["failure"]["buttonCalls"] == [
        {"disabled": True, "label": "Refreshing bundles..."},
        {"disabled": False, "label": None},
    ]

    assert data["success"]["hasRequirementBundlesCache"] is True
    assert data["success"]["requirementBundlesLength"] == 0
    assert data["success"]["setRequirementBundlesCalls"][0]["hasCache"] is True
    assert "No bundles found. Click refresh to check again or create a bundle." in data["success"]["placeholderMessages"]
    assert all("No cached bundles yet" not in msg for msg in data["success"]["placeholderMessages"])
    assert "Bundles refreshed" in data["success"]["toastMessages"]


def test_thinking_process_template_prefers_normalized_fields():
    template = Path("app/templates/partials/thinking_process_panel.html").read_text(encoding="utf-8")
    assert "event.get('type')" in template or "event.event_type or event.type" in template
    if "event.summary" in template and "event.data and event.data.message" in template:
        assert template.find("event.summary") < template.find("event.data and event.data.message")


def test_copilot_auth_no_runtime_proxy_strings():
    js = _chat_ui_js_source()
    assert "/a/${agentId}/api/copilot/auth/start" not in js
    assert "/a/${agentId}/api/copilot/auth/check" not in js


def test_start_copilot_auth_uses_portal_endpoints_and_stops_on_declined():
    node_bin = shutil.which("node")
    if not node_bin:
        pytest.skip("node is not installed; skipping copilot auth behavior test")

    js_file = _chat_ui_js_source()
    get_state_fn = _extract_js_function(js_file, "getManagedCopilotState")
    stop_polling_fn = _extract_js_function(js_file, "stopCopilotPolling")
    get_auth_base_fn = _extract_js_function(js_file, "getManagedCopilotAuthBase")
    get_github_base_fn = _extract_js_function(js_file, "getManagedGithubBaseUrl")
    finish_fn = _extract_js_function(js_file, "finishCopilotAuthWithMessage")
    start_copilot_fn = _extract_js_function(js_file, "startCopilotAuth")

    script = f"""
const fetchCalls = [];
const intervalCalls = [];
const clearedIntervals = [];
const toasts = [];

let intervalId = 1;
function setIntervalStub(fn, ms) {{
  const id = intervalId++;
  intervalCalls.push({{ id, ms, fn }});
  return id;
}}
function clearIntervalStub(id) {{
  clearedIntervals.push(id);
}}

global.setInterval = setIntervalStub;
global.clearInterval = clearIntervalStub;

function safe(v) {{ return String(v || ""); }}
function showToast(msg) {{ toasts.push(msg); }}

const classes = () => ({{ add() {{}}, remove() {{}}, toggle() {{}} }});
const elements = {{
  authStatus: {{ classList: classes() }},
  instructions: {{ classList: classes() }},
  statusText: {{ textContent: "" }},
  verifyLink: {{ href: "", textContent: "" }},
  deviceLink: {{ href: "", classList: classes() }},
  userCode: {{ textContent: "" }},
  timer: {{ textContent: "" }},
  apiKey: {{ value: "" }},
  githubBase: {{ value: "https://github.company.com" }},
}};

const root = {{
  dataset: {{ copilotAuthBase: "/api/copilot/auth" }},
  querySelector(sel) {{
    if (sel === '#copilot_auth_status') return elements.authStatus;
    if (sel === '#copilot_instructions') return elements.instructions;
    if (sel === '#copilot_status_text') return elements.statusText;
    if (sel === '#copilot_verify_link') return elements.verifyLink;
    if (sel === '#copilot_device_link') return elements.deviceLink;
    if (sel === '#copilot_user_code') return elements.userCode;
    if (sel === '#copilot_timer') return elements.timer;
    if (sel === 'input[name="llm_api_key"]') return elements.apiKey;
    if (sel === 'input[name="github_base_url"]') return elements.githubBase;
    return null;
  }}
}};

async function fetch(url, options) {{
  fetchCalls.push({{ url, options }});
  if (url.endsWith('/start')) {{
    return {{
      ok: true,
      async json() {{
        return {{
          auth_id: 'auth-1',
          device_code: 'device-1',
          user_code: 'CODE1',
          verification_url: 'https://github.com/login/device',
          verification_complete_url: 'https://github.com/login/device?user_code=CODE1',
          expires_in: 60,
          interval: 7,
        }};
      }}
    }};
  }}
  if (url.endsWith('/check')) {{
    return {{ ok: true, async json() {{ return {{ status: 'declined', message: 'nope' }}; }} }};
  }}
  throw new Error('unexpected url');
}}

global.fetch = fetch;

{get_state_fn}
{stop_polling_fn}
{get_auth_base_fn}
{get_github_base_fn}
{finish_fn}
{start_copilot_fn}

(async () => {{
  await startCopilotAuth(root);
  await intervalCalls.find((x) => x.ms === 7000).fn();

  console.log(JSON.stringify({{
    fetchCalls,
    intervalMs: intervalCalls.map((x) => x.ms),
    clearedIntervals,
    toasts,
    statusText: elements.statusText.textContent,
    startBody: JSON.parse(fetchCalls[0].options.body),
  }}));
}})().catch((error) => {{
  console.error(error);
  process.exit(1);
}});
"""

    completed = subprocess.run([node_bin, "-e", script], capture_output=True, text=True, check=True)
    data = json.loads(completed.stdout)

    assert data["fetchCalls"][0]["url"] == "/api/copilot/auth/start"
    assert data["startBody"]["github_base_url"] == "https://github.company.com"
    assert 7000 in data["intervalMs"]
    assert data["fetchCalls"][1]["url"] == "/api/copilot/auth/check"
    assert data["statusText"] == "nope"
    assert len(data["clearedIntervals"]) >= 2


def test_start_copilot_auth_stops_on_check_http_error_or_missing_status():
    node_bin = shutil.which("node")
    if not node_bin:
        pytest.skip("node is not installed; skipping copilot auth behavior test")

    js_file = _chat_ui_js_source()
    get_state_fn = _extract_js_function(js_file, "getManagedCopilotState")
    stop_polling_fn = _extract_js_function(js_file, "stopCopilotPolling")
    get_auth_base_fn = _extract_js_function(js_file, "getManagedCopilotAuthBase")
    get_github_base_fn = _extract_js_function(js_file, "getManagedGithubBaseUrl")
    finish_fn = _extract_js_function(js_file, "finishCopilotAuthWithMessage")
    start_copilot_fn = _extract_js_function(js_file, "startCopilotAuth")

    script = f"""
async function runScenario(mode) {{
  const fetchCalls = [];
  const intervalCalls = [];
  const clearedIntervals = [];

  let intervalId = 1;
  function setIntervalStub(fn, ms) {{
    const id = intervalId++;
    intervalCalls.push({{ id, ms, fn }});
    return id;
  }}
  function clearIntervalStub(id) {{
    clearedIntervals.push(id);
  }}

  global.setInterval = setIntervalStub;
  global.clearInterval = clearIntervalStub;

  function safe(v) {{ return String(v || ""); }}
  function showToast(_msg) {{}}

  const classes = () => ({{ add() {{}}, remove() {{}}, toggle() {{}} }});
  const elements = {{
    authStatus: {{ classList: classes() }},
    instructions: {{ classList: classes() }},
    statusText: {{ textContent: "" }},
    verifyLink: {{ href: "", textContent: "" }},
    deviceLink: {{ href: "", classList: classes() }},
    userCode: {{ textContent: "" }},
    timer: {{ textContent: "" }},
    apiKey: {{ value: "" }},
    githubBase: {{ value: "https://github.company.com" }},
  }};

  const root = {{
    dataset: {{ copilotAuthBase: "/api/copilot/auth" }},
    querySelector(sel) {{
      if (sel === '#copilot_auth_status') return elements.authStatus;
      if (sel === '#copilot_instructions') return elements.instructions;
      if (sel === '#copilot_status_text') return elements.statusText;
      if (sel === '#copilot_verify_link') return elements.verifyLink;
      if (sel === '#copilot_device_link') return elements.deviceLink;
      if (sel === '#copilot_user_code') return elements.userCode;
      if (sel === '#copilot_timer') return elements.timer;
      if (sel === 'input[name="llm_api_key"]') return elements.apiKey;
      if (sel === 'input[name="github_base_url"]') return elements.githubBase;
      return null;
    }}
  }};

  async function fetch(url, options) {{
    fetchCalls.push({{ url, options }});
    if (url.endsWith('/start')) {{
      return {{
        ok: true,
        async json() {{
          return {{
            auth_id: 'auth-1',
            device_code: 'device-1',
            user_code: 'CODE1',
            verification_url: 'https://github.com/login/device',
            verification_complete_url: 'https://github.com/login/device?user_code=CODE1',
            expires_in: 60,
            interval: 7,
          }};
        }}
      }};
    }}
    if (mode === 'http_error') {{
      return {{ ok: false, status: 404, async json() {{ return {{ error: 'Authorization not found or expired' }}; }} }};
    }}
    return {{ ok: true, status: 200, async json() {{ return {{ message: 'missing status from server' }}; }} }};
  }}

  global.fetch = fetch;

  {get_state_fn}
  {stop_polling_fn}
  {get_auth_base_fn}
  {get_github_base_fn}
  {finish_fn}
  {start_copilot_fn}

  await startCopilotAuth(root);
  await intervalCalls.find((x) => x.ms === 7000).fn();

  return {{
    fetchCalls,
    clearedIntervals,
    statusText: elements.statusText.textContent,
  }};
}}

(async () => {{
  const httpError = await runScenario('http_error');
  const missingStatus = await runScenario('missing_status');
  console.log(JSON.stringify({{ httpError, missingStatus }}));
}})().catch((error) => {{
  console.error(error);
  process.exit(1);
}});
"""

    completed = subprocess.run([node_bin, "-e", script], capture_output=True, text=True, check=True)
    data = json.loads(completed.stdout)

    assert len(data["httpError"]["clearedIntervals"]) >= 2
    assert data["httpError"]["statusText"] != "Waiting for authorization..."
    assert "Authorization not found or expired" in data["httpError"]["statusText"]

    assert len(data["missingStatus"]["clearedIntervals"]) >= 2
    assert data["missingStatus"]["statusText"] != "Waiting for authorization..."
    assert "missing status" in data["missingStatus"]["statusText"]


def test_thinking_process_advanced_debug_copy_controls_are_wired():
    template = Path("app/templates/partials/thinking_process_panel.html").read_text(encoding="utf-8")
    js = Path("app/static/js/chat_ui.js").read_text(encoding="utf-8")
    css = Path("app/static/css/app.css").read_text(encoding="utf-8")

    assert 'data-copy-debug-text="1"' in template
    assert 'data-copyable-text-block="1"' in template
    assert 'data-copy-source="1"' in template
    assert 'data-copy-label="System Prompt"' in template
    assert 'data-copy-label="Request Flow"' in template
    assert 'data-copy-label="Available Tools"' in template
    assert 'data-copy-label="Final Response"' in template

    assert 'target?.closest("[data-copy-debug-text]")' in js
    assert 'closest("[data-copyable-text-block]")' in js
    assert 'querySelector("[data-copy-source]")' in js
    assert "copyText(text)" in js
    assert "setDebugCopyButtonCopied" in js
    assert "Copied to clipboard" in js

    assert ".portal-copy-icon-btn" in css
    assert ".portal-copy-icon-btn.is-copied" in css
    assert ".portal-copyable-text-head" in css

def test_chat_stream_event_type_parsing_source_markers_present():
    js = _chat_ui_js_source()
    assert 'function getChatStreamEventType(eventName, data)' in js
    assert 'data.type || data.event_type || data.event' in js
    assert 'hasExplicitEvent = false' in js
    assert 'if (!dataLines.length && !hasExplicitEvent) return null;' in js
    assert 'const t = getChatStreamEventType(eventName, data);' in js
    assert "if (sawEvent && !sawFinal && !requestCtx.streamedText)" in js
    assert '/api/chat/stream' in js
    assert 'getReader()' in js
    assert 'TextDecoder()' in js
    assert '/api/chat' in js
    assert 'message.delta' in js


def test_skills_panel_badges_and_command_guard_source_markers_present():
    from pathlib import Path
    html = Path('app/templates/partials/skills_panel.html').read_text(encoding='utf-8')
    assert 'portal-status-badge' in html
    assert 'data-skill-command="/{{ normalized_skill_name }}"' in html
    assert 'Prompt-only in this runtime; Python skill.py is not executed.' in html
    py = Path('app/web.py').read_text(encoding='utf-8')
    assert 'def _normalize_permission_state(value) -> str:' in py
    assert 'def _normalize_runtime_compatibility(value) -> str:' in py
    assert 'disabled_reasons = []' in py


def test_chat_stream_sse_helpers_cover_final_string_and_nested_event_data():
    js = _chat_ui_js_source()
    assert "function getChatStreamTextPayload(data)" in js
    assert 'if (typeof data === "string") return data;' in js
    assert "function normalizeChatStreamEventData(data)" in js
    assert "Object.assign(normalized, normalized.data)" in js
    assert "const responseText = getChatStreamTextPayload(data) || requestCtx.streamedText || \"\"" in js
    assert "const eventData = normalizeChatStreamEventData(data)" in js
    assert "handleAgentEventMessage(JSON.stringify({" in js
    assert "return 'unsupported'" in js
    assert "if (sawEvent && !sawFinal && !requestCtx.streamedText)" in js


def test_skills_panel_template_behavior_for_disabled_and_enabled_skills():
    from app.web import templates
    tpl = templates.get_template("partials/skills_panel.html")
    html = tpl.render(
        {
            "skills": [
                {"name": "unsupported_skill", "description": "No opencode support", "capability_allowed": True, "permission_state": "allowed", "runtime_compatibility": "unsupported", "disabled": True, "disabled_reason": "Unsupported by this runtime", "prompt_only": False},
                {"name": "permission_denied", "description": "Denied", "capability_allowed": True, "permission_state": "denied", "runtime_compatibility": "full", "disabled": True, "disabled_reason": "Denied by runtime permission", "prompt_only": False},
                {"name": "prompt_only_skill", "description": "Prompt only", "capability_allowed": True, "permission_state": "ask", "runtime_compatibility": "prompt_only", "disabled": False, "disabled_reason": "", "prompt_only": True},
                {"name": "full_skill", "description": "Full support", "capability_allowed": True, "permission_state": "allowed", "runtime_compatibility": "full", "disabled": False, "disabled_reason": "", "prompt_only": False},
            ]
        }
    )
    assert "data-skill-command=\"/unsupported_skill\"" not in html
    assert "data-skill-command=\"/permission_denied\"" not in html
    assert "data-skill-command=\"/prompt_only_skill\"" in html
    assert "data-skill-command=\"/full_skill\"" in html
    assert "Prompt-only in this runtime; Python skill.py is not executed." in html
    assert "No opencode support" in html
    assert "portal-status-badge" in html
