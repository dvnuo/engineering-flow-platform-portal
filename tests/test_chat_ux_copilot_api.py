"""Edit on send, image thumbnails after a reload, the balance chip under token billing, the runtime's model catalog.

Static checks on the shipped source plus node runs of the pure helpers, in
the style of test_chat_model_badge_static.py and test_chat_usage_cost_line_node.py.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from _js_extract_helpers import _extract_js_function
from app.contracts.llm_catalog import (
    clear_catalog_models,
    coerce_to_provider_model,
    is_plausible_copilot_model_id,
    is_retired_copilot_model,
    is_selectable_model,
    register_catalog_models,
)
from app.services.runtime_profile_service import RuntimeProfileService
from test_chat_usage_cost_line_node import _bundle, _run_node
from test_copilot_credits_portal import _render_usage_panel

ROOT = Path(__file__).resolve().parents[1]


def _js() -> str:
    return (ROOT / "app" / "static" / "js" / "chat_ui.js").read_text(encoding="utf-8")


# --- 1. the user bubble gets its id without a reload --------------------------


def test_the_completion_handler_asks_the_session_for_the_id_when_the_answer_has_none():
    js = _js()
    handler = js[js.index("if (optimisticUserArticle && payload.user_message_id) {"):]
    handler = handler[: handler.index("if (requestCtx.usedStream === true) {")]
    assert "else if (optimisticUserArticle && finalSessionId)" in handler
    assert "reconcileOptimisticUserMessageId(agentIdAtSend, finalSessionId, optimisticUserArticle)" in handler
    body = _extract_js_function(js, "reconcileOptimisticUserMessageId")
    assert "/api/sessions/" in body
    assert "addEditButtonsToMessages()" in body


def test_reconcile_stamps_the_latest_user_message_id_and_redraws_the_actions():
    script = f"""
let fetched = [];
const agentApiFor = async (agentId, path) => {{ fetched.push(path); return {{ messages: [
  {{ role: "user", id: "u-1" }}, {{ role: "assistant", id: "a-1" }}, {{ role: "user", id: "u-9" }}, {{ role: "assistant", id: "a-9" }},
] }}; }};
let redraws = 0;
const addEditButtonsToMessages = () => {{ redraws += 1; }};
{_extract_js_function(_js(), "reconcileOptimisticUserMessageId")}
(async () => {{
  const assistantArticle = {{ dataset: {{}} }};
  const row = {{ nextElementSibling: {{ querySelector: () => assistantArticle }} }};
  const article = {{ dataset: {{ localUser: "1", optimisticUser: "1" }}, isConnected: true, closest: () => row }};
  const first = await reconcileOptimisticUserMessageId("agent-1", "s-1", article);
  const again = await reconcileOptimisticUserMessageId("agent-1", "s-1", article);
  const none = await reconcileOptimisticUserMessageId("", "s-1", article);
  console.log(JSON.stringify({{ first, again, none, dataset: article.dataset, assistant: assistantArticle.dataset, redraws, fetched }}));
}})();
"""
    result = _run_node(script)
    assert result["first"] == "u-9"
    assert result["again"] == "u-9"
    assert result["none"] is None
    assert result["dataset"] == {"localUser": "1", "messageId": "u-9"}
    assert result["assistant"] == {"userMessageId": "u-9"}
    # The id is written once; the second answer finds it in place.
    assert result["redraws"] == 1
    assert result["fetched"] == ["/api/sessions/s-1", "/api/sessions/s-1"]


# --- 2. stored images come back as thumbnails ---------------------------------


def test_history_images_are_drawn_from_the_file_route():
    js = _js()
    history = js[js.index("function renderChatHistory("):]
    history = history[: history.index("if (entry.type === \"question_answer\")")]
    assert 'const isImage = type === "image" || contentType.startsWith("image/");' in history
    assert "attachmentFileUrl(attachment)" in history
    assert "buildAttachmentThumbNode(attachment, thumbUrl" in history
    # The share page already draws stored images this way; the chat page now agrees.
    share = (ROOT / "app" / "static" / "js" / "share_view.js").read_text(encoding="utf-8")
    assert 'attachment.type === "image" && attachment.file_id' in share


def test_the_thumbnail_node_opens_the_lightbox_and_falls_back_to_the_chip():
    script = f"""
const made = [];
const document = {{ createElement: (tag) => {{ const el = {{ tagName: tag.toUpperCase(), dataset: {{}}, parentNode: null, replaced: null, replaceWith(node) {{ this.replaced = node; }} }}; made.push(el); return el; }} }};
const buildAttachmentChipNode = (attachment, name) => ({{ tagName: "CHIP", name, parentNode: null }});
let iconsDrawn = 0;
const renderIcons = () => {{ iconsDrawn += 1; }};
{_extract_js_function(_js(), "buildAttachmentThumbNode")}
const attachment = {{ file_id: "f-1", name: "shot.png", content_type: "image/png", type: "image" }};
const img = buildAttachmentThumbNode(attachment, "/a/agent-1/api/files/f-1?session_id=s-1", "shot.png");
const before = {{ src: img.src, alt: img.alt, loading: img.loading, className: img.className, dataset: {{ ...img.dataset }} }};
img.onerror();
console.log(JSON.stringify({{ before, replaced: img.replaced, iconsDrawn }}));
"""
    result = _run_node(script)
    assert result["before"] == {
        "src": "/a/agent-1/api/files/f-1?session_id=s-1",
        "alt": "shot.png",
        "loading": "lazy",
        "className": "message-attachment-thumb",
        "dataset": {
            "fileId": "f-1",
            "previewUrl": "/a/agent-1/api/files/f-1?session_id=s-1",
            "previewName": "shot.png",
            "isImage": "true",
        },
    }
    assert result["replaced"] == {"tagName": "CHIP", "name": "shot.png", "parentNode": None}
    assert result["iconsDrawn"] == 1


# --- 3. the balance chip under usage-based billing ----------------------------


def test_quota_chip_reads_pooled_allotments_exhaustion_and_the_live_header():
    script = f"""
{_bundle(_js())}
const pooled = copilotQuotaSummary({{ available: true, plan: "business", reset_date: "2026-12-01", token_based_billing: true,
  credits: {{ id: "premium_interactions", label: "AI credits", included: null, used: 412, remaining: null, unlimited: true, pooled: true, has_quota: true, exhausted: false, overage_permitted: false }} }});
const spent = copilotQuotaSummary({{ available: true, credits: {{ id: "premium_interactions", included: null, used: 1900, remaining: null, unlimited: true, pooled: true, has_quota: false, exhausted: true, overage_permitted: true }} }});
const seat = copilotQuotaSummary({{ available: true, fetched_at: "2026-11-02T10:00:00+00:00", credits: {{ id: "premium_interactions", included: 1500, used: 320, remaining: 1180, percent_remaining: 78.67, unlimited: false, pooled: false }},
  live: {{ id: "premium_interactions", entitlement: 1500, unlimited: false, percent_remaining: 40, observed_at: "2026-11-02T10:05:00+00:00", overage_permitted: false }} }});
const stale = copilotQuotaSummary({{ available: true, fetched_at: "2026-11-02T10:00:00+00:00", credits: {{ included: 1500, used: 320, remaining: 1180 }},
  live: {{ entitlement: 1500, percent_remaining: 40, observed_at: "2026-11-02T09:00:00+00:00" }} }});
const down = copilotQuotaSummary({{ available: false, error: "GitHub answered HTTP 503", live: {{ entitlement: 1500, percent_remaining: 25, reset_date: "2026-12-01", observed_at: "2026-11-02T10:05:00+00:00" }} }});
const pooledSnapshot = copilotQuotaSummary({{ available: true, snapshots: [
  {{ id: "chat", unlimited: true }},
  {{ id: "premium_interactions", label: "AI credits", unlimited: true, credits_used: 77, has_quota: true }},
] }});
console.log(JSON.stringify({{
  pooled, pooledHtml: copilotQuotaChipHtml(pooled),
  spent, spentHtml: copilotQuotaChipHtml(spent),
  seat, stale, down, downHtml: copilotQuotaChipHtml(down), pooledSnapshot,
}}));
"""
    result = _run_node(script)

    pooled = result["pooled"]
    assert pooled["pooled"] is True
    assert pooled["used"] == 412
    assert pooled["included"] is None
    assert pooled["remaining"] is None
    assert pooled["percentUsed"] is None
    assert pooled["hasNumbers"] is True
    assert pooled["warning"] is False
    assert "412 credits used" in result["pooledHtml"]
    assert "organization pool" in result["pooledHtml"]
    assert "portal-quota-meter" not in result["pooledHtml"]
    assert 'data-copilot-quota="ok"' in result["pooledHtml"]
    assert "2026-12-01" in result["pooledHtml"]

    spent = result["spent"]
    assert spent["exhausted"] is True
    assert spent["warning"] is True
    assert spent["overagePermitted"] is True
    assert 'data-copilot-quota="exhausted"' in result["spentHtml"]
    assert "is-warning" in result["spentHtml"]
    assert "allotment for this month is used up" in result["spentHtml"]
    assert "Additional usage is enabled" in result["spentHtml"]

    # The live header of the latest answer is newer than the endpoint's reading, so it wins.
    seat = result["seat"]
    assert seat["percentUsed"] == 60
    assert seat["used"] == 900
    assert seat["remaining"] == 600
    assert seat["included"] == 1500
    # ...but an older header does not.
    assert result["stale"]["used"] == 320
    assert result["stale"]["percentUsed"] == pytest.approx(21.33, abs=0.01)

    down = result["down"]
    assert down["available"] is True
    assert down["fromLive"] is True
    assert down["percentUsed"] == 75
    assert down["used"] == 1125
    assert "From the latest answer" in result["downHtml"]
    assert "2026-12-01" in result["downHtml"]

    snapshot = result["pooledSnapshot"]
    assert snapshot["pooled"] is True
    assert snapshot["used"] == 77
    assert snapshot["unit"] == "AI credits"


def test_usage_panel_shows_a_pooled_allotment_and_its_exhaustion():
    usage = {"period_days": 30, "global": {"request_count": 1}, "by_model": {}, "by_provider": {}}
    pooled = _render_usage_panel(
        usage=usage,
        quota={
            "available": True,
            "reset_date": "2026-12-01",
            "credits": {"id": "premium_interactions", "label": "AI credits", "included": None, "used": 412.25, "remaining": None, "unlimited": True, "pooled": True, "has_quota": True, "exhausted": False},
            "snapshots": [{"id": "premium_interactions", "label": "AI credits", "unlimited": True, "credits_used": 412.25, "used": 412.25}],
        },
    )
    assert "412 credits used" in pooled
    assert "organization pool" in pooled
    assert "left" not in pooled.split("Copilot balance this month", 1)[1].split("portal-quota-section", 1)[0]
    assert 'data-copilot-quota="pooled"' in pooled
    assert "used up" not in pooled

    spent = _render_usage_panel(
        usage=usage,
        quota={
            "available": True,
            "credits": {"id": "premium_interactions", "included": None, "used": 1900, "remaining": None, "unlimited": True, "pooled": True, "has_quota": False, "exhausted": True, "overage_permitted": True},
            "snapshots": [],
        },
    )
    assert 'data-copilot-quota="exhausted"' in spent
    assert "is-warning" in spent
    assert "allotment for this month is used up" in spent
    assert "Additional usage is enabled" in spent

    seat = _render_usage_panel(
        usage=usage,
        quota={
            "available": True,
            "credits": {"id": "premium_interactions", "label": "AI credits", "included": 1500, "used": 320, "remaining": 1180, "unlimited": False, "pooled": False},
            "snapshots": [
                {"id": "premium_interactions", "label": "AI credits", "entitlement": 1500, "remaining": 1180, "used": 320, "percent_remaining": 78.7, "unlimited": False},
                {"id": "chat", "label": "Chat", "unlimited": True},
            ],
        },
    )
    assert "320 credits used" in seat
    assert "1180 left" in seat
    # The snapshot already shown as the balance is not listed a second time.
    assert "AI credits" not in seat


# --- 4. one model list for everyone --------------------------------------------


def test_the_pickers_read_the_portal_wide_catalog_and_keep_the_static_table_as_fallback():
    js = _js()
    # Never per assistant: the composer takes the list from chat-model-profile,
    # the connector form from GET /api/models/catalog, both served by the Portal.
    assert 'agentApiFor(agentId, "/api/models")' not in js
    assert "refreshRuntimeModelCatalog" not in js
    assert 'api("/api/models/catalog")' in js
    composer = _extract_js_function(js, "renderComposerModelSelectorForAgent")
    assert "pickerModelLabel(value)" in composer
    assert "runtimeCatalogPickerModels" not in composer
    connector = _extract_js_function(js, "updateModelOptions")
    assert "catalogPickerModels(portalCatalog)" in connector
    assert "managedProviderModels[provider]" in connector
    profile = _extract_js_function(js, "refreshComposerModelProfile")
    assert "rememberModelLabels(payload?.model_labels)" in profile
    assert "ensurePortalModelCatalog().then" in js


def test_catalog_models_are_listed_with_the_known_labels():
    script = f"""
const managedProviderModels = {{ github_copilot: [
  {{ value: "auto", label: "Auto" }}, {{ value: "gpt-6.1-sol", label: "GPT-6.1 Sol" }}, {{ value: "gpt-6-luna", label: "GPT-6 Luna" }},
], ai_platform: [] }};
const portalModelLabels = new Map();
{_extract_js_function(_js(), "rememberModelLabels")}
{_extract_js_function(_js(), "pickerModelLabel")}
{_extract_js_function(_js(), "catalogPickerModels")}
rememberModelLabels({{ "gpt-6.2-nova": "GPT-6.2 Nova", "mai-code-1.1-flash": "MAI-Code-1.1-Flash", "": "nothing" }});
const catalog = {{ source: "catalog", models: [
  {{ id: "auto", label: "Auto (Balance)", kind: "auto" }},
  {{ id: "gpt-6.1-sol", label: "GPT-6.1 Sol (OpenAI)", kind: "model" }},
  {{ id: "gpt-6.2-nova", label: "GPT-6.2 Nova", kind: "model" }},
  {{ id: "gpt-5.3-codex", label: "GPT-5.3-Codex", kind: "model" }},
] }};
console.log(JSON.stringify({{
  listed: catalogPickerModels(catalog),
  fromStatic: catalogPickerModels({{ source: "static", models: [{{ id: "gpt-6-sol", label: "x" }}] }}),
  empty: catalogPickerModels({{ source: "catalog", models: [] }}),
  missing: catalogPickerModels(null),
  labels: [pickerModelLabel("gpt-6-luna"), pickerModelLabel("mai-code-1.1-flash"), pickerModelLabel("gpt-9-zeta")],
}}));
"""
    result = _run_node(script)
    assert result["listed"] == [
        {"value": "auto", "label": "Auto"},
        {"value": "gpt-6.1-sol", "label": "GPT-6.1 Sol"},
        {"value": "gpt-6.2-nova", "label": "GPT-6.2 Nova"},
        {"value": "gpt-5.3-codex", "label": "GPT-5.3-Codex"},
    ]
    assert result["fromStatic"] is None
    assert result["empty"] is None
    assert result["missing"] is None
    assert result["labels"] == ["GPT-6 Luna", "MAI-Code-1.1-Flash", "gpt-9-zeta"]


@pytest.fixture
def _no_catalog():
    clear_catalog_models()
    yield
    clear_catalog_models()


def test_without_a_catalog_a_plausible_copilot_id_passes_and_a_retired_one_is_repaired(_no_catalog):
    assert is_plausible_copilot_model_id("gpt-6.2-nova") is True
    assert is_plausible_copilot_model_id("claude-sonnet-5.5") is True
    assert is_plausible_copilot_model_id("mai-code-1.1-flash") is True
    for bad in ("", "bogus", "GPT-6-Sol", "gpt 6", "gpt-5.6-terra", "gpt-4o", "gpt-5", "../x", "a" * 70):
        assert is_plausible_copilot_model_id(bad) is False, bad
    assert is_retired_copilot_model("gpt-5.4") is True
    assert is_retired_copilot_model("gpt-6-sol") is False

    assert coerce_to_provider_model("github_copilot", "gpt-6.2-nova") == "gpt-6.2-nova"
    assert coerce_to_provider_model("github_copilot", "gpt-5.6-terra") == "auto"
    assert coerce_to_provider_model("github_copilot", "bogus") == "auto"
    # AI Platform is a fixed gateway: nothing dynamic about it.
    assert coerce_to_provider_model("ai_platform", "gpt-6.2-nova") == "gpt-5.4"

    assert is_selectable_model("github_copilot", "gpt-6.2-nova") is True
    assert is_selectable_model("github_copilot", "gpt-6-sol") is True
    assert is_selectable_model("github_copilot", "gpt-5.4") is False
    assert is_selectable_model("ai_platform", "gpt-6.2-nova") is False
    assert is_selectable_model("ai_platform", "gpt-5.4") is True
    assert RuntimeProfileService.is_managed_model_allowed("github_copilot", "gpt-6.2-nova") is True
    assert RuntimeProfileService.is_managed_model_allowed("github_copilot", "gpt-5.4") is False
    assert RuntimeProfileService.is_managed_model_allowed("github_copilot", "") is False
    assert RuntimeProfileService.is_managed_model_allowed("ai_platform", "gpt-6.2-nova") is False


def test_once_the_catalog_is_known_it_is_the_rule(_no_catalog):
    # What the pickers offer is what validation accepts: a model Copilot still
    # serves passes whatever its line (GPT-5.3-Codex), an id the catalog does
    # not list is refused, and the static list always stands.
    register_catalog_models(["auto", "gpt-6.1-sol", "mai-code-1.1-flash", "GPT-5.3-Codex"])
    assert is_selectable_model("github_copilot", "mai-code-1.1-flash") is True
    assert is_selectable_model("github_copilot", "gpt-5.3-codex") is True
    assert is_selectable_model("github_copilot", "gpt-6-sol") is True
    assert is_selectable_model("github_copilot", "gpt-9-zeta") is False
    assert is_selectable_model("github_copilot", "gpt-6.2-nova") is False
    assert coerce_to_provider_model("github_copilot", "gpt-5.3-codex") == "gpt-5.3-codex"
    assert coerce_to_provider_model("github_copilot", "gpt-9-zeta") == "auto"
    assert RuntimeProfileService.is_managed_model_allowed("github_copilot", "gpt-5.3-codex") is True
    assert RuntimeProfileService.is_managed_model_allowed("github_copilot", "gpt-9-zeta") is False
    assert RuntimeProfileService.is_managed_model_allowed("ai_platform", "mai-code-1.1-flash") is False
