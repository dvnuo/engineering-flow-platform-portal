# Portal Runtime / Control-Plane Contract

## 1) Portal role
- Portal is the control plane: UI, proxy, agent/resource registry, and policy/runtime-profile coordination.
- Portal does **not** execute tools/skills itself, and does not own runtime-internal recovery algorithms.
- Runtime is exposed only through the EFP-compatible API surface on service port `:8000`.

## 2) Routing contract
- User-facing proxy route: `/a/{agent_id}/api/*`.
- Portal targets runtime service port `:8000`.
- For chat UX, Portal should prefer `POST /a/{agent_id}/api/chat/stream` (SSE) over waiting for long blocking JSON responses.

## 3) Runtime selection
- Portal supports two runtime markers: `native` and `opencode`.
- `native` provisions the Python `dvnuo/engineering-flow-platform` runtime image configured by Portal. `opencode` provisions the OpenCode-compatible runtime image configured by Portal.
- Missing, blank, or legacy stored runtime markers normalize to `native` where Portal serializes existing agent state. Invalid non-empty request values are rejected at request boundaries.
- `/api/agents/defaults` returns `default_runtime_type` and a `runtime_types` matrix with each supported runtime marker, label, configured default image, and default mount path.
- Creating an agent without an explicit `runtime_type` uses `DEFAULT_RUNTIME_TYPE` when it is valid. If `DEFAULT_RUNTIME_TYPE` is blank, Portal falls back to `native`; if it is invalid, `/api/agents/defaults` displays `native` as the fallback and create reports a clear server configuration error.
- Switching an existing agent between `native` and `opencode` is allowed. When `runtime_type` changes and the request does not explicitly set `image`, Portal updates the agent image to that runtime's configured default image.
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
- Portal does not support a runtime source overlay. It runs the configured runtime image and does not clone runtime source into `/app/src` or `/app/.git`.
- Portal does not configure external tools repositories, branches, or mounts. Runtime built-in tools are runtime-owned.

## 5) State contract
- Portal K8s provisioning owns image, workspace, skill asset, and env wiring; runtime owns tools, skills execution, loop control, context shaping, compaction, sessions, permissions, and recovery behavior.
- For `native`, Portal sets `EFP_RUNTIME_SESSION_ROOT` under the configured workspace mount (`<workspace>/.efp/runtime`) so runtime sessions, checkpoints, todos, and chat artifacts persist on the agent PVC across pod restarts.
- For `opencode`, Portal keeps using the adapter/OpenCode state mounts (`EFP_ADAPTER_STATE_DIR` and `OPENCODE_DATA_DIR`) for compatibility state and upstream OpenCode data.

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
- Runtime profiles are Portal-owned only for concise integration context: `llm`, `proxy`, `jira`, `confluence`, `github`, `aws`, `jenkins`, `mobile-auto`, `git`, and `debug`.
- Portal stores and forwards LLM provider/model/Copilot API key fields that it owns.
- Portal stores and forwards proxy and external integration sections that it owns. For the Python EFP runtime this includes enough config for runtime-side file generation:
  - `jira.enabled` and `jira.instances[]` with `name`, `url` (accepted from `url` or `base_url`), `username` (accepted from `username` or `email`), `password`, `token` (accepted from `token` or `api_token`), `project` (accepted from `project` or `project_key`), `api_version`, and per-instance `enabled`.
  - `confluence.enabled` and `confluence.instances[]` with `name`, `url` (accepted from `url` or `base_url`), `username` (accepted from `username` or `email`), `password`, `token` (accepted from `token` or `api_token`), `space` (accepted from `space` or `space_key`), and per-instance `enabled`.
  - `github.enabled`, `github.api_token` (accepted from `api_token`, `token`, or `access_token`), and `github.base_url`.
  - `mobile-auto.enabled`, `mobile-auto.defaults`, and `mobile-auto.browserstack` fields for BrowserStack REST/Appium credentials, proxy, BrowserStackLocal mode, and local binary path.
  - `git.user.name` and `git.user.email`.
- The Python runtime consumes the applied profile config and writes its own external tool files: `ATLASSIAN_CONFIG` / `~/.config/atlassian/config.json` for the `engineering-flow-platform-tools` `jira` and `confluence` CLIs, GitHub CLI host config for `gh`, mobile-auto BrowserStack config in `EFP_CONFIG`, and git user config for `git`.
- Portal must not write those runtime files itself and must not execute `jira`, `confluence`, `gh`, `mobile-auto`, `BrowserStackLocal`, or `git` commands.
- Portal drops low-level runtime internals for tools, skills, loop control, context shaping, compaction, prompt assembly, structured output, and runtime mode.
- Runtime profile apply payloads and trusted chat metadata carry the sanitized profile context under `config` / `runtime_profile.config`.
- Browser-provided chat `metadata` is untrusted. Portal replaces it with server-owned runtime profile/config/authorization metadata.

## 11) Runtime tool/catalog contract
- Runtime core tool ids are runtime-owned and should appear in `/api/capabilities` snapshots as `capability_type: "tool"` entries: `apply_patch`, `bash`, `edit`, `glob`, `grep`, `invalid`, `read`, `skill`, `task`, `todowrite`, `webfetch`, `write`.
- Removed legacy aliases such as `read_file`, `write_file`, `list_dir`, `shell_exec`, `shell_status`, `shell_kill`, `todo_write`, `task_status`, `task_cancel`, `skill_list`, and old `fetch` are runtime-owned compatibility decisions, not Portal controls.
- Runtime-owned built-in tools and adapter actions are runtime implementation details, not Portal asset provisioning.
- PR creation / adapter action availability is determined by runtime capability snapshot, built-in runtime tool surface, runtime profile, and permission policy.
- Portal may keep control-plane fallback aliases to avoid mapping gaps, but aliases must not imply a tools repo, tools index, any Portal-managed tools directory/env or external-tools manifest.

## 12) Runtime session API contract
- Portal currently proxies runtime session list/delete/chatlog endpoints:
  - `GET /a/{agent_id}/api/sessions`
  - `DELETE /a/{agent_id}/api/sessions/{session_id}`
  - `GET /a/{agent_id}/api/sessions/{session_id}/chatlog`
- Runtime summary, revert, and unrevert UI work needs stable runtime endpoint names and methods before Portal should add dedicated controls.

## 13) On-demand repository checkout contract
- Portal does **not** parse slash commands and does **not** clone user-requested business repositories during pod startup.
- Runtime adapters own slash command parsing and on-demand checkout flows (for example `/create-pull-request in git repo <url> from branch <head> to <base>`).
- Portal provisions skills assets with initContainers:
  - skills clone target: `/app/skills`
  - root layout: `<skill-name>/SKILL.md`
  - nested layout: `DEFAULT_SKILL_REPO_SUBDIR=skills`
- `GIT_TOKEN` remains initContainer-only for asset clone and is not injected into the main runtime container by default.
- Private business-repo checkout must be authorized by runtime-side provider/runtime-profile credentials (for example GitHub provider token), not by broad Portal/K8s git token injection into runtime.
