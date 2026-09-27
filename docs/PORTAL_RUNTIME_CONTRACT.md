# Portal Runtime / Control-Plane Contract

For a member walkthrough, read the [Beginner Guide](BEGINNER_GUIDE.md). For installation and deployment, read the [Operations Guide](OPERATIONS_GUIDE.md). This document describes the integration boundary implemented by Portal.

## 1) Portal role
- Portal is the control plane: UI, proxy, agent/resource registry, and policy/connector-settings (runtime profile) coordination.
- Portal does **not** execute tools/skills itself, and does not own runtime-internal recovery algorithms.
- Runtime is exposed only through the EFP-compatible API surface on service port `:8000`.

## 2) Routing contract
- User-facing proxy route: `/a/{agent_id}/api/*`.
- Portal targets runtime service port `:8000`.
- For chat UX, Portal should prefer `POST /a/{agent_id}/api/chat/stream` (SSE) over waiting for long blocking JSON responses.
- The chat UI uses SSE for response text and a WebSocket at `/a/{agent_id}/api/events` for runtime activity, including tool, permission, question, and connector events. Reverse proxies must support both streaming and WebSocket upgrades.
- Portal checks the member's session and active allowlist entry, agent visibility/ownership, and running state. Administrators can access all agents. Workspace file operations and mutating session endpoints require an owner or administrator even when an agent is shared.
- User-facing proxy requests cannot reach runtime `api/internal/*`, removed SSH endpoints, or the removed direct `api/config` / `api/config/save` settings surface.

## 3) Runtime selection
- Portal supports two runtime markers: `native` and `opencode`.
- `native` provisions the Python `dvnuo/engineering-flow-platform` runtime image configured by Portal. `opencode` provisions the OpenCode-compatible runtime image configured by Portal.
- Missing, blank, or legacy stored runtime markers normalize to `native` where Portal serializes existing agent state. Invalid non-empty request values are rejected at request boundaries.
- `/api/agents/defaults` returns `default_runtime_type` and a `runtime_types` matrix with each supported runtime marker, label, configured default image, and default mount path.
- Creating an agent without an explicit `runtime_type` uses a valid `DEFAULT_RUNTIME_TYPE` when that marker is enabled, otherwise the first enabled marker. A blank value prefers `native` if enabled. An invalid non-empty setting makes create report a server configuration error; `/api/agents/defaults` still displays an enabled fallback.
- `ENABLED_RUNTIME_TYPES` (default `native`) lists the markers this Portal offers for new agents and for assistant types. `/api/agents/defaults` keeps every supported marker in `runtime_types`, each with an `enabled` flag, and adds the `enabled_runtime_types` list; `default_runtime_type` is always an enabled marker (the configured default when it is enabled, otherwise the first enabled one). Creating an agent or an assistant type with a marker that is not enabled is rejected with 422. Existing agents keep their runtime.
- Switching an existing agent between `native` and `opencode` is allowed. The target marker must be enabled (`ENABLED_RUNTIME_TYPES`); otherwise the request is rejected with 422. When `runtime_type` changes and the request does not explicitly set `image`, Portal updates the agent image to that runtime's configured default image.
- Both runtimes default to `/workspace` as the workspace mount path.
- The OpenCode runtime receives OpenCode-specific state, env, and mount wiring for its adapter, but Portal does not provide a source overlay.
- Portal does not expose multiple Python EFP versions, runtime source overlay, or advanced runtime/source settings controls.

## 4) Assets contract
- Skills directory: `/app/skills`.
- Portal provisions the full selected skill package tree into `/app/skills`; it does not parse skills and does not copy only `SKILL.md`.
- Root-layout skills repositories should expose packages as `<skill-name>/SKILL.md`, with ordinary package files such as `<skill-name>/scripts/...`, `<skill-name>/templates/...`, `<skill-name>/reference/...`, and `<skill-name>/examples/...` preserved.
- Nested layouts are selected with `DEFAULT_SKILL_REPO_SUBDIR`, for example `DEFAULT_SKILL_REPO_SUBDIR=skills` copies `repo/skills/.` directly into `/app/skills`.
- `DEFAULT_SKILL_ASSET_VERSION` is a rollout marker only. Changing it updates Deployment template annotations so pods restart and the skills initContainer reclones same-branch content.
- Workspace default: `/workspace`.
- Behavior assets are provisioned separately from skills. `DEFAULT_AGENT_SETTINGS_REPO_URL`, `DEFAULT_AGENT_SETTINGS_BRANCH`, and `DEFAULT_AGENT_SETTINGS_REPO_SUBDIR` select a package containing required `AGENTS.md` and `instructions/`. The initContainer copies these into the workspace and mirrors instructions into `<workspace>/.efp/instructions/`. Optional `portal/` files provide runtime-served welcome text and starter cards. `DEFAULT_AGENT_SETTINGS_ASSET_VERSION` is the corresponding rollout marker. Agent-specific repository selections can override defaults.
- Portal does not support a runtime source overlay. It runs the configured runtime image and does not clone runtime source into `/app/src` or `/app/.git`.
- Portal does not configure external tools repositories, branches, or mounts. Runtime built-in tools are runtime-owned.

## 5) State contract
- Portal K8s provisioning owns image, workspace, skill asset, and env wiring; runtime owns tools, skills execution, loop control, context shaping, compaction, sessions, permissions, and recovery behavior.
- For `native`, Portal sets `EFP_RUNTIME_SESSION_ROOT` under the configured workspace mount (`<workspace>/.efp/runtime`) so runtime sessions, checkpoints, todos, and chat artifacts persist on the agent PVC across pod restarts.
- For `opencode`, Portal keeps using the adapter/OpenCode state mounts (`EFP_ADAPTER_STATE_DIR` and `OPENCODE_DATA_DIR`) for compatibility state and upstream OpenCode data.
- Each member has exactly one runtime profile row (their connector settings; `runtime_profiles.owner_user_id` is unique), and every agent is bound to its owner's row (`agents.runtime_profile_id`). The Kubernetes Secret stays per row: `efp-profile-{runtime_profile_id}` with `config.json` and `revision`.
- Saving a connector (or `PATCH /api/runtime-profile`) updates that Secret, bumps the row `revision` when the config changed, and restarts the member's running agents that are idle. A busy agent (an active task, or an active chat/task execution updated within the last 2 hours) is not restarted: it keeps its old settings until the member restarts it. Portal records the revision each pod was (re)started with in `agents.profile_revision_applied`; a running agent with an older revision is reported as `settings_restart_pending: true` by `GET /api/agents/status` and `GET /api/agents/{id}/status`, and the UI shows **Restart to apply**. Stopped agents apply the settings on their next start.
- Runtimes project the canonical profile at boot. Readiness uses `/ready` on port 8000 after runtime profile projection succeeds.

## 6) Trace / observability contract
- Portal request middleware creates/binds `X-Trace-Id`.
- Portal forwards runtime trace + identity headers:
  - `X-Trace-Id`
  - `X-Span-Id`
  - `X-Parent-Span-Id`
  - `X-Portal-Task-Id`
  - `X-Portal-Dispatch-Id`
  - `X-Portal-User-Id`
  - `X-Portal-User-Name`
  - `X-Portal-Agent-Name`
- Runtime logs should consume these headers for cross-service correlation.
- Portal sanitizes header values and does not trust browser-spoofed identity/trace headers.
- For user-facing HTTP and WebSocket requests, Portal generates a fresh trace id.
- Browser-supplied `X-Trace-Id` / `X-Request-Id` are not forwarded to runtime as trusted trace ids.
- Runtime receives only Portal-generated trace headers from current log context.

## 7) Migrations / startup
- `alembic upgrade head` is required.
- The supplied Dockerfile's startup command runs migrations before Uvicorn. Direct local Uvicorn startup requires running migrations explicitly first. Application startup verifies schema readiness; it does not create missing tables automatically.
- Do not use `Base.metadata.create_all` as a startup shortcut.
- `runtime_type` DB `server_default` is only a backfill migration concern, not a head schema default contract.

## 8) Smoke / CI
- `integration/scripts/smoke_portal.sh` validates Portal-side contracts only.
- Live runtime contract validation belongs to runtime repo(s) or multi-repo integration.


## 9) Runtime response contract
Portal responsibility:
- Do not execute tools or enforce runtime tool permissions in Portal.
- Render runtime events (reasoning/tool/permission/continuation/progress) as runtime-origin telemetry in Thinking Process.
- Render `completion_state` + related diagnostics explicitly in chat UI for non-success outcomes (`blocked`, `incomplete`, `error`, `empty_final`), instead of presenting them as normal success responses.
- Portal remains control-plane/proxy only; it must not execute tools and must not implement runtime-internal recovery behavior.

Runtime responsibility:
- Never return success with an empty visible assistant response.
- Return non-success completion states such as `blocked`, `incomplete`, `empty_final`, or `error` when no final visible text is available.

## 10) Runtime profile/config contract
- Runtime profiles are Portal-owned only for concise integration context: `llm`, `proxy`, `jira`, `confluence`, `github`, `aws`, `jenkins`, `nexus`, `splunk`, `pgsql`, `mobile-auto`, `git`, and `debug`.
- Members edit these sections through settings connectors (one connector per service; see [Connectors Contract §7.1](CONNECTORS_CONTRACT.md#71-settings-connectors)), each of which writes only its own sections of the member's single row.
- `debug` is not a member setting: the rendered Secret always carries `debug: {"enabled": true, "log_level": "DEBUG"}`, replacing whatever an older row stored.
- The supported Portal providers are `github_copilot` and `ai_platform`. Portal stores and forwards provider, model, thinking/context defaults, Copilot API key, or AI Platform user credentials. AI Platform host/URI settings are supplied by deployment configuration when the runtime config is materialized. Provider/model normalization and any runtime-specific projection are Portal's responsibility; model execution is the runtime's responsibility.
- LLM provider/model/Copilot API key fields remain part of the supported profile contract alongside AI Platform credentials.
- Portal stores and forwards proxy and external integration sections that it owns. For the Python EFP runtime this includes enough config for runtime-side file generation:
  - `jira.enabled` and `jira.instances[]` with `name`, `url` (accepted from `url` or `base_url`), `username` (accepted from `username` or `email`), `password`, `token` (accepted from `token` or `api_token`), `project` (accepted from `project` or `project_key`), `api_version`, and per-instance `enabled`.
  - `confluence.enabled` and `confluence.instances[]` with `name`, `url` (accepted from `url` or `base_url`), `username` (accepted from `username` or `email`), `password`, `token` (accepted from `token` or `api_token`), `space` (accepted from `space` or `space_key`), and per-instance `enabled`.
  - `github.enabled`, `github.api_token` (accepted from `api_token`, `token`, or `access_token`), and `github.base_url`.
  - `nexus.enabled`, `nexus.default_instance` and `nexus.instances[]` with `name` (required, unique), `url`, `username`, `password` or `token`, and per-instance `enabled`.
  - `splunk.enabled`, `splunk.default_instance` and `splunk.instances[]` with `name`, `url` (the management API, port 8089), `token` or `username`+`password`, `default_index`, `default_earliest`, `max_results` (1..10000), and per-instance `enabled`.
  - `pgsql.enabled`, `pgsql.default_instance` and `pgsql.instances[]` with `name`, `host`, `port` (1..65535), `database`, `username`, `password`, `sslmode` (`require`, `verify-ca`, `verify-full`, `prefer`), and per-instance `enabled`. Secrets live only under `password` / `token`.
  - `mobile-auto.enabled`, `mobile-auto.defaults`, and `mobile-auto.browserstack` fields for BrowserStack REST/Appium credentials, proxy, BrowserStackLocal mode, and local binary path.
  - `git.user.name` and `git.user.email`.
- The Python runtime consumes the applied profile config and writes its own external tool files: `ATLASSIAN_CONFIG` / `~/.config/atlassian/config.json` for the `engineering-flow-platform-tools` `jira` and `confluence` CLIs, GitHub CLI host config for `gh`, mobile-auto BrowserStack config in `EFP_CONFIG`, and git user config for `git`.
- Portal does not write those runtime tool files or execute agent business tasks through `jira`, `confluence`, `gh`, `mobile-auto`, `BrowserStackLocal`, or `git`. Portal does perform control-plane operations such as authenticated connection tests and `git ls-remote` branch discovery; Kubernetes initContainers clone the selected behavior/skill assets.
- Portal drops low-level runtime internals for tools, skills, loop control, context shaping, compaction, prompt assembly, structured output, and runtime mode.
- Runtime profile projection and trusted chat metadata carry the sanitized profile context under `config` / `runtime_profile.config`. Profile Secrets store canonical configuration in `config.json`; runtimes project it at boot. If `EFP_CONFIG_KEY` is configured, sensitive values in that Secret are encrypted and the runtime requires the same key. This field encryption does not imply encryption of the Portal database.
- Browser-provided chat `metadata` is untrusted. Portal replaces it with server-owned runtime profile/config/authorization metadata.
- Trusted chat metadata also carries `portal_user` (`{id, username, display_name}`) for the signed-in member, built server-side from the session user. The runtime renders it into the model's system prompt so "my"/"me" resolves to the member rather than to the shared Jira/Confluence service account; browser-supplied `portal_user` / `portal_user_id` / `portal_user_name` values are dropped.

## 11) Runtime tool/catalog contract
- Runtime core tool ids are runtime-owned and should appear in `/api/capabilities` snapshots as `capability_type: "tool"` entries: `apply_patch`, `bash`, `edit`, `glob`, `grep`, `invalid`, `read`, `skill`, `task`, `todowrite`, `webfetch`, `write`.
- Removed legacy aliases such as `read_file`, `write_file`, `list_dir`, `shell_exec`, `shell_status`, `shell_kill`, `todo_write`, `task_status`, `task_cancel`, `skill_list`, and old `fetch` are runtime-owned compatibility decisions, not Portal controls.
- Runtime-owned built-in tools and adapter actions are runtime implementation details, not Portal asset provisioning.
- PR creation / adapter action availability is determined by runtime capability snapshot, built-in runtime tool surface, runtime profile, and permission policy.
- Portal may keep control-plane fallback aliases to avoid mapping gaps, but aliases must not imply a tools repo, tools index, any Portal-managed tools directory/env or external-tools manifest.

## 12) Runtime session API contract

All paths below have the Portal prefix `/a/{agent_id}`. Session contents remain runtime-owned; the Portal metadata registry is not a transcript store.

Core full proxy routes include `GET /a/{agent_id}/api/sessions`, `DELETE /a/{agent_id}/api/sessions/{session_id}`, and `GET /a/{agent_id}/api/sessions/{session_id}/chatlog`.

| Method | Runtime path | Portal use |
|---|---|---|
| GET | `/api/sessions` | List conversations |
| GET | `/api/sessions/{session_id}` | Load a conversation and recovery metadata |
| GET | `/api/sessions/{session_id}/chatlog` | Retrieve the runtime chat log |
| DELETE | `/api/sessions/{session_id}` | Delete a conversation |
| POST | `/api/sessions/{session_id}/rename` | Rename with `{ "name": "..." }` |
| GET | `/api/sessions/{session_id}/context-usage` | Approximate context usage and compaction eligibility |
| POST | `/api/sessions/{session_id}/compact` | Compact older turns when supported |
| POST | `/api/sessions/{session_id}/messages/{message_id}/edit/async` | Edit an earlier user turn and submit it again |
| POST | `/api/sessions/{session_id}/messages/{message_id}/delete-from-here` | Remove that turn and later turns |

The runtime reports whether manual compaction is supported and eligible. The UI disables it for unsupported, busy, or unauthorized sessions. Native runtime compaction is expected to create a recovery checkpoint first. Portal displays runtime recovery metadata and reconnects to active requests; it does not implement checkpoint recovery itself. Standalone summary, revert, and unrevert controls are not currently provided; the compaction action is the current UI for summarizing older conversation turns.

## 13) On-demand repository checkout contract
- Portal does **not** parse slash commands and does **not** clone user-requested business repositories during pod startup.
- Runtime adapters own slash command parsing and on-demand checkout flows (for example `/create-pull-request in git repo <url> from branch <head> to <base>`).
- Portal provisions skills assets with initContainers:
  - skills clone target: `/app/skills`
  - root layout: `<skill-name>/SKILL.md`
  - nested layout: `DEFAULT_SKILL_REPO_SUBDIR=skills`
- `GIT_TOKEN` remains initContainer-only for asset clone and is not injected into the main runtime container by default.
- Private business-repo checkout must be authorized by runtime-side provider/runtime-profile credentials (for example GitHub provider token), not by broad Portal/K8s git token injection into runtime.

## 14) Local connector contract

The browser submits top-level local connector hints with an interactive chat request. Portal validates them against the signed-in member's saved local connector settings (`user_connectors`) before injecting trusted runtime metadata. Settings connectors (Jira, GitHub, and the other services) are not sent per request; they reach the runtime through the profile Secret at boot (section 5). A compatible runtime requests the local action over events; the originating Portal browser tab calls its loopback bridge and sends the result back through Portal. The runtime pod does not call the member's loopback address directly. See the [Connectors Contract](CONNECTORS_CONTRACT.md) for envelopes and endpoints.

## 15) Chat attachment contract
- The chatbox uploads each attached file to the runtime before the message is sent, then passes the returned ids in the chat request's `attachments` array. Transcript metadata and retained attachment bytes are described below; finishing a run does not by itself remove the transcript's attachment links.
- Portal routes: `POST /a/{agent_id}/api/files/upload?session_id=...` (dedicated proxy: enforces `EFP_MAX_UPLOAD_MB` and the extension allowlist before forwarding) and `GET /a/{agent_id}/api/files/{file_id}/preview`; `POST .../api/files/parse`, `GET .../api/files/{file_id}` and `DELETE .../api/files/{file_id}` go through the generic proxy.
- Runtime routes (both runtimes): `POST /api/files/upload` (multipart `file` part → `201 {"success": true, "file_id", "filename", "content_type", "size"}`; `413` over the cap, `415` for a disallowed extension or unparseable bytes), `POST /api/files/parse` (`{"file_id"}`), `GET /api/files/{file_id}/preview`, `GET /api/files/{file_id}`, `DELETE /api/files/{file_id}`. A file bound to a session is only visible with that `session_id`.
- The allowlist is Portal-owned: `EFP_CHAT_UPLOAD_EXTENSIONS` (default `pdf,docx,xlsx,csv,txt,log,pptx,zip,md,yaml,yml,json,xml`, non-visual because the default model has no vision) renders the composer's file picker, is checked by the upload proxy, and is set together with `EFP_MAX_UPLOAD_MB` on every agent pod so the runtime accepts exactly the same set. Images (`jpg,jpeg,png,webp,gif`) go to the model as images when a deployment adds them; `pdf`/`docx`/`xlsx`/`pptx`/`csv` go through their parsers, a `zip` is projected as its listing plus the text files inside, other formats are projected as UTF-8 text; a listed binary format the runtime cannot parse is rejected at upload (`415`).
- A runtime that answers `404` on `/api/files/upload` predates this contract; the Portal reports that as `502` with an explicit "restart on a current runtime image" message.
- Transcript contract: the model receives the composed prompt (projected file context ahead of the question), but the persisted user turn carries `metadata.original_user_message`, `metadata.display_attachments` (`file_id`, `name`, `content_type`, `size`, `type`, `parsed`) and `metadata.internal_model_content_hidden`; `GET /api/sessions/{id}` surfaces them as `display_content` and `attachments`, and the Portal renders the member's words plus one chip per file. Attachment bytes stay on the runtime until the session is deleted (or `EFP_CHAT_UPLOAD_RETENTION_DAYS`, default 30, expires them), so a chip links to `GET /a/{agent_id}/api/files/{file_id}?session_id=...` (inline; `download=1` for a download) and the proxy passes the runtime's `Content-Disposition` through. The proxy hardens that response on its own as well: html, svg and xml are always sent as a download and every attachment response carries `X-Content-Type-Options: nosniff`, so an uploaded page never runs under the Portal's origin.
