# Documentation Review — September 17, 2026

## Baseline and scope

The clean `engineering-flow-platform-portal-latest` checkout was fast-forwarded from `7b77eae` to GitHub `origin/master` at `021baaafdc3f2406540b467596c6ba961ad821d5`. A later `git ls-remote origin refs/heads/master` check returned the same revision. Documentation work is on `codex/english-docs-beginner-guide`. The separate original Portal checkout's uncommitted application changes were left intact.

All eight Markdown documents tracked at that baseline were reviewed. `requirements.txt` was also checked for prose requiring translation; it contains dependency declarations. No application implementation, database migration, deployment manifest, or test implementation was changed by this documentation update.

## Existing documents reviewed

| Document | Corrections and checks |
| --- | --- |
| [Repository README](../README.md) | Added guide entrypoints; corrected sign-in and registration, supported providers, skills discovery, runtime prerequisites, session/event routing, deployment references, profile behavior, and configuration guidance |
| [Kubernetes guide](../k8s/README.md) | Corrected manifest order, ServiceAccount/RBAC, namespaces, Secret wiring, authentication placeholders, two deployment alternatives, engine selection, workspace defaults, storage, and upgrades |
| [Kubernetes troubleshooting](K8S_TROUBLESHOOTING.md) | Translated all Chinese prose and code comments to English; corrected reachable Kubernetes node IP, process environment overrides, readiness, endpoint, and no-op provisioning diagnostics |
| [Integration README](../integration/README.md) | Matched the smoke script's selected tests; documented equivalent shell instructions and the limits of contract checks |
| [Portal/runtime contract](PORTAL_RUNTIME_CONTRACT.md) | Corrected current session UI capabilities, profile/provider fields, configuration boundaries, encryption scope, and API descriptions while preserving tested contract assertions |
| [Phase 5 notes](PHASE5_PRODUCTIZATION.md) | Corrected upgrade, capability catalog, session, and UI descriptions against current implementation |
| [Connector contract](CONNECTORS_CONTRACT.md) | Corrected the member/browser-tab relay, permissions, metadata/version behavior, setup flow, and package delivery assumptions |
| [Browser download README](../app/static/downloads/README.md) | Clarified that bridge archives must be supplied by the operator or an explicit download URL; the current Portal image workflow does not fetch them automatically |

The review compared these documents with current routes, schemas, services, frontend controls, Kubernetes manifests, Dockerfile, CI, and existing tests. Outdated documentation was corrected to match the code; this review did not attempt to redesign product behavior.

## New tutorial coverage

The [Beginner's Guide](BEGINNER_GUIDE.md) covers sign-in, navigation, simple/advanced assistant creation, all connection sections, profile copies/defaults, chat, interactive questions/approvals, skills, inference settings, sessions, context/compaction, attachments and workspace files, lifecycle/sharing/system instructions, background tasks, event/timer delegations, browser connectors, administration, help, shortcuts, troubleshooting, and a practice workflow.

The [Operations Guide](OPERATIONS_GUIDE.md) covers Windows and POSIX setup, migrations, local verification, Docker persistence, real-runtime prerequisites, Kubernetes configuration, sign-in and allowlists, engines/profiles/assets/resources, connector packages, workers/logs, backup/upgrade/recovery, API groups, and development/testing.

Nineteen actual screenshots show the current local UI. Their [provenance and recreation instructions](screenshots/README.md) identify the demonstration data and the absence of real runtime credentials. Both guides were rendered to HTML for a local browser check: all embedded images loaded, and neither guide overflowed the 1280-pixel preview viewport. All nineteen screenshots were visually inspected.

Notable details confirmed in the second review:

- Default Connections can include shared service-account credentials. New profiles receive independent, member-visible/editable copies; later default edits do not update existing profiles.
- Saving a member's connection profile can restart its bound running assistants.
- Tasks and delegations are visible across members in this revision; management remains owner-restricted. Assistant file access requires the owner or an administrator.
- Follow-up and rerun reuse the Portal task record and replace its prior result; they are not an automatic archive of every attempt.
- The example file named `efp-efs-pvc.yaml` uses node-local `hostPath`, not EFS.
- `K8S_NODE_IP` and `EFP_CONFIG_KEY` are read from the process environment; placing them only in the application's `.env` file is insufficient.
- Disabled or uninitialized Kubernetes provisioning can produce a simulated Running state without a real assistant runtime.

## Validation performed

- Migrated a separate, empty local SQLite database through `20260913_0035`, then started the current Portal application and checked `/health`.
- Exercised local sign-in, the seeded types, assistant creation, profile/admin/help panels, task/delegation forms, connector setup, and the Timer schedule preview while capturing screenshots.
- Scanned repository Markdown for Chinese characters; none remain. Checked fenced blocks, local links, heading anchors, image targets, PNG dimensions, and whitespace.
- Confirmed only documentation text and screenshot assets are part of the change.
- **169 distinct existing tests passed across selected runs.** One documentation wording assertion initially failed after editing; the valid contract wording was restored and the unchanged test passed on rerun. There are no remaining observed failures in the selected tests. This was not a full-suite or a single consolidated 169-test run.

The test groups can be reproduced from the repository root in PowerShell with the installed requirements and pytest:

```powershell
$env:PYTHONPATH = '.'
$env:K8S_ENABLED = 'false'
.\.venv\Scripts\python.exe -m pytest -q --disable-warnings `
  tests/test_portal_runtime_contract_docs.py `
  tests/test_config.py `
  tests/test_schema_guard.py `
  tests/test_enabled_runtime_types.py `
  tests/test_help_center.py `
  tests/test_auth_api.py `
  tests/test_connectors.py `
  tests/test_role_expansion_setup.py

.\.venv\Scripts\python.exe -m pytest -q --disable-warnings tests/test_proxy_service.py

.\.venv\Scripts\python.exe -m pytest -q --disable-warnings tests/test_k8s_service.py `
  -k 'workspace or readiness or runtime_status or opencode or disabled'
```

The selected Kubernetes expression runs 17 tests and deselects 41. Validation used Python 3.13.7 with the repository's pinned requirements in a local virtual environment; CI targets Python 3.11. Existing deprecation warnings were not converted into implementation changes.

## Limits of the evidence

The screenshots and local checks do not verify production SSO/Copilot authorization, live model replies, external Jira/GitHub/other integrations, Kubernetes deployment, real runtime files/sessions/context, successful background execution, or an installed browser bridge. Those need a configured environment and suitable test accounts. The guides explain their workflows and success checks, but do not represent instructions as completed live-service tests. Full CI, a Docker build, and production deployment were not run for this documentation-only change.
