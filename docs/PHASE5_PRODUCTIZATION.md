# Phase 5 Productization Closure

This document preserves the Phase 5 operational and integration contracts and reflects the current Portal implementation. Start with the [Operations Guide](OPERATIONS_GUIDE.md) for a complete installation or upgrade procedure, and the [Portal / Runtime Contract](PORTAL_RUNTIME_CONTRACT.md) for current runtime boundaries.

## 1) Upgrade path (Portal operators)

When upgrading an existing Portal deployment to a build that includes Phase 5 tables/fields:

1. Back up the database and record the current application revision, image, configuration, and required secrets. Back up persistent runtime data separately.
2. Stop writes during the migration window. Prepare the matching new application code and Alembic files.
3. Run migrations against the deployment's database before serving requests with the new application:

```bash
alembic upgrade head
```

4. Start the new application and verify sign-in, runtime readiness, and a small chat/task. The supplied container entrypoint runs migrations automatically before Uvicorn; direct Python starts require the explicit command above. For git-clone deployments, pin or record the clone revision as well as the image.
5. Verify internal execution-context endpoints are reachable only through trusted network paths. If rollback is necessary after a schema change, use a tested database restore/migration procedure; replacing the image alone is insufficient.

## 2) Runtime capability snapshot contract

Portal supports runtime-catalog alignment via `RUNTIME_CAPABILITY_CATALOG_SNAPSHOT_JSON`.

Accepted payload forms:

- object form
  - `catalog_version: string` (optional; `version` is accepted as an alias)
  - `supports_snapshot_contract: boolean` (optional)
  - `capabilities: list[capability_entry]` (`items` is accepted as an alias)
- list form
  - treated as `capabilities`

`capability_entry` fields:

- `capability_id` (or alias `id`) - required string
- `capability_type` (or alias `type`) - include a string such as `tool`, `skill`, `channel_action`, or `adapter_action` so the entry can be mapped correctly; omitted values parse as an empty type
- `logical_name` - optional string (used for tool/skill/channel mapping; `name`, `action_alias`, and `action` are fallback sources)
- `adapter_system` - optional string (canonical field for adapter action mapping)
- `action_alias` - optional string (canonical field for adapter action mapping)
- `external_system` - optional string (compatibility alias for `adapter_system`)
- `action` - optional string (compatibility alias for `action_alias`)
- `enabled` - optional boolean, default `true`
- `permission_state`, `runtime_compatibility`, `tool_mappings`, and `metadata` - optional runtime compatibility details; older `compatibility` and `opencode_compatibility` names are accepted

`catalog_source` is assigned by Portal's loader rather than trusted from a payload field. Invalid entries are skipped. Send a meaningful `capability_type` even though the parser accepts a missing type.

Blank settings, invalid JSON, non-object/list JSON, and empty objects/lists fall back to deterministic local seed mappings. A non-empty object with no usable capability entries still produces an empty snapshot; validate the entries before deployment instead of expecting this case to fall back.

Runtime capability snapshots should include the runtime-owned core tool surface as `capability_type: "tool"` entries. Current core tool ids to expect are:

`apply_patch`, `bash`, `edit`, `glob`, `grep`, `invalid`, `read`, `skill`, `task`, `todowrite`, `webfetch`, `write`.

Removed legacy aliases such as `read_file`, `write_file`, `list_dir`, `shell_exec`, `todo_write`, `skill_list`, and old `fetch` are compatibility decisions inside the runtime. They are not Portal-owned tool controls.

## 3) Runtime profile apply/config contract

Portal sends concise Portal-owned profile context under `runtime_profile.config`: LLM provider/model/thinking/context defaults and credentials, proxy, and external integration sections for Jira, Confluence, GitHub, AWS, Jenkins, Mobile/BrowserStack, Git, and debug.

Supported providers are GitHub Copilot and AI Platform. AI Platform credentials belong to the profile; its endpoints come from deployment settings. Portal normalizes `llm.provider` and `llm.model` and projects them for the selected runtime. Saving a profile updates its canonical `config.json` Secret and restarts bound running assistants; stopped assistants read it at their next start.

Runtime internals for tools, skills, loop control, context shaping, compaction, prompt assembly, structured output, and runtime mode are owned by the selected runtime (the Python EFP runtime or the OpenCode runtime) and are not Portal-managed profile settings. See the [current profile contract](PORTAL_RUNTIME_CONTRACT.md#10-runtime-profileconfig-contract) for projection and credential handling.

## 4) Portal container env contract

Portal container should be configured with:

- A stable, private `SECRET_KEY` for session signing
- `BOOTSTRAP_ADMIN_PASSWORD` when the initial administrator must be created
- A persistent `DATABASE_URL` and the storage backing it
- `PORTAL_INTERNAL_BASE_URL` when runtimes need Portal callbacks (usually Portal service DNS, for example `http://efp-portal-service.default.svc.cluster.local`); it is not required for every local Portal start
- Matching `EFP_CONFIG_KEY` on Portal and agent runtimes when profile Secret field encryption is enabled

## 5) Portal-managed runtime environment

Portal injects plain env:

- `PORTAL_INTERNAL_BASE_URL` (only when configured; omitted if empty)
- `EFP_RUNTIME_SESSION_ROOT` for native agents, pointing under the workspace mount (`<workspace>/.efp/runtime`) so session state survives pod restarts
- OpenCode-specific data/adapter state directories and timeouts for OpenCode agents

Agent runtimes also receive an optional `EFP_CONFIG_KEY` reference from `efp-agents-secret`. The broad `GIT_TOKEN` used by clone initContainers is not injected into runtime main containers. Provisioning details live in `app/services/k8s_service.py` and the [Operations Guide](OPERATIONS_GUIDE.md).

## 6) Kubernetes git credential key wiring

Portal runtime creation reads git credentials from `efp-agents-secret`.

Configurable selector key:

- `K8S_GIT_TOKEN_KEY` (default `GIT_TOKEN`)

Portal clone uses HTTPS + `GIT_ASKPASS` + token-only auth.
The askpass username response is fixed to `x-access-token`.
Clone URL is not rewritten to authenticated HTTPS and credentials are not embedded in URL.
If token is absent, clone proceeds with unauthenticated URL.

## 7) Session Metadata Registry semantics

Portal keeps runtime session metadata in `agent_session_metadata` using **composite key `(agent_id, session_id)`**.
This is intentionally agent-scoped and avoids collisions across different agents reusing the same external session id.

Internal APIs:

- `PUT /api/internal/agents/{agent_id}/sessions/{session_id}/metadata`
- `GET /api/internal/agents/{agent_id}/sessions/{session_id}/metadata`
- `GET /api/internal/agents/{agent_id}/sessions/metadata` with optional filters:
  - `latest_event_state`
  - `current_task_id`

Registry scope is metadata-only; it is not a full chat-history store.

Portal currently supports session list/load/delete/chatlog, rename, context usage, manual compaction when supported, and message edit/delete-from-here. These operations remain runtime-owned and are proxied with ownership checks for mutations. The [session API contract](PORTAL_RUNTIME_CONTRACT.md#12-runtime-session-api-contract) lists current endpoints; standalone summary, revert, and unrevert controls are not provided.

## 8) GitHub review stale terminal semantics

For GitHub review tasks superseded by newer PR `head_sha`:

- Portal marks superseded active tasks as `stale`.
- `stale` is a superseded terminal state (not `failed`).
- If runtime results arrive late for an already-stale task, Portal keeps `stale` and does not overwrite it back to `done`/`failed`.
