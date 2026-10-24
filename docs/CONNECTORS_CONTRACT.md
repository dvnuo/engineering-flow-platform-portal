# Connectors Contract (Portal ↔ Runtime ↔ Local bridge)

Status: v1 (protocol_version 1). Companion to the [Portal / Runtime Contract](PORTAL_RUNTIME_CONTRACT.md). Connectors
are the one place a member configures what their assistants can reach. There are two kinds (registry:
`app/services/connector_registry.py`): **settings** connectors for services the assistant signs in to from its pod
(the model provider, Jira, GitHub, AWS, ...; see §7.1), and **local** connectors for a program on the member's own PC.
The first local connector type is `local_bridge`. Sections 1–6 and 9–11 describe the local transport. Every
transport-level name there is connector-type agnostic, so further local types only add a registry entry and a
page module.

For installation and use, see the [Beginner Guide](BEGINNER_GUIDE.md). Portal code implements the page, proxy, and settings portions below. Runtime tool behavior and the loopback HTTP service are companion requirements implemented in the runtime/tools repositories; a Portal-only smoke test cannot validate them end to end.

## 0. Terms

| Term | Meaning |
|---|---|
| connector | A per-user capability an assistant can use. A **local** connector's settings are stored in Portal table `user_connectors`, keyed by `(owner_user_id, connector_type)`, and never enter a pod. A **settings** connector's values are sections of the member's single settings row (`runtime_profiles`, §7.1). |
| connector_type | Stable string id, e.g. `local_bridge`, `jira`, `llm`. |
| kind | `local` (needs the chat page as a bridge to the user's machine) or `settings` (the runtime talks to the service directly with credentials delivered through the pod Secret at boot). |
| client_id | Random id generated per Portal browser tab (`sessionStorage`). Identifies which tab (and therefore which machine) executes local requests for one chat turn. |
| bridge | The page-side dispatcher plus the local program it talks to. For `local_bridge` (shown as **Local bridge**) the local program is `efp-bridge` from `engineering-flow-platform-tools` (its `docs/BRIDGE.md`): browser automation for the runtime's `browser` tool, and mobile recording, which the Mobile testing panel calls directly. |

## 1. Chat request (page → Portal)

The page adds a **top-level** `connectors` object to `POST /a/{agent_id}/api/chat` and
`POST /a/{agent_id}/api/chat/stream`. It is never placed inside `metadata` (the proxy replaces client metadata).

```json
{
  "message": "...", "session_id": "...", "request_id": "...",
  "connectors": {
    "local_bridge": { "client_id": "tab-7f3a9c", "protocol_version": 1 }
  }
}
```

Rules
- Only local connector types the user has **enabled** in Connectors are sent. Settings connectors never travel in this object; they reach the runtime through the pod Secret (§7.1).
- The current Local bridge page also requires a Chromium browser, the chat's **Browser on** toggle, and a reachable bridge. Keep the originating Portal tab open while a request is running.
- `client_id`: 1–64 chars, `[A-Za-z0-9_-]`.
- Absent or invalid `connectors` means "no connectors for this turn".
- The same top-level object rides on `POST /a/{agent_id}/api/sessions/{session_id}/question/respond` and
  `.../permission/respond` (sent by `interactive_input.js`): an answer to a card resumes the parked turn on a
  request of its own, and the resumed run can reach the member's browser only if the answering tab is named again.

## 2. Runtime metadata injected by Portal (Portal → runtime)

`app/api/proxy.py` validates the object against `user_connectors` and injects the
server-trusted form into the runtime execution metadata:

```json
"metadata": {
  "connectors": {
    "local_bridge": {
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
- `enable_browser_tool` is set only when `connectors.local_bridge` is present and the
  request is interactive chat. Runtime treats it like `enable_question_tool`.
- The proxy gives the two respond posts of §1 the same treatment as a chat post: client `metadata` is
  dropped, the server-owned runtime metadata, identity, and the session's run settings go in, then the
  connectors are injected. The runtime also keeps the parked turn's connectors (`pending_input_context` in
  session metadata, written when a run stops on a card) and falls back to them when an answer names none,
  so a Portal that predates this still resumes the turn with the browser tool; an answer that names
  connectors wins, since it says which tab is answering.
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
| `page.upload` | `ref` or `selector`, `files: ["output/report.pdf"]` (workspace-relative, at most 10, each within `EFP_MAX_UPLOAD_MB`), `clear?` | the runtime checks each file against the workspace and sends `files: [{path, name, bytes}]` to the page, which stages the bytes on the bridge (section 6) and runs the command with `file_ids`; returns `{file_count, files: [{id, name, bytes}], selector, title, url}`; tool errors `file_not_found`, `file_outside_workspace`, `file_too_large`, `too_many_files`, `no_workspace`; page errors `bridge_outdated`, `file_fetch_failed`; bridge errors `file_not_staged`, `payload_too_large` |
| `page.screenshot` | `target_id?, full_page?` | returns `{mime:"image/jpeg", base64, width, height}`; longest side ≤ 1280 |
| `workflow.run` | `workflow: "<skill>/workflow.yaml"` (read from the workspace, else from a discovered skill directory) or `workflow_yaml`, `vars?: {name: value}`, `files?: {var: "workspace/path" \| ["workspace/path", …]}` (a list stages several files for one variable), `max_total_bytes?` (the sum of the files' sizes, `files_too_large` above it), `yes?`, `dry_run?`, `continue_on_error?`, `target_id?` | the runtime sends `{workflow_yaml, vars, files: [{var, path, name, bytes}], yes, dry_run, continue_on_error, workflow?, target_id?}` (one entry per file; a list variable repeats its `var`); the page stages the files and merges their ids into `vars` before running, the ids of one variable joined with commas, which the bridge splits; returns the bridge's workflow result (`pass, status, steps[], failed_step, duration_ms`); a failed playbook is `workflow_failed` with the result as `error.data`, which the tool renders step by step; default `timeout_seconds` 90; tool errors `workflow_not_found`, `workflow_too_large`, `files_too_large`, plus the file errors above |
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

Tool-level error codes: `connector_disabled` (no `connectors.local_bridge` in metadata),
`connector_timeout` (no answer from the page within `timeout_seconds` plus the runtime's grace: 30 s, plus one
second per MiB of files the page stages first; `timeout_seconds` itself, default 60, 90 for `workflow.run`, max
120, is the bridge's budget for the command, so a wait that runs out on the bridge comes back as the bridge's
own `timeout` or `workflow_failed`, and `connector_timeout` means the page did not answer at all),
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
    "connector_type": "local_bridge",
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

For `page.upload` and `workflow.run`, `connector_request.params.files` carries
`{path, name, bytes}` entries (`{var, path, name, bytes}` for a playbook), never file
contents: the page fetches each file itself through
`GET /a/{agent_id}/api/server-files/download?paths=<path>` (the Server files download,
with the member's own session) and stages it on the bridge before running the command.
The timeline row shows the file count and, for a playbook, its path.

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

## 6. Local bridge HTTP API (`efp-bridge serve`, 127.0.0.1)

Default port 8765; if busy the service tries 8766–8770. The page probes the member's configured `preferred_port` and the default 8765–8770 range, preferring the last working port when available.

| Method | Path | Body / Response |
|---|---|---|
| OPTIONS | `*` | `204` with `Access-Control-Allow-Origin: <origin>`, `Access-Control-Allow-Methods: GET, POST, OPTIONS`, `Access-Control-Allow-Headers: Content-Type`, `Access-Control-Allow-Private-Network: true`, `Access-Control-Max-Age: 600`, `Vary: Origin` |
| GET | `/ping` | `{ "ok": true, "data": { "version": "0.1.0", "protocol_version": 1, "capabilities": ["browser", "mobile", "browser_files", "workflow"], "browser": { "files": true, "workflow": true, "max_file_bytes": 104857600, "workflow_actions": [...] }, "session": { "name": "default", "alive": true, "debug_port": 57848, "tab_count": 3 } } }` |
| GET | `/commands` | `{ "ok": true, "data": { "commands": ["tab.list", "page.snapshot", …] } }` |
| POST | `/run` | request `{ "command": "page.snapshot", "params": {…}, "session": "default", "timeout_seconds": 30 }` → the CLI JSON envelope `{ ok, data }` or `{ ok, error }`; HTTP status = `error.status` when present, else 200 |
| POST | `/browser/files` | `multipart/form-data` with one part named `file` (its filename is the name the page will see) → `{ "ok": true, "data": { "file": { "id", "name", "bytes", "expires_at" } } }`; `413 payload_too_large` over `max_file_bytes`, `429 too_many_files` past 50 staged files. A staged file lives on the member's computer for 15 minutes after it was staged or last used and goes when the bridge stops |

Rules
- The response headers above support cross-origin loopback calls and Private Network Access preflight. Browser policy can also require Local network access permission. `Access-Control-Allow-Origin` echoes the configured `--origin` (a single
  origin; `*` is accepted by the CLI but not recommended).
- Requests whose `Origin` header does not match the configured origin get `403`.
- Only tab/page/bookmark.list/session.status/session.ensure commands plus `page.upload` and
  `workflow.run` are exposed; session lifecycle beyond `session.ensure`, `page.eval`,
  `page.fetch`, `workflow.record`, and downloads are not. `page.upload{ref|selector, file_ids,
  clear?}` attaches staged files by id only (`404 file_not_staged` for anything else, a path on
  the member's computer included). `workflow.run{workflow_yaml, vars?, yes?, dry_run?,
  continue_on_error?, target_id?}` runs a playbook on the bridge's session and answers the
  browser CLI's workflow result, `412 workflow_failed` with that result as `data` when a step
  fails; through the bridge a playbook uses only the actions `/ping` lists as
  `browser.workflow_actions` (`400 workflow_step_not_allowed` otherwise), `page.extract_schema`
  declares its `fields` inline, and a `page.upload` step's file values are staged ids (the page
  fills them in through `vars`). A bridge whose `/ping` lacks `browser_files` or `workflow` is
  reported to the assistant as `bridge_outdated` by the page. The page gives a `/run` call the
  request's `timeout_seconds` plus five seconds, never less than 65 seconds, so a long playbook
  is not cut off by the page before the bridge answers. `session.ensure` reopens the managed Chrome window
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
runs `efp-bridge launch "<url>"`, which starts `efp-bridge serve --origin … --port … --url …`
if it is not already running and asks a running bridge whose window is closed to reopen it
(registration: `efp-bridge register --origin <origin>`). The bridge launches
Chrome directly on the start page, so the window opens with that single tab. The `url`
parameter is `LOCAL_BRIDGE_BROWSER_START_URL` (§8) resolved by the page against its own origin; it
must be absolute http(s) and is omitted when the setting is empty (the bridge then opens the
origin). The page passes the same value as `session.ensure{url}` so a bridge started before
the setting changed reopens on the current page.

## 7. Portal Connectors API

| Method | Path | Notes |
|---|---|---|
| GET | `/api/connectors` | list of registry types with the current user's state, ordered by category: `[{type, label, kind, category, description, icon, enabled, state, status_label, mode, config, settings, last_verified_at}]`. `mode` is `system` or `custom` for settings connectors (section 7.1), null for local ones. `state` is `connected`, `off`, or `not_set_up`; `status_label` is its display text. `settings` holds deployment-level values the page needs (read-only; `local_bridge`: `{start_url}` raw from `LOCAL_BRIDGE_BROWSER_START_URL`) |
| GET | `/api/connectors/{type}` | one entry |
| PUT | `/api/connectors/{type}` | local connectors only: `{ "enabled": true, "config": { … } }`; config validated against the type's schema |
| POST | `/api/connectors/{type}/verify` | local connectors only: request `{ "ok": true, "details": {…} }` from the page's own probe; response `{ "ok": true, "last_verified_at": "..." }`. Portal remembers the latest successful verification; it does not probe the member's PC itself |
| GET | `/app/connectors/{type}/panel` | htmx panel (both kinds; §7.1 for settings connectors) |

`local_bridge` config schema: `{ "auto_enable_in_new_chats": bool (default true), "preferred_port": int 1024–65535 (default 8765) }`.

The API is always served. Unconfigured local connectors are listed as disabled (`state: not_set_up`) with their default settings. Unknown connector types return 404; invalid config keys return 400. A malformed request model can return 422. When `CONNECTORS_ENABLED=false`, local connectors are omitted from the list and `GET`/`PUT`/`verify` on them return 404; the Connectors menu and the settings connectors stay available.

### 7.1 Settings connectors

| type | label | category | settings keys it owns | form sections | tests |
|---|---|---|---|---|---|
| `llm` | Model provider | Model | `llm` | `llm` | `llm` |
| `jira` | Jira | Work tracking & docs | `jira` | `jira` | `jira` |
| `confluence` | Confluence | Work tracking & docs | `confluence` | `confluence` | `confluence` |
| `github` | GitHub | Code & delivery | `github`, `git` | `github`, `git` | `github` |
| `jenkins` | Jenkins | Code & delivery | `jenkins` | `jenkins` | `jenkins` |
| `nexus` | Nexus Repository | Code & delivery | `nexus` | `nexus` | `nexus` |
| `aws` | AWS | Cloud & data | `aws` | `aws` | – |
| `splunk` | Splunk | Cloud & data | `splunk` | `splunk` | `splunk` |
| `pgsql` | PostgreSQL | Cloud & data | `pgsql` | `pgsql` | `pgsql` |
| `browserstack` | BrowserStack | Testing | `mobile-auto` | `mobile` | – |
| `proxy` | Proxy | Network | `proxy` | `proxy` | `proxy` |

Storage: every member has exactly one settings row in `runtime_profiles` (unique `owner_user_id`), created from the
admin's **Default connectors** seed on first use. Each settings connector reads and writes only its own keys of that
row's `config_json`. Every assistant the member owns is bound to the row (`agents.runtime_profile_id`).

Connector modes: every settings connector of a member is in one of two modes, kept in
`runtime_profiles.connector_modes_json` as `{type: "custom"}` (a type that is absent is `system`):

- `system`: the connector's values are the administrator's **Default connectors** as they are *now*. Nothing is copied
  into the row; wherever the row is read (panel, connection test, the pod Secret, `GET /api/runtime-profile`) the
  connector's `managed_sections` are taken from the seed. A new member, and a connector type that appears later, start
  here. The seed having nothing for the connector means the connector is not set up.
- `custom`: the member's own values, in the row. The administrator's later changes do not touch them.

`ConnectorSpec.managed_sections` says which sections the mode governs: every section the connector owns, except
that `github` manages `github` but not the commit identity `git`, and `llm` manages nothing (the model provider is
always the member's own, so a changed shared key or password reaches only new members). A member's first row
therefore holds only the seed's `llm` (and `git`) sections.

When the administrator saves the Default connectors, the members whose connectors in `system` mode changed are
updated after the response (FastAPI background task, `connector_defaults_sync.sync_followers`): their row's
`revision` is bumped and the save rollout runs for each of them (Secret, idle restarts, "Restart to apply" on busy
assistants), one member at a time, a failure never stopping the rest; one `sync_connector_defaults` audit row
carries the counts. A seed a member's own Save would refuse (a value the sanitizer cannot store, a default instance
or account or an EKS row naming nothing) is refused on save, since members in `system` mode would receive it as is.

Rows from before modes existed (`connector_modes_json` NULL) count as `custom` everywhere until startup classifies
them (`RuntimeProfileService.backfill_connector_modes`): a connector whose row values equal the current seed follows
it from then on and its copy leaves the row; one whose values differ stays the member's own. Nothing an assistant
sees changes and no revision moves. Writing a connector's values (the panel's Save, `PATCH /api/runtime-profile`)
makes that connector `custom`, unless the values written are the seed's own, which leaves the mode as it is.

Delivery: the row is rendered into the Kubernetes Secret `efp-profile-{row id}`, which the assistant's pod reads **at
boot**. A running pod does not see a change until it restarts. The renderer always writes
`debug: {enabled: true, log_level: "DEBUG"}`; debug logging is not a connector.

Routes (session-cookie web routes; `{type}` must be a settings connector, otherwise 404):

| Method | Path | Behaviour |
|---|---|---|
| GET | `/app/connectors/{type}/panel` | `partials/connectors/panel.html` wrapping `partials/connectors/<type>.html`: a header with the connector's state, the restart notice (below), the mode switch (below; not on Model provider), the form, and **Save**. In `system` mode the form is read-only (`chat_ui.js applySystemDefaultReadOnly`) and shows the seed's values; Save is not offered and a posted save is refused |
| POST | `/app/connectors/{type}/save` | merges the posted form into the stored row. Only the `__touch_<section>` flags for this connector's form sections are honoured, so a save never rewrites another connector's keys. An unchanged save replies "Saved. Nothing changed." without restarting anything. A change bumps the row's `revision`, is audited, updates the Secret, and applies the restart policy. The response is the re-rendered panel (status text starting "Saved.") with header `HX-Trigger: connectorsChanged` |
| POST | `/app/connectors/{type}/test/{target}` | runs a connection test with the posted (unsaved) form merged over the stored values; JSON `{ok, target, message}`. 404 when `target` is not one of the connector's tests |
| POST | `/app/connectors/{type}/mode` | form field `mode`. `custom`: the connector becomes the member's own, starting from the current seed values (copied into the row) or from the values parked in the row since they last followed the system default; nothing the assistants see changes, so no restart. `system`: the connector follows the Default connectors again (the member's own values stay parked in the row); when the settings the assistants get change, the `revision` is bumped, a `follow_connector_defaults` audit row is written (sections and changed secret paths, no values), the Secret is updated and the restart policy applies, as for a save. The response is the re-rendered panel with `HX-Trigger: connectorsChanged`. 404 for `llm`, unknown and local connectors; 400 for another `mode` |

The mode switch on the panel: in `system` mode a **Customize** button (posts `mode=custom`); in `custom` mode a
**Use system default** button that asks for a confirmation first (`hx-confirm`, routed through the styled dialog by
`dialogs.js`, which reads `data-confirm-title`, `data-confirm-ok` and `data-confirm-danger` from the element). The
Connectors list marks connectors in `system` mode with "System default" (`mode` in `GET /api/connectors`).

Restart policy on save: running assistants bound to the row that are idle are restarted at once. A busy assistant
(an active task, or an active chat/task execution that reported within the last 2 hours) is not interrupted: it keeps
its old settings and is reported as pending. Stopped assistants read the new Secret when they next start. Portal
records the row revision each pod was started with (`agents.profile_revision_applied`). A running assistant with an
older revision shows **Restart to apply** in the assistant list and status card
(`settings_restart_pending: true` in `GET /api/agents/status` and `GET /api/agents/{id}/status`), and every settings
connector panel lists it under "Restart to apply your latest connector changes" with a **Restart** button.

Connectors API behaviour for settings connectors: they are always listed (regardless of `CONNECTORS_ENABLED`), their
`state` is read from the member's row, and they never return values (`config` and `settings` are `{}`,
`last_verified_at` is null). `PUT` and `verify` on them return 400; they are edited only through their panels. API
clients that need the whole document can use `GET`/`PATCH /api/runtime-profile` (see the
[Operations Guide](OPERATIONS_GUIDE.md#14-explore-the-api)).

## 8. Configuration

| Where | Key | Default | Purpose |
|---|---|---|---|
| Portal env | `LOCAL_BRIDGE_DOWNLOAD_URL` | empty → `/static/downloads/efp-bridge-{platform}.zip` | download link template; `{platform}` is one of `windows-amd64`, `windows-arm64`, `darwin-arm64`, `darwin-amd64`, `linux-amd64`, `linux-arm64` (tools `scripts/local-bridge/package.sh` builds one zip per platform: `efp-bridge`, `mobile-auto`, installer, README). The panel offers the member's own system first (User-Agent, refined by client hints on the page) and lists the rest |
| Portal env | `LOCAL_BRIDGE_VERSION` | empty | shown on the panel |
| Portal env | `LOCAL_BRIDGE_BROWSER_START_URL` | empty → Portal origin | first tab of the EFP window when the bridge opens or reopens it; absolute http(s) URL or a path resolved against the Portal origin; sent as the link's `url` and as `session.ensure{url}` |
| Portal env | `CONNECTORS_ENABLED` | `true` | when false, hides the local connectors: omitted from the Connectors list and `/api/connectors`, 404 from their API routes, and no chat **Browser** toggle. The Connectors menu and the settings connectors are unaffected |
| efp-bridge | `EFP_BRIDGE_PORT`, `EFP_BRIDGE_ALLOWED_ORIGIN` (config `bridge.port`, `bridge.allowed_origin`) | 8765, empty | defaults of `efp-bridge serve`; `efp-bridge register` stores the origin |
| runtime | `enable_browser_tool` (Portal-managed runtime field) | false | registers the tool |

## 9. Timeline rendering (Portal)

`connector.request` → one tool row "Browser: page.snapshot → <tab title>"; `connector.responded`
updates it with ok/failed and duration. No page content is rendered in the timeline.

## 10. Versioning

`protocol_version` is carried by the page (§1), the bridge `/ping` (§6) and the tool result.
v1 = 1. The current Portal page records the bridge version but does not reject a reachable bridge solely because its reported protocol version is lower. Deploy matching bridge/runtime versions and verify them together; do not rely on automatic version negotiation or an upgrade warning.

## 11. Package availability

The default download URLs are links, not a package installer or downloader in Portal. The checked-in downloads directory contains a README only, and the current Portal image workflow does not fetch bridge archives. Operators must populate the archives before building/serving the image, mount them into the served directory, or set `LOCAL_BRIDGE_DOWNLOAD_URL` to real published packages. In a deployment that overlays `app/` from Git, packages in the image can be hidden by that mount. See the [downloads note](../app/static/downloads/README.md).
