# Portal Runtime / Control-Plane Contract

## 1) Portal role
- Portal is the control plane: UI, proxy, agent/resource registry, and policy/runtime-profile coordination.
- Portal does **not** execute tools/skills itself, and does not own runtime-internal recovery algorithms.
- Runtime is exposed only through the EFP-compatible API surface on service port `:8000`.

## 2) Routing contract
- User-facing proxy route: `/a/{agent_id}/api/*`.
- Portal targets runtime service port `:8000`.
- OpenCode internal `:4096` is pod-internal only; Portal does not expose `/opencode/*` user routes.
- For chat UX, Portal should prefer `POST /a/{agent_id}/api/chat/stream` (SSE) over waiting for long blocking JSON responses.

## 3) Runtime types
- Allowed runtime types: `native`, `opencode`.
- Invalid non-empty `runtime_type` must be rejected at schema/API/helper levels (no silent fallback to `native`).
- Legacy rows/fixtures with missing or blank runtime_type may default to `native` for compatibility.

## 4) Assets contract
- Skills directory: `/app/skills`.
- Portal provisions the full selected skill package tree into `/app/skills`; it does not parse skills and does not copy only `SKILL.md`.
- Root-layout skills repositories should expose packages as `<skill-name>/SKILL.md`, with ordinary package files such as `<skill-name>/scripts/...`, `<skill-name>/templates/...`, `<skill-name>/reference/...`, and `<skill-name>/examples/...` preserved.
- Nested layouts are selected with `DEFAULT_SKILL_REPO_SUBDIR`, for example `DEFAULT_SKILL_REPO_SUBDIR=skills` copies `repo/skills/.` directly into `/app/skills`.
- `DEFAULT_SKILL_ASSET_VERSION` is a rollout marker only. Changing it updates Deployment template annotations so pods restart and the skills initContainer reclones same-branch content.
- OpenCode runtime syncs `/app/skills` into `/workspace/.opencode/skills`.
- Workspace defaults:
  - native: `/root/.efp`
  - opencode: `/workspace`
- Native source overlay is **default false**.
  - Only when `ENABLE_RUNTIME_SOURCE_OVERLAY=true` and `DEFAULT_AGENT_RUNTIME_REPO_URL` is valid should runtime source be cloned to `/app/src` (plus `/app/.git`).
- opencode does not mount `/app/src`.

## 5) State contract
- opencode state dirs:
  - `/root/.local/share/opencode`
  - `/root/.local/share/efp-compat`
- Portal K8s provisioning owns mount/env wiring; runtime owns actual recovery behavior.

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
- Container startup runs migrations.
- Do not use `Base.metadata.create_all` as a startup shortcut.
- `runtime_type` DB `server_default` is only a backfill migration concern, not a head schema default contract.

## 8) Smoke / CI
- `integration/scripts/smoke_portal.sh` validates Portal-side contracts only.
- Live runtime contract validation belongs to runtime repo(s) or multi-repo integration.


## 9) OpenCode permission env contract
Portal responsibility:
- Pass `EFP_OPENCODE_PERMISSION_MODE` and `EFP_OPENCODE_ALLOW_BASH_ALL` to opencode runtime containers.
- Do not execute tools or enforce OpenCode tool permissions in Portal.

Runtime responsibility:
- Generate OpenCode config permission map from those env vars.
- Never return success with an empty visible assistant response.
- Return non-success completion states such as `blocked`, `incomplete`, `empty_final`, or `error` when no final visible text is available.

Portal responsibility:
- Render runtime events (reasoning/tool/permission/continuation/progress) as runtime-origin telemetry in Thinking Process.
- Render `completion_state` + related diagnostics explicitly in chat UI for non-success outcomes (`blocked`, `incomplete`, `error`, `empty_final`), instead of presenting them as normal success responses.
- Portal remains control-plane/proxy only; it must not execute tools and must not implement runtime-internal recovery behavior.

## 10) Runtime v2 profile/config contract
- Portal runtime profiles preserve the Runtime v2 `RuntimeConfig` field names it receives in profile JSON for safe runtime-owned behavior:
  - tool selection and permissions: `enabled_tools`, `disabled_tools`, `tool_permissions`
  - skills and commands: `active_skills`, `skill_directories`, `command_directories`, `enable_command_expansion`
  - loop and context controls: `max_iterations`, `doom_loop_threshold`, `max_context_parts`, `max_context_chars`, `max_context_tokens`, `context_reserve_chars`, `context_reserve_tokens`
  - compaction controls: `compaction_auto`, `compaction_prune`, `compaction_tail_turns`, `compaction_preserve_recent_chars`, `compaction_preserve_recent_tokens`, `compaction_reserved_chars`, `compaction_tool_output_max_chars`, `compaction_prune_min_chars`, `compaction_prune_protect_chars`, `enable_compaction_summarizer`, `enable_context_overflow_retry`, `enable_session_revert_snapshots`
  - prompts and instructions: `include_default_system_prompt`, `include_environment_context`, `include_runtime_reminders`, `system_prompt_texts`, `system_prompt_paths`, `max_system_prompt_chars`, `include_default_instructions`, `attach_read_instructions`, `instruction_texts`, `instruction_paths`, `max_instruction_chars`
  - skills, commands, and prompt references: `include_skill_sidecar_content`, `max_skill_sidecar_chars`, `max_command_chars`, `resolve_prompt_references`, `max_prompt_reference_chars`, `max_prompt_directory_entries`
  - tool output and mode controls: `tool_output_max_lines`, `tool_output_max_bytes`, `tool_output_truncation_direction`, `archive_truncated_tool_outputs`, `tool_output_dir`, `runtime_mode`, `enable_plan_tool`, `plan_mode_read_only`, `enable_question_tool`, `enable_lsp_tool`, `model_aware_tool_selection`, `inject_background_task_results`, `emit_llm_stream_events`, `track_usage`, `structured_output_schema`
- Portal preserves those fields through persisted profile JSON, runtime-profile apply payloads, and trusted chat metadata, and exposes advanced Runtime v2 controls for the preserved field surface in Agent Settings and Runtime Profile management.
- Portal keeps the existing Copilot projection from `llm.provider=github_copilot` to the runtime provider name expected by the selected runtime type.
- Legacy `llm.tools` may remain in stored profiles for compatibility, but Portal must not force `llm.tools=["*"]` when explicit Runtime v2 tool selection exists through `enabled_tools`, `disabled_tools`, or `tool_permissions`.
- Runtime profile apply payloads and trusted chat metadata carry the sanitized Runtime v2 config surface under `config` / `runtime_profile.config`.
- Browser-provided chat `metadata` is untrusted. Portal replaces it with server-owned runtime profile/config/authorization metadata.

## 11) Runtime v2 tool/catalog contract
- Runtime v2 core tool ids are runtime-owned and should appear in `/api/capabilities` snapshots as `capability_type: "tool"` entries: `apply_patch`, `bash`, `edit`, `glob`, `grep`, `invalid`, `read`, `skill`, `task`, `todowrite`, `webfetch`, `write`.
- Removed legacy aliases such as `read_file`, `write_file`, `list_dir`, `shell_exec`, `shell_status`, `shell_kill`, `todo_write`, `task_status`, `task_cancel`, `skill_list`, and old `fetch` are runtime-owned compatibility decisions, not Portal controls.
- Runtime-owned built-in tools and adapter actions are runtime implementation details, not Portal asset provisioning.
- PR creation / adapter action availability is determined by runtime capability snapshot, built-in runtime tool surface, runtime profile, and permission policy.
- Portal may keep control-plane fallback aliases to avoid mapping gaps, but aliases must not imply a tools repo, tools index, any Portal-managed tools directory/env or external-tools manifest.

## 12) Runtime v2 session API contract
- Portal currently proxies runtime session list/delete/chatlog endpoints:
  - `GET /a/{agent_id}/api/sessions`
  - `DELETE /a/{agent_id}/api/sessions/{session_id}`
  - `GET /a/{agent_id}/api/sessions/{session_id}/chatlog`
- Runtime v2 summary, revert, and unrevert UI work needs stable runtime endpoint names and methods before Portal should add dedicated controls.

## 13) OpenCode on-demand repository checkout contract
- Portal does **not** parse slash commands and does **not** clone user-requested business repositories during pod startup.
- Runtime adapters own slash command parsing and on-demand checkout flows (for example `/create-pull-request in git repo <url> from branch <head> to <base>`).
- OpenCode checkout workspace contract:
  - workspace root: `/workspace`
  - runtime checkout root: `/workspace/repos`
  - repository path: `/workspace/repos/<owner>/<repo>`
- Portal provisions skills assets with initContainers:
  - skills clone target: `/app/skills`
  - root layout: `<skill-name>/SKILL.md`
  - nested layout: `DEFAULT_SKILL_REPO_SUBDIR=skills`
- `GIT_TOKEN` remains initContainer-only for asset clone and is not injected into the main runtime container by default.
- Private business-repo checkout must be authorized by runtime-side provider/runtime-profile credentials (for example GitHub provider token), not by broad Portal/K8s git token injection into runtime.
