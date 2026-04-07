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
- `external_system` - optional string (used for adapter action aliases)
- `action` - optional string (used for adapter action aliases)

If parsing fails or data is missing, Portal falls back to deterministic local seed mappings.

## 3) Portal container env contract

Portal container should be configured with:

- `BOOTSTRAP_ADMIN_PASSWORD`
- `PORTAL_INTERNAL_API_KEY`
- `RUNTIME_INTERNAL_API_KEY`
- `PORTAL_INTERNAL_BASE_URL` (usually Portal service DNS, for example `http://efp-portal-service.default.svc.cluster.local`)

Portal->Runtime control-plane headers are sourced from these env values.

## 4) Portal-managed runtime env contract (`efp-agents-secret`)

Portal injects runtime container env from `efp-agents-secret`:

- `EFP_CONFIG_KEY`
- `PORTAL_INTERNAL_API_KEY`
- `RUNTIME_INTERNAL_API_KEY`

Portal also injects plain env:

- `PORTAL_INTERNAL_BASE_URL` (only when configured; omitted if empty)

## 5) Kubernetes git credential key wiring

Portal runtime creation reads git credentials from `efp-agents-secret`.

Configurable selector keys:

- `K8S_GIT_USERNAME_KEY` (default `GIT_USERNAME`)
- `K8S_GIT_TOKEN_KEY` (default `GIT_TOKEN`)

If both credentials are present, clone URL is rewritten to authenticated HTTPS.
If credentials are absent, clone proceeds with unauthenticated URL.
