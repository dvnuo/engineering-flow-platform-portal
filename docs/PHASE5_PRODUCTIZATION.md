# Phase 5 Productization Closure

This document captures the **operational closing items** for Portal Phase 5 work without re-implementing the phase feature set.

## 1) Upgrade path (Portal operators)

When upgrading an existing Portal deployment to a build that includes Phase 5 tables/fields:

1. Back up DB.
2. Deploy the new image.
3. Run migrations:

```bash
alembic upgrade head
```

4. Verify new internal execution-context endpoints are reachable only inside trusted network paths.

## 2) Runtime capability snapshot contract

Portal supports runtime-catalog alignment via `RUNTIME_CAPABILITY_CATALOG_SNAPSHOT_JSON`.

Accepted payload forms:

- object form
  - `catalog_version: string` (optional)
  - `catalog_source: string` (optional)
  - `supports_snapshot_contract: boolean` (optional)
  - `capabilities: list[capability_entry]`
- list form
  - treated as `capabilities`

`capability_entry` fields:

- `capability_id` (or alias `id`) - required string
- `capability_type` (or alias `type`) - required string
- `logical_name` - optional string (used for tool/skill/channel mapping)
- `adapter_system` - optional string (canonical field for adapter action mapping)
- `action_alias` - optional string (canonical field for adapter action mapping)
- `external_system` - optional string (compatibility alias for `adapter_system`)
- `action` - optional string (compatibility alias for `action_alias`)

If parsing fails or data is missing, Portal falls back to deterministic local seed mappings.

Runtime capability snapshots should include the runtime-owned core tool surface as `capability_type: "tool"` entries. Current core tool ids to expect are:

`apply_patch`, `bash`, `edit`, `glob`, `grep`, `invalid`, `read`, `skill`, `task`, `todowrite`, `webfetch`, `write`.

Removed legacy aliases such as `read_file`, `write_file`, `list_dir`, `shell_exec`, `todo_write`, `skill_list`, and old `fetch` are compatibility decisions inside the runtime. They are not Portal-owned tool controls.

## 3) Runtime profile apply/config contract

Portal now sends only concise Portal-owned profile context under `runtime_profile.config`: LLM provider/model/Copilot API key fields, proxy, and external integration sections for Jira, Confluence, GitHub, Git, and debug.

Portal keeps the managed Copilot provider/model projection, including `llm.provider` and `llm.model`, without coupling it to a runtime selection UI.

Runtime internals for tools, skills, loop control, context shaping, compaction, prompt assembly, structured output, and runtime mode are owned by the Python EFP runtime and are not Portal-managed profile settings.

## 4) Portal container env contract

Portal container should be configured with:

- `BOOTSTRAP_ADMIN_PASSWORD`
- `PORTAL_INTERNAL_BASE_URL` (usually Portal service DNS, for example `http://efp-portal-service.default.svc.cluster.local`)

## 5) Portal-managed runtime env contract (`efp-agents-secret`)

Portal injects runtime container env from `efp-agents-secret`:

- `EFP_CONFIG_KEY`

Portal also injects plain env:

- `PORTAL_INTERNAL_BASE_URL` (only when configured; omitted if empty)

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

Portal currently proxies runtime session list/delete/chatlog. Runtime summary, revert, and unrevert controls should wait for stable runtime endpoint names and methods before UI work.

## 8) GitHub review stale terminal semantics

For GitHub review tasks superseded by newer PR `head_sha`:

- Portal marks superseded active tasks as `stale`.
- `stale` is a superseded terminal state (not `failed`).
- If runtime results arrive late for an already-stale task, Portal keeps `stale` and does not overwrite it back to `done`/`failed`.
