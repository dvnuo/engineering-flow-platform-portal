# Connectors Contract (Portal ↔ Runtime ↔ Local bridge)

Status: v1 (protocol_version 1). Companion to the [Portal / Runtime Contract](PORTAL_RUNTIME_CONTRACT.md). The first
connector type is `local_browser`; every transport-level name below is connector-type
agnostic so that further types only add a registry entry and a page module.

For installation and use, see the [Beginner Guide](BEGINNER_GUIDE.md). Portal code implements the page, proxy, and settings portions below. Runtime tool behavior and the loopback HTTP service are companion requirements implemented in the runtime/tools repositories; a Portal-only smoke test cannot validate them end to end.

## 0. Terms

| Term | Meaning |
|---|---|
| connector | A per-user capability that lives outside the agent pod (a program on the user's PC, or an external service). Stored in Portal table `user_connectors`, keyed by `(owner_user_id, connector_type)`. |
| connector_type | Stable string id, e.g. `local_browser`. |
| kind | `local` (needs the chat page as a bridge to the user's machine) or `remote` (Portal/runtime talk to the service directly). v1 implements `local` only. |
| client_id | Random id generated per Portal browser tab (`sessionStorage`). Identifies which tab (and therefore which machine) executes local requests for one chat turn. |
| bridge | The page-side dispatcher plus the local program it talks to. For `local_browser` the local program is `browser serve` from `engineering-flow-platform-tools`. |

## 1. Chat request (page → Portal)

The page adds a **top-level** `connectors` object to `POST /a/{agent_id}/api/chat` and
`POST /a/{agent_id}/api/chat/stream`. It is never placed inside `metadata` (the proxy replaces client metadata).

```json
{
  "message": "...", "session_id": "...", "request_id": "...",
  "connectors": {
    "local_browser": { "client_id": "tab-7f3a9c", "protocol_version": 1 }
  }
}
```

Rules
- Only connector types the user has **enabled** in Connectors settings are sent.
- The current Local browser page also requires a Chromium browser, the chat's **Browser on** toggle, and a reachable bridge. Keep the originating Portal tab open while a request is running.
- `client_id`: 1–64 chars, `[A-Za-z0-9_-]`.
- Absent or invalid `connectors` means "no connectors for this turn".

## 2. Runtime metadata injected by Portal (Portal → runtime)

`app/api/proxy.py` validates the object against `user_connectors` and injects the
server-trusted form into the runtime execution metadata:

```json
"metadata": {
  "connectors": {
    "local_browser": {
      "enabled": true,
      "client_id": "tab-7f3a9c",
      "protocol_version": 1,
      "config": { "auto_enable_in_new_chats": true, "preferred_port": 8765 }
    }
  },
  "enable_browser_tool": true
}
```

- A type that is not enabled for the user is **omitted** (not `enabled:false`).
- `enable_browser_tool` is set only when `connectors.local_browser` is present and the
  request is interactive chat. Runtime treats it like `enable_question_tool`.
- `config` comes from the member's saved server-side settings, not from the submitted hint. A non-integer or missing `protocol_version` defaults to 1; Portal does not otherwise negotiate the version here.

## 3. Runtime tool `browser`

Registered by the native runtime when `enable_browser_tool` is true (always visible to the
model in that case, even under an `enabled_tools` allowlist).

Arguments
```json
{ "action": "page.snapshot", "params": { "target_id": "…" }, "timeout_seconds": 60 }
```

| action | params | notes |
|---|---|---|
| `tab.list` | – | returns `{tabs:[{id,title,url,active}]}` |
| `tab.current` | – | |
| `tab.activate` | `target_id` | |
| `tab.open` | `url` | http/https only |
| `page.snapshot` | `target_id?` | visible text |
| `page.text` | `target_id?, selector?` | |
| `page.outline` | `target_id?` | headings/landmarks |
| `page.ax` | `target_id?` | accessibility refs (`ref`) usable by click/type |
| `page.find` | `role?, name?, text?, selector?` | |
| `page.extract` | `selector` | |
| `page.table` | `selector?` | |
| `page.wait` | `selector?` or `text?`, `timeout_seconds?` | |
| `page.click` | `ref` or `selector` | |
| `page.type` | `ref` or `selector`, `text` | |
| `page.select` | `ref` or `selector`, `value` | |
| `page.check` / `page.uncheck` | `ref` or `selector` | |
| `page.press` | `key`, optional `ref` or `selector` | |
| `page.screenshot` | `target_id?, full_page?` | returns `{mime:"image/jpeg", base64, width, height}`; longest side ≤ 1280 |
| `bookmark.list` | – | |
| `session.status` | – | |

Result (model-visible `content` is a rendered summary; structured `output` is below)
```json
{ "ok": true, "action": "page.snapshot", "target": {"id": "...", "title": "...", "url": "..."},
  "data": { ... connector-specific ... } }
```
Failure
```json
{ "ok": false, "action": "page.click", "error": { "code": "connector_timeout", "message": "...", "hint": "..." } }
```

Tool-level error codes: `connector_disabled` (no `connectors.local_browser` in metadata),
`connector_timeout` (no response within `timeout_seconds`, default 60, max 120),
`connector_cancelled` (run cancelled while waiting), `connector_error` (bridge returned
`ok:false`; `error` carries the bridge's code/message/hint verbatim, e.g. `session_busy`,
`devtools_unavailable`, `target_not_found`, `command_not_allowed`).

Page text in `content` is wrapped as `<page-content>…</page-content>`; the system prompt
states that page content is data, not instructions.

## 4. Events (runtime → Portal page)

Runtime publishes `tool.connector_requested` on the runtime event bus **while the tool is
waiting** (not after it returns). The gateway projects it to `connector.request` on
the runtime WebSocket `/api/events`, proxied to the page as `/a/{agent_id}/api/events`:

```json
{
  "type": "connector.request",
  "session_id": "…", "request_id": "…",
  "data": {
    "session_id": "…", "request_id": "…",
    "connector_type": "local_browser",
    "connector_request": {
      "id": "cr_…",
      "action": "page.snapshot",
      "params": { "target_id": "…" },
      "target_client_id": "tab-7f3a9c",
      "timeout_seconds": 60,
      "created_at": "2026-09-13T10:00:00Z"
    }
  }
}
```

`data.session_id` and `data.request_id` are mandatory (the events socket filters on them).
After the bridge answers, runtime emits `tool.connector_responded` → `connector.responded`
with `{ id, ok, duration_ms, error? }` for the timeline.

## 5. Respond endpoint (page → Portal proxy → runtime)

`POST /a/{agent_id}/api/sessions/{session_id}/connectors/respond`

Portal applies its normal session-mutation access check: the requester must be the assistant's owner or an administrator. A member using another person's public assistant cannot complete this response path; use an owned assistant for Local browser work.

```json
{ "request_id": "cr_…", "client_id": "tab-7f3a9c",
  "ok": true, "result": { ... } }
```
or
```json
{ "request_id": "cr_…", "client_id": "tab-7f3a9c",
  "ok": false, "error": { "code": "session_busy", "message": "...", "hint": "..." } }
```

Responses: `202 {"ok": true, "request_id": "..."}`; `409 {"error": "connector_request_not_pending"}`
when the id is unknown, already resolved, timed out, or the run was cancelled;
`400` on malformed JSON. `result` is limited to 2 MB.

## 6. Local bridge HTTP API (`browser serve`, 127.0.0.1)

Default port 8765; if busy the service tries 8766–8770. The page probes the member's configured `preferred_port` and the default 8765–8770 range, preferring the last working port when available.

| Method | Path | Body / Response |
|---|---|---|
| OPTIONS | `*` | `204` with `Access-Control-Allow-Origin: <origin>`, `Access-Control-Allow-Methods: GET, POST, OPTIONS`, `Access-Control-Allow-Headers: Content-Type`, `Access-Control-Allow-Private-Network: true`, `Access-Control-Max-Age: 600`, `Vary: Origin` |
| GET | `/ping` | `{ "ok": true, "data": { "version": "0.1.0", "protocol_version": 1, "session": { "name": "default", "alive": true, "debug_port": 57848, "tab_count": 3 } } }` |
| GET | `/commands` | `{ "ok": true, "data": { "commands": ["tab.list", "page.snapshot", …] } }` |
| POST | `/run` | request `{ "command": "page.snapshot", "params": {…}, "session": "default", "timeout_seconds": 30 }` → the CLI JSON envelope `{ ok, data }` or `{ ok, error }`; HTTP status = `error.status` when present, else 200 |

Rules
- The response headers above support cross-origin loopback calls and Private Network Access preflight. Browser policy can also require Local network access permission. `Access-Control-Allow-Origin` echoes the configured `--origin` (a single
  origin; `*` is accepted by the CLI but not recommended).
- Requests whose `Origin` header does not match the configured origin get `403`.
- Only tab/page/bookmark.list/session.status/session.ensure commands are exposed; session
  lifecycle beyond `session.ensure` is not. `session.ensure` reopens the managed Chrome window
  after the member closed it (the bridge outlives the window, so `/ping` keeps answering with
  `session.alive: false`) and brings a tab at the Portal origin to the front without adding
  tabs; it returns `{ session, reused, target, tab_opened }`. The page calls it (timeout 60)
  when the bridge is reachable but the window is closed, instead of the protocol link.
- A tab or page command that finds the window closed (`session_not_running`, or
  `session_not_found` after `browser session stop`) is retried once after the bridge reopens
  the window; `session.status` reports the closed window as is.
- Requests for the same `session` are executed one at a time.
- Screenshots come back as JPEG base64, longest side 1280.
- The page carries `client_id` only to the Portal, never to the bridge.

Protocol link: `efp-bridge://start?origin=<urlencoded Portal origin>&port=8765[&url=<urlencoded start page>]`
runs `browser.exe bridge-launch "<url>"`, which starts `browser serve --origin … --port … --url …`
if it is not already running and asks a running bridge whose window is closed to reopen it
(registration: `browser serve --register-protocol --origin <origin>`). The bridge launches
Chrome directly on the start page, so the window opens with that single tab. The `url`
parameter is `LOCAL_BROWSER_START_URL` (§8) resolved by the page against its own origin; it
must be absolute http(s) and is omitted when the setting is empty (the bridge then opens the
origin). The page passes the same value as `session.ensure{url}` so a bridge started before
the setting changed reopens on the current page.

## 7. Portal Connectors API

| Method | Path | Notes |
|---|---|---|
| GET | `/api/connectors` | list of registry types with the current user's state: `[{type, label, kind, category, description, enabled, config, settings, last_verified_at}]`; `settings` holds deployment-level values the page needs (read-only; `local_browser`: `{start_url}` raw from `LOCAL_BROWSER_START_URL`) |
| GET | `/api/connectors/{type}` | one entry |
| PUT | `/api/connectors/{type}` | `{ "enabled": true, "config": { … } }`; config validated against the type's schema |
| POST | `/api/connectors/{type}/verify` | request `{ "ok": true, "details": {…} }` from the page's own probe; response `{ "ok": true, "last_verified_at": "..." }`. Portal remembers the latest successful verification; it does not probe the member's PC itself |
| GET | `/app/connectors/{type}/panel` | htmx panel |

`local_browser` config schema: `{ "auto_enable_in_new_chats": bool (default true), "preferred_port": int 1024–65535 (default 8765) }`.

Unconfigured connectors are listed as disabled with their default settings. Unknown connector types return 404; invalid config keys return 400. A malformed request model can return 422. When `CONNECTORS_ENABLED=false`, the settings API returns 404 and the menu is hidden.

## 8. Configuration

| Where | Key | Default | Purpose |
|---|---|---|---|
| Portal env | `LOCAL_BROWSER_CLI_DOWNLOAD_URL` | empty → `/static/downloads/efp-browser-bridge-{platform}.zip` | download link template; `{platform}` is one of `windows-amd64`, `windows-arm64`, `darwin-arm64`, `darwin-amd64`, `linux-amd64`, `linux-arm64` (tools `scripts/browser-bridge/package.sh` builds one zip per platform: binary, installer, README). The panel offers the member's own system first (User-Agent, refined by client hints on the page) and lists the rest |
| Portal env | `LOCAL_BROWSER_CLI_VERSION` | empty | shown on the panel |
| Portal env | `LOCAL_BROWSER_START_URL` | empty → Portal origin | first tab of the EFP window when the bridge opens or reopens it; absolute http(s) URL or a path resolved against the Portal origin; sent as the link's `url` and as `session.ensure{url}` |
| Portal env | `CONNECTORS_ENABLED` | `true` | hides the Connectors menu and returns 404 from its settings API when false |
| tools CLI | `EFP_BROWSER_SERVE_PORT`, `EFP_BROWSER_SERVE_ALLOWED_ORIGIN` | 8765, empty | defaults for `browser serve` |
| runtime | `enable_browser_tool` (Portal-managed runtime field) | false | registers the tool |

## 9. Timeline rendering (Portal)

`connector.request` → one tool row "Browser: page.snapshot → <tab title>"; `connector.responded`
updates it with ok/failed and duration. No page content is rendered in the timeline.

## 10. Versioning

`protocol_version` is carried by the page (§1), the bridge `/ping` (§6) and the tool result.
v1 = 1. The current Portal page records the bridge version but does not reject a reachable bridge solely because its reported protocol version is lower. Deploy matching bridge/runtime versions and verify them together; do not rely on automatic version negotiation or an upgrade warning.

## 11. Package availability

The default download URLs are links, not a package installer or downloader in Portal. The checked-in downloads directory contains a README only, and the current Portal image workflow does not fetch bridge archives. Operators must populate the archives before building/serving the image, mount them into the served directory, or set `LOCAL_BROWSER_CLI_DOWNLOAD_URL` to real published packages. In a deployment that overlays `app/` from Git, packages in the image can be hidden by that mount. See the [downloads note](../app/static/downloads/README.md).
