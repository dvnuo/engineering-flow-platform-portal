# Portal Runtime / Control-Plane Contract

## 1) Portal role
- Portal is the control plane: UI, proxy, agent/resource registry, and policy/runtime-profile coordination.
- Portal does **not** execute tools/skills itself, and does not own runtime-internal recovery algorithms.
- Runtime is exposed only through the EFP-compatible API surface on service port `:8000`.

## 2) Routing contract
- User-facing proxy route: `/a/{agent_id}/api/*`.
- Portal targets runtime service port `:8000`.
- OpenCode internal `:4096` is pod-internal only; Portal does not expose `/opencode/*` user routes.

## 3) Runtime types
- Allowed runtime types: `native`, `opencode`.
- Invalid non-empty `runtime_type` must be rejected at schema/API/helper levels (no silent fallback to `native`).
- Legacy rows/fixtures with missing or blank runtime_type may default to `native` for compatibility.

## 4) Assets contract
- Skills directory: `/app/skills`.
- Tools directory: `/app/tools`.
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
