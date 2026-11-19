/**
 * What an assistant can use, what it is using, and why it last stopped.
 *
 * The details panel used to show three pills: the CPU and memory the assistant
 * has reserved, and a disk size. None of them is the ceiling it can reach or
 * what it is using right now, and an assistant that was stopped for running out
 * of memory came back looking like nothing had happened. This draws the missing
 * part under the pills: the limit, the live usage when the cluster reports it,
 * how often the assistant restarted, and the reason for the last time.
 *
 * The reading comes from GET /api/agents/{id}/resources, asked for when a
 * member opens the details and never by the sidebar's polling. chat_ui.js puts
 * an empty #agent-resource-reading under the pills and calls show(); when the
 * request fails the panel simply stays as it was before this file existed.
 */
(function () {
  "use strict";

  const HOST_ID = "agent-resource-reading";
  const FRESH_MS = 15000;
  const EVICTION_SHOWN_FOR_MS = 7 * 24 * 60 * 60 * 1000;
  // agentId -> { reading, at }: the panel is redrawn on every status change,
  // and the last reading is drawn at once so the lines do not blink.
  const readings = new Map();

  function esc(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function formatWhen(iso) {
    if (!iso) return "";
    const when = new Date(iso);
    if (Number.isNaN(when.getTime())) return "";
    const locale = document.documentElement.lang || undefined;
    try {
      return when.toLocaleString(locale, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
    } catch (error) {
      return when.toLocaleString();
    }
  }

  function amounts(values, percents) {
    const parts = [];
    const source = values || {};
    const shares = percents || {};
    if (source.cpu) {
      parts.push(shares.cpu == null
        ? t("{amount} CPU", { amount: source.cpu })
        : t("{amount} CPU ({percent}% of the limit)", { amount: source.cpu, percent: shares.cpu }));
    }
    if (source.memory) {
      parts.push(shares.memory == null
        ? t("{amount} memory", { amount: source.memory })
        : t("{amount} memory ({percent}% of the limit)", { amount: source.memory, percent: shares.memory }));
    }
    if (source.ephemeral_storage) {
      parts.push(t("{amount} local disk", { amount: source.ephemeral_storage }));
    }
    return parts;
  }

  function lastStop(reading) {
    const termination = reading.last_termination;
    if (termination && termination.reason === "OOMKilled") {
      const time = formatWhen(termination.finished_at);
      const limit = reading.limits && reading.limits.memory;
      if (time && limit) {
        return { tone: "warning", text: t("Last restart, {time}: it ran out of memory and was stopped at its {limit} limit. Whatever it was doing at that moment was lost.", { time, limit }) };
      }
      return { tone: "warning", text: t("Its last restart happened because it ran out of memory. Whatever it was doing at that moment was lost.") };
    }
    if (termination && (termination.exit_code || (termination.reason && termination.reason !== "Completed"))) {
      const time = formatWhen(termination.finished_at);
      const reason = termination.reason || t("Unknown");
      const code = termination.exit_code == null ? "?" : termination.exit_code;
      if (time) {
        return { tone: "warning", text: t("Last restart, {time}: it stopped unexpectedly ({reason}, exit code {code}).", { time, reason, code }) };
      }
      return { tone: "warning", text: t("Its last restart happened because it stopped unexpectedly ({reason}, exit code {code}).", { reason, code }) };
    }
    return null;
  }

  function eviction(reading, now) {
    const record = reading.last_eviction;
    if (!record) return null;
    const at = record.at ? new Date(record.at).getTime() : NaN;
    if (!Number.isNaN(at) && now - at > EVICTION_SHOWN_FOR_MS) return null;
    const time = formatWhen(record.at);
    const text = time
      ? t("Moved to another machine, {time}: the one it was running on ran short of memory or disk. Whatever it was doing at that moment was lost.", { time })
      : t("It was moved to another machine because the one it was running on ran short of memory or disk. Whatever it was doing at that moment was lost.");
    return { tone: "warning", text, detail: record.message || "" };
  }

  // The lines to draw for one reading, as plain data: { label, text, tone, detail }.
  function describe(reading, now) {
    const lines = [];
    if (!reading || typeof reading !== "object") return lines;
    const moment = typeof now === "number" ? now : Date.now();

    const reserved = amounts(reading.requests);
    if (reserved.length) lines.push({ label: t("Reserved"), text: reserved.join(" · ") });
    const limit = amounts(reading.limits);
    if (limit.length) lines.push({ label: t("Limit"), text: limit.join(" · ") });

    const usage = reading.usage;
    if (reading.usage_status === "ok" && usage) {
      const used = amounts(
        { cpu: usage.cpu, memory: usage.memory },
        { cpu: usage.cpu_percent_of_limit, memory: usage.memory_percent_of_limit },
      );
      if (used.length) lines.push({ label: t("In use now"), text: used.join(" · ") });
    } else if (reading.usage_status === "metrics_unavailable" || reading.usage_status === "metrics_forbidden") {
      lines.push({ label: t("In use now"), text: t("not reported by this cluster") });
    }

    const restarts = Number(reading.restart_count) || 0;
    if (restarts > 0) {
      lines.push({ text: tn(restarts, "Restarted once since this run began.", "Restarted {n} times since this run began.") });
    }
    const stop = lastStop(reading);
    if (stop) lines.push(stop);
    const moved = eviction(reading, moment);
    if (moved) lines.push(moved);
    return lines;
  }

  function draw(host, reading) {
    const html = describe(reading).map((line) => {
      const label = line.label ? `<span class="portal-resource-line-label">${esc(line.label)}</span> ` : "";
      const detail = line.detail ? `<span class="portal-resource-line-detail">${esc(line.detail)}</span>` : "";
      return `<div class="portal-resource-line${line.tone ? ` is-${esc(line.tone)}` : ""}">${label}${esc(line.text)}${detail}</div>`;
    }).join("");
    host.innerHTML = html;
    host.hidden = !html;
  }

  function hostFor(agentId) {
    const host = document.getElementById(HOST_ID);
    return host && host.dataset.agentId === String(agentId) ? host : null;
  }

  async function show(agentId) {
    if (!agentId) return;
    const cached = readings.get(agentId);
    const host = hostFor(agentId);
    if (host && cached) draw(host, cached.reading);
    if (cached && Date.now() - cached.at < FRESH_MS) return;

    let reading = null;
    try {
      const response = await fetch(`/api/agents/${encodeURIComponent(agentId)}/resources`);
      if (!response.ok) return;
      reading = await response.json();
    } catch (error) {
      // No reading is not an error to show: the pills above still stand.
      return;
    }
    readings.set(agentId, { reading, at: Date.now() });
    const current = hostFor(agentId);
    if (current) draw(current, reading);
  }

  window.AssistantResources = { show, describe };
})();
