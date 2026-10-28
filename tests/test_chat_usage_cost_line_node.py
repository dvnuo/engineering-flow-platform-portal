"""What an answer cost rides in the model badge's tooltip, and its neighbours.

Both runtimes stamp ``usage`` on the final chat payload and on the stored
assistant message (tokens, iterations, estimated AI credits, the billing
tier); the native runner's usage.updated events carry the running total. The
chat UI folds that into the ``.message-model`` badge at the top right of the
bubble: its tooltip lists the model lines, then the cost, and its
``data-credits`` feeds the session total. Next to it: a budget-pause notice
with a Continue button, the 272K billing line on the context meter and the
monthly Copilot balance chip. The pure helpers run in node; the wiring is
checked on the shipped source, in the style of test_chat_model_badge_static.py.
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
        # The helpers print non-ASCII (the middle dot, the approximately-equal
        # sign); Windows would otherwise decode node's UTF-8 stdout as cp1252.
        result = subprocess.run([node_bin, str(script_path)], check=False, capture_output=True, encoding="utf-8", errors="replace")
    if result.returncode != 0:
        raise AssertionError(f"node failed\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}")
    return json.loads(result.stdout.strip())


def _bundle(js: str) -> str:
    # escapeHtml / escapeHtmlAttr / formatContextTokens / inferenceModelLabel
    # are chat_ui globals the helpers lean on; the label lookup reads the
    # managed provider catalog in the page, so it is a table here.
    stubs = """
const escapeHtml = (value) => String(value ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
const escapeHtmlAttr = (value) => escapeHtml(value).replace(/"/g, "&quot;");
const MODEL_LABELS = { "gpt-6.1-sol": "GPT-6.1 Sol", "gpt-6-luna": "GPT-6 Luna", "auto": "Auto", "auto-efficiency": "Auto (Efficiency)" };
const inferenceModelLabel = (value) => MODEL_LABELS[String(value || "").trim()] || String(value || "").trim() || "Model";
"""
    return "\n".join([
        stubs,
        _extract_js_helper_block(js, "assistantModelAttribution"),
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


def test_cost_details_ride_in_the_model_badge_tooltip():
    script = f"""
{_bundle(_js())}
const payload = {json.dumps(FINAL_PAYLOAD)};
const summary = assistantUsageSummary(payload);
const attribution = {{ model: "gpt-6-luna", requestedModel: "auto", autoTier: "auto", fallbackReason: "" }};
const liveEvent = {{ type: "usage.updated", iteration: 7, usage: {{ input_tokens: 500000, cached_input_tokens: 400000, output_tokens: 9000, reasoning_tokens: 6000, estimated_credits: 31.2 }} }};
const opencodeMessage = {{ info: {{ tokens: {{ input: 1000, output: 200, reasoning: 50, cache: {{ read: 500, write: 100 }} }} }} }};
const longContext = {{ usage: {{ input_tokens: 300000, output_tokens: 100, estimated_credits: 12, pricing: {{ tier: "long_context" }} }} }};
const messages = [{{ role: "assistant", content: "a" }}, {{ role: "assistant", content: "b", usage: {{ input_tokens: 10, output_tokens: 2, estimated_credits: 0.5 }} }}, {{ role: "assistant", content: "c" }}];
console.log(JSON.stringify({{
  lines: assistantUsageCostLines(summary),
  liveLines: assistantUsageCostLines(assistantUsageSummary(liveEvent), {{ live: true }}),
  title: assistantModelBadgeTitle(attribution, summary),
  html: buildAssistantModelBadgeHtml(attribution, summary),
  modelOnlyHtml: buildAssistantModelBadgeHtml(attribution, null),
  billedHtml: buildAssistantModelBadgeHtml(null, summary),
  creditsOnlyHtml: buildAssistantModelBadgeHtml(null, assistantUsageSummary(longContext)),
  longHtml: buildAssistantModelBadgeHtml(attribution, assistantUsageSummary(longContext)),
  nothing: buildAssistantModelBadgeHtml(null, null),
  opencode: assistantUsageSummary(opencodeMessage),
  latest: latestAssistantUsageSummary(messages),
  none: assistantUsageSummary({{ response: "no usage here" }}),
  emptyLines: assistantUsageCostLines(null),
  compact: [formatCompactTokens(980), formatCompactTokens(45000), formatCompactTokens(1905000), formatCompactTokens(12345678)],
  credits: [formatCredits(104.3), formatCredits(31.26), formatCredits(0.5), formatCredits(2.345)],
}}));
"""
    result = _run_node(script)

    assert result["lines"] == [
        "Steps: 15",
        "Input: 1.9M tokens (cache 91%)",
        "Output: 45K tokens (reasoning 30K)",
        "Credits: ≈ 104 (Default tier)",
        "Credits are an estimate from GitHub's published price list (1 credit = $0.01), not the bill.",
    ]
    # One hover: the model lines lead, a blank line, then the cost.
    assert result["title"].split("\n") == ["Model: gpt-6-luna", "Requested: auto", "Tier: auto", ""] + result["lines"]
    assert result["html"].startswith('<span class="message-model" title="Model: gpt-6-luna')
    assert result["html"].endswith(">Auto → GPT-6 Luna</span>")
    assert 'data-model="gpt-6-luna"' in result["html"]
    assert 'data-credits="104.3"' in result["html"]
    assert 'data-tier="default"' in result["html"]
    # Without usage the badge reads as it always did.
    assert 'title="Model: gpt-6-luna\nRequested: auto\nTier: auto"' in result["modelOnlyHtml"]
    assert "data-credits" not in result["modelOnlyHtml"]
    # The running total while the turn works: the iteration count comes from
    # the event, the cache ratio from the token counts, and the last line says
    # the figures are not final.
    assert result["liveLines"][:2] == ["Steps: 7", "Input: 500K tokens (cache 80%)"]
    assert result["liveLines"][-2:] == ["Credits: ≈ 31.2", "Still running; these are the totals so far."]
    # No attribution on the message: the model it was billed as names the badge.
    assert result["billedHtml"].endswith(">GPT-6.1 Sol</span>")
    assert 'data-model="gpt-6.1-sol"' in result["billedHtml"]
    assert "Model: gpt-6.1-sol" in result["billedHtml"]
    # No model anywhere: the credits are the badge, so the cost can still be read.
    assert result["creditsOnlyHtml"].endswith(">≈ 12 credits</span>")
    assert "data-model" not in result["creditsOnlyHtml"]
    assert result["nothing"] == ""
    # Past the 272K line the badge is tinted and the tooltip names the tier.
    assert 'class="message-model message-model-long-context"' in result["longHtml"]
    assert "Long context tier, 2× price" in result["longHtml"]
    assert 'data-tier="long_context"' in result["longHtml"]
    # OpenCode's own record: input/output/reasoning plus cache.read.
    assert result["opencode"]["inputTokens"] == 1000
    assert result["opencode"]["cachedInputTokens"] == 500
    assert result["opencode"]["cacheHitRatio"] == 0.5
    assert result["opencode"]["reasoningTokens"] == 50
    assert result["latest"]["estimatedCredits"] == 0.5
    assert result["none"] is None
    assert result["emptyLines"] == []
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
    assert result["creditsText"] == "Paused at about 212 credits for this message (limit 200)."
    assert result["iterationText"] == "Paused after 30 steps (limit 30)."
    assert "data-budget-pause-continue" in result["html"]
    assert ">Continue</button>" in result["html"]
    assert "to carry on." in result["html"]
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
    assert "1520 credits used" in result["creditsHtml"]
    assert "380 left" in result["creditsHtml"]
    assert "2026-11-01" in result["creditsHtml"]
    # A limited snapshot stands in when there is no credits block.
    assert result["snapshot"]["used"] == 1600
    assert result["snapshot"]["unit"] == "Premium"
    assert result["snapshot"]["warning"] is True
    assert "Unavailable" in result["unavailableHtml"]
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
    css = _css()

    history = _extract_js_function(src, "renderChatHistory")
    assert 'buildAssistantModelBadgeHtml(latestAssistantModelAttribution(entry.messages), typeof latestAssistantUsageSummary === "function" ? latestAssistantUsageSummary(entry.messages) : null)' in history
    assert "entry === lastAssistantEntry" in history
    assert "applyBudgetPauseNotice(row, budgetPause)" in history

    article = _extract_js_function(src, "buildAssistantMessageArticle")
    assert "buildAssistantModelBadgeHtml(assistantModelAttribution(options), options.usageSummary || null)" in article

    group = _extract_js_function(src, "buildAssistantGroupMessageArticle")
    assert 'usageSummary: typeof latestAssistantUsageSummary === "function" ? latestAssistantUsageSummary(group?.messages || []) : null' in group

    finalize = _extract_js_function(src, "finalizePendingAssistantRow")
    assert "assistantUsageSummary(payload) || requestCtx?.usageSummary || null" in finalize
    assert "applyAssistantModelBadge(article, assistantModelAttribution(payload) || requestCtx?.modelAttribution || null, usageSummary)" in finalize
    assert "budgetPauseInfo(payload) || requestCtx?.budgetPause || null" in finalize

    update = _extract_js_function(src, "updateOrCreateAssistantRowForRequest")
    assert "assistantUsageSummary(payload) || requestCtx?.usageSummary || null" in update
    assert "applyAssistantModelBadge(article, assistantModelAttribution(payload) || requestCtx?.modelAttribution || null, usageSummary)" in update

    stream = _extract_js_function(src, "handleChatStreamEvent")
    assert "requestCtx.usageSummary = liveUsage" in stream
    assert "requestCtx.modelAttribution || null, requestCtx.usageSummary || null, { live: true }" in stream
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

    # The session total sums the badges; the separate line under the bubble is
    # gone, so the badge is the one place the cost is read.
    total = _extract_js_function(src, "sumVisibleAssistantCredits")
    assert '".message-model[data-credits]"' in total
    assert 'class="message-cost"' not in src
    assert "applyAssistantCostLine" not in src
    assert "buildAssistantCostLineHtml" not in src
    assert ".message-cost {" not in css
    assert ".message-model-long-context {" in css
