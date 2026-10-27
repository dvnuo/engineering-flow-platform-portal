"""The line under each answer that says what the turn cost, and its neighbours.

Both runtimes stamp ``usage`` on the final chat payload and on the stored
assistant message (tokens, iterations, estimated AI credits, the billing
tier); the native runner's usage.updated events carry the running total. The
chat UI turns that into a ``.message-cost`` line, a budget-pause notice with a
Continue button, the 272K billing line on the context meter and the monthly
Copilot balance chip. The pure helpers run in node; the wiring is checked on
the shipped source, in the style of test_chat_model_badge_static.py.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from _js_extract_helpers import _extract_js_function, _extract_js_helper_block

ROOT = Path(__file__).resolve().parents[1]


def _js() -> str:
    return (ROOT / "app" / "static" / "js" / "chat_ui.js").read_text(encoding="utf-8")


def _css() -> str:
    return (ROOT / "app" / "static" / "css" / "app.css").read_text(encoding="utf-8")


def _run_node(script: str) -> dict:
    node_bin = shutil.which("node")
    if not node_bin:
        pytest.skip("node is not installed; skipping cost line helper test")
    # The bundle is past Windows' command-line limit: it goes through a file.
    with tempfile.TemporaryDirectory() as tmp:
        script_path = Path(tmp) / "cost_line_case.js"
        script_path.write_text(script, encoding="utf-8")
        # The helpers print Chinese; Windows would otherwise decode node's
        # UTF-8 stdout as cp1252.
        result = subprocess.run([node_bin, str(script_path)], check=False, capture_output=True, encoding="utf-8", errors="replace")
    if result.returncode != 0:
        raise AssertionError(f"node failed\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}")
    return json.loads(result.stdout.strip())


def _bundle(js: str) -> str:
    # escapeHtml / escapeHtmlAttr / formatContextTokens are chat_ui globals the
    # helpers lean on; the first two are stubbed, the third is the real one.
    stubs = """
const escapeHtml = (value) => String(value ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
const escapeHtmlAttr = (value) => escapeHtml(value).replace(/"/g, "&quot;");
"""
    return "\n".join([
        stubs,
        _extract_js_helper_block(js, "assistantUsageCost"),
        _extract_js_helper_block(js, "copilotQuota"),
        _extract_js_helper_block(js, "contextBillingView"),
        _extract_js_function(js, "formatContextTokens"),
        _extract_js_function(js, "inferenceContextLabel"),
    ])


FINAL_PAYLOAD = {
    "response": "done",
    "usage": {
        "input_tokens": 1_905_000,
        "cached_input_tokens": 1_736_000,
        "output_tokens": 45_000,
        "reasoning_tokens": 30_000,
        "total_tokens": 1_950_000,
        "estimated_credits": 104.3,
        "iterations": 15,
        "cache_hit_ratio": 0.911,
        "pricing": {"model": "gpt-6.1-sol", "tier": "default", "source": "static", "threshold_tokens": 272_000},
    },
}


def test_cost_line_reads_the_runtime_usage_and_renders_the_chinese_line():
    script = f"""
{_bundle(_js())}
const payload = {json.dumps(FINAL_PAYLOAD)};
const summary = assistantUsageSummary(payload);
const liveEvent = {{ type: "usage.updated", iteration: 7, usage: {{ input_tokens: 500000, cached_input_tokens: 400000, output_tokens: 9000, reasoning_tokens: 6000, estimated_credits: 31.2 }} }};
const opencodeMessage = {{ info: {{ tokens: {{ input: 1000, output: 200, reasoning: 50, cache: {{ read: 500, write: 100 }} }} }} }};
const longContext = {{ usage: {{ input_tokens: 300000, output_tokens: 100, estimated_credits: 12, pricing: {{ tier: "long_context" }} }} }};
const messages = [{{ role: "assistant", content: "a" }}, {{ role: "assistant", content: "b", usage: {{ input_tokens: 10, output_tokens: 2, estimated_credits: 0.5 }} }}, {{ role: "assistant", content: "c" }}];
console.log(JSON.stringify({{
  text: assistantUsageCostText(summary),
  title: assistantUsageCostTitle(summary),
  html: buildAssistantCostLineHtml(summary),
  liveText: assistantUsageCostText(assistantUsageSummary(liveEvent)),
  opencode: assistantUsageSummary(opencodeMessage),
  longHtml: buildAssistantCostLineHtml(assistantUsageSummary(longContext)),
  latest: latestAssistantUsageSummary(messages),
  none: assistantUsageSummary({{ response: "no usage here" }}),
  emptyHtml: buildAssistantCostLineHtml(null),
  compact: [formatCompactTokens(980), formatCompactTokens(45000), formatCompactTokens(1905000), formatCompactTokens(12345678)],
  credits: [formatCredits(104.3), formatCredits(31.26), formatCredits(0.5), formatCredits(2.345)],
}}));
"""
    result = _run_node(script)

    assert result["text"] == (
        "15 \u8f6e \u00b7 \u8f93\u5165 1.9M\uff08cache 91%\uff09 \u00b7 "
        "\u8f93\u51fa 45K\uff08\u63a8\u7406 30K\uff09 \u00b7 \u2248 104 credits"
    )
    assert "Default \u6863" in result["title"]
    assert "gpt-6.1-sol" in result["title"]
    assert "1 credit = $0.01" in result["title"]
    assert 'class="message-cost"' in result["html"]
    assert 'data-credits="104.3"' in result["html"]
    assert 'data-tier="default"' in result["html"]
    # The running total while the turn works: the iteration count comes from
    # the event, the cache ratio from the token counts.
    assert result["liveText"].startswith("7 \u8f6e \u00b7 \u8f93\u5165 500K\uff08cache 80%\uff09")
    assert result["liveText"].endswith("\u2248 31.2 credits")
    # OpenCode's own record: input/output/reasoning plus cache.read.
    assert result["opencode"]["inputTokens"] == 1000
    assert result["opencode"]["cachedInputTokens"] == 500
    assert result["opencode"]["cacheHitRatio"] == 0.5
    assert result["opencode"]["reasoningTokens"] == 50
    assert "message-cost-tier-long" in result["longHtml"]
    assert "Long context 2\u00d7" in result["longHtml"]
    assert result["latest"]["estimatedCredits"] == 0.5
    assert result["none"] is None
    assert result["emptyHtml"] == ""
    assert result["compact"] == ["980", "45K", "1.9M", "12M"]
    assert result["credits"] == ["104", "31.3", "0.5", "2.35"]


def test_budget_pause_notice_reads_the_final_payload_and_the_event():
    script = f"""
{_bundle(_js())}
const finalPayload = {{ response: "stopped", terminal_reason: "budget_paused", budget_pause: {{ reason: "credits_guard", estimated_credits: 212.4, credits_limit: 200, iterations: 18 }} }};
const event = {{ type: "run_budget_paused", data: {{ reason: "iteration_cap", iterations: 30, iteration_limit: 30 }} }};
console.log(JSON.stringify({{
  credits: budgetPauseInfo(finalPayload),
  creditsText: budgetPauseNoticeText(budgetPauseInfo(finalPayload)),
  iterationText: budgetPauseNoticeText(budgetPauseInfo(event)),
  html: buildBudgetPauseNoticeHtml(budgetPauseInfo(finalPayload)),
  plain: budgetPauseInfo({{ response: "ok", terminal_reason: "completed" }}),
}}));
"""
    result = _run_node(script)

    assert result["credits"]["reason"] == "credits_guard"
    assert result["credits"]["estimatedCredits"] == 212.4
    assert result["credits"]["creditsLimit"] == 200
    assert result["creditsText"] == "\u672c\u6761\u6d88\u606f\u5df2\u7528\u7ea6 212 credits\uff08\u4e0a\u9650 200\uff09\uff0c\u5df2\u6682\u505c\u3002"
    assert result["iterationText"] == "\u672c\u6761\u6d88\u606f\u5df2\u8dd1 30 \u8f6e\uff08\u4e0a\u9650 30\uff09\uff0c\u5df2\u6682\u505c\u3002"
    assert "data-budget-pause-continue" in result["html"]
    assert ">\u7ee7\u7eed</button>" in result["html"]
    assert result["plain"] is None


def test_quota_chip_reads_credits_or_snapshots_and_warns_past_80_percent():
    script = f"""
{_bundle(_js())}
const credits = copilotQuotaSummary({{ available: true, credits: {{ included: 1900, used: 1520, remaining: 380 }}, reset_date: "2026-11-01" }});
const snapshot = copilotQuotaSummary({{ available: true, snapshots: [
  {{ id: "chat", label: "Chat", unlimited: true }},
  {{ id: "premium_interactions", label: "Premium", entitlement: 1900, remaining: 300 }},
] }});
const unavailable = copilotQuotaSummary({{ available: false, error: "runtime answered 404" }});
console.log(JSON.stringify({{
  credits,
  creditsHtml: copilotQuotaChipHtml(credits),
  snapshot,
  unavailableHtml: copilotQuotaChipHtml(unavailable),
  bare: copilotQuotaSummary({{ available: true, plan: "business" }}),
  bareHtml: copilotQuotaChipHtml(copilotQuotaSummary({{ available: true, plan: "business" }})),
}}));
"""
    result = _run_node(script)

    assert result["credits"]["used"] == 1520
    assert result["credits"]["remaining"] == 380
    assert result["credits"]["percentUsed"] == 80
    assert result["credits"]["warning"] is True
    assert "is-warning" in result["creditsHtml"]
    assert "\u5df2\u7528 1520 credits" in result["creditsHtml"]
    assert "\u5269\u4f59 380" in result["creditsHtml"]
    assert "2026-11-01" in result["creditsHtml"]
    # A limited snapshot stands in when there is no credits block.
    assert result["snapshot"]["used"] == 1600
    assert result["snapshot"]["unit"] == "Premium"
    assert result["snapshot"]["warning"] is True
    assert "\u4e0d\u53ef\u7528" in result["unavailableHtml"]
    assert "runtime answered 404" in result["unavailableHtml"]
    assert result["bare"]["hasNumbers"] is False
    assert "business" in result["bareHtml"]


def test_context_meter_marks_the_272k_line_and_the_long_context_tier():
    script = f"""
{_bundle(_js())}
const over = contextBillingView({{ used_tokens: 180000, calibrated_used_tokens: 300000, context_window_tokens: 1000000, billing_threshold_tokens: 272000, budget_tokens: 914000, calibration: {{ ratio: 1.67 }} }});
const under = contextBillingView({{ used_tokens: 90000, context_window_tokens: 256000, billing_threshold_tokens: 272000, billing_tier: "default", budget_tokens: 128000 }});
const legacy = contextBillingView({{ used_tokens: 90000, context_window_tokens: 256000 }});
console.log(JSON.stringify({{ over, under, legacy, labels: [inferenceContextLabel(64000), inferenceContextLabel(128000), inferenceContextLabel(256000), inferenceContextLabel(1000000), inferenceContextLabel(0)] }}));
"""
    result = _run_node(script)

    over = result["over"]
    assert over["tier"] == "long_context"
    assert over["overThreshold"] is True
    assert over["fillClass"] == " is-over-threshold"
    assert 'style="left:27.2%"' in over["markerHtml"]
    assert "is-long-context" in over["rowHtml"]
    assert "914K" in over["rowHtml"]
    assert "300,000" in over["calibrationHtml"] and "1.67" in over["calibrationHtml"]

    under = result["under"]
    assert under["tier"] == "default"
    assert under["markerHtml"] == "", "no marker when the window ends below the threshold"
    assert "Default" in under["rowHtml"]
    assert under["fillClass"] == ""

    legacy = result["legacy"]
    assert legacy == {"fillClass": "", "markerHtml": "", "rowHtml": "", "calibrationHtml": "", "tier": "", "overThreshold": False, "overBudget": False}

    assert result["labels"] == ["64K", "128K", "256K", "1M \u00b7 Long context 2\u00d7", "Auto"]


def test_rows_panels_and_streams_are_wired_to_the_helpers():
    src = _js()

    history = _extract_js_function(src, "renderChatHistory")
    assert "buildAssistantCostLineHtml(usageSummary)" in history
    assert "latestAssistantUsageSummary(entry.messages)" in history
    assert "entry === lastAssistantEntry" in history
    assert "applyBudgetPauseNotice(row, budgetPause)" in history

    finalize = _extract_js_function(src, "finalizePendingAssistantRow")
    assert "applyAssistantCostLine(article, assistantUsageSummary(payload) || requestCtx?.usageSummary || null)" in finalize
    assert "budgetPauseInfo(payload) || requestCtx?.budgetPause || null" in finalize

    update = _extract_js_function(src, "updateOrCreateAssistantRowForRequest")
    assert "applyAssistantCostLine(article, usageSummary)" in update

    stream = _extract_js_function(src, "handleChatStreamEvent")
    assert "requestCtx.usageSummary = liveUsage" in stream
    assert "{ live: true }" in stream
    assert 'embeddedType === "run_budget_paused"' in stream
    assert "requestCtx.budgetPause = budgetPause" in stream

    trackable = _extract_js_function(src, "isTrackableStreamEvent")
    assert '"run_budget_paused"' in trackable

    panel = _extract_js_function(src, "renderContextUsagePanel")
    assert "contextBillingView(snapshot)" in panel
    assert 'id="context-quota-chip"' in panel
    assert "renderCopilotQuotaChip(state.selectedAgentId)" in panel
    assert "sumVisibleAssistantCredits(dom.messageList)" in panel
    assert "buildCostGuideHtml()" in panel

    guide = _extract_js_function(src, "buildCostGuideHtml")
    assert 'const COPILOT_PRICING_DOCS_URL = "https://docs.github.com/en/copilot/reference/copilot-billing/models-and-pricing";' in src
    assert "COPILOT_PRICING_DOCS_URL" in guide
    assert "#/help/copilot-costs" in guide

    legacy = _extract_js_function(src, "canonicalMessageToLegacyDisplayMessage")
    assert "usage: (typeof assistantUsageObject === \"function\" ? assistantUsageObject(message) : null) || undefined" in legacy

    usage_card = _extract_js_function(src, "fetchUsageForAgent")
    assert "total_estimated_credits" in usage_card
    assert "total_cached_input_tokens" in usage_card
    assert "total_reasoning_tokens" in usage_card

    quota = _extract_js_function(src, "renderCopilotQuotaChip")
    assert 'agentApiFor(agentId, "/api/copilot/quota")' in quota

    css = _css()
    for selector in (".message-cost {", ".message-cost-notice {", ".portal-context-meter-marker {", ".portal-context-meter-fill.is-over-threshold", ".portal-quota-chip {", ".portal-cost-guide summary"):
        assert selector in css, selector
