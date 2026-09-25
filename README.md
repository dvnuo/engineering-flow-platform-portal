# Engineering Flow Platform Portal

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115.0-green.svg)](https://fastapi.tiangolo.com/)

Portal is the web interface and control plane for Engineering Flow Platform. Members create assistants, chat, manage connections, and delegate work. An EFP Native or OpenCode runtime executes the work.

## Documentation

| Start here | What it covers |
|---|---|
| [Beginner Guide](docs/BEGINNER_GUIDE.md) | Complete English walkthrough with screenshots: sign-in, assistants, chat, files, connections, connectors, tasks, delegations, administration, and troubleshooting. |
| [Operations Guide](docs/OPERATIONS_GUIDE.md) | Installation, configuration, upgrades, backups, runtime provisioning, and operational checks. |
| [Kubernetes deployment](k8s/README.md) | The manifests supplied with this repository. |
| [Portal / Runtime Contract](docs/PORTAL_RUNTIME_CONTRACT.md) | Runtime boundaries, configuration, routing, sessions, and assets. |
| [Connectors Contract](docs/CONNECTORS_CONTRACT.md) | Portal, runtime, and local browser bridge protocol. |
| [Phase 5 Productization](docs/PHASE5_PRODUCTIZATION.md) | Capability snapshots, session metadata, and task supersession. |
| [Integration smoke checks](integration/README.md) | Portal contract checks and their limits. |

See the [full documentation index](docs/README.md) for troubleshooting, screenshot provenance, and the documentation review record. The application also includes **Help**, setup guidance in connection panels, and a first-run tour.

---

## Features

- **Agent Management** - Create, start, stop, delete, share agents
- **Web Chat UI** - Chat with EFP agents via reverse proxy
- **Connections** - Reusable runtime profiles for GitHub Copilot or AI Platform, Jira, Confluence, GitHub, AWS, Jenkins, Nexus, Splunk, PostgreSQL, BrowserStack, proxy, and Git identity
- **File Management** - Upload files, preview attachments
- **Session History** - View past conversations
- **Usage Tracking** - Monitor agent usage and costs
- **Skills** - Discover available workflows by typing `/` in chat or choosing a task/delegation skill
- **Tasks and Delegations** - Run tasks and configure work triggered by schedules or supported external sources
- **Local Browser Connector** - Let a compatible runtime use a managed Chrome window on the member's computer
- **Administration** - Manage member access, assistant types, and default connections
- **Diagrams** - A `` ```mermaid `` fence in an assistant reply renders inline with a Diagram | Code switch; Copy hands back the source for a README, a pull request, or Confluence Gliffy's Mermaid import

---

## Quick Start

### Prerequisites

- Python 3.11+
- Git, to obtain the source
- SQLite support included in the standard Python distribution

This starts the Portal UI and database. `K8S_ENABLED=false` does not start a local assistant runtime. Working chat, runtime files, and tools require a separately reachable runtime; production assistant provisioning uses Kubernetes. See the [Operations Guide](docs/OPERATIONS_GUIDE.md).

### Setup

```bash
# Run in the repository root. On Windows, see the PowerShell commands below.
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt

# Use your own values and keep them out of source control.
export SECRET_KEY="$(python -c 'import secrets; print(secrets.token_urlsafe(48))')"
read -r -s -p "Initial administrator password: " BOOTSTRAP_ADMIN_PASSWORD
export BOOTSTRAP_ADMIN_PASSWORD

# Apply schema migrations (required for both first-time setup and upgrades)
alembic upgrade head

# Start server
uvicorn app.main:app --reload
```

On Windows PowerShell, use:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
$env:SECRET_KEY = (& .\.venv\Scripts\python.exe -c "import secrets; print(secrets.token_urlsafe(48))")
$bootstrapCredential = Get-Credential -UserName admin -Message "Choose the initial Portal administrator password"
$env:BOOTSTRAP_ADMIN_PASSWORD = $bootstrapCredential.GetNetworkCredential().Password
.\.venv\Scripts\alembic.exe upgrade head
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload
```

Environment variables above last only for the current shell. For subsequent starts, persist a stable `SECRET_KEY` and the required settings in a private `.env` file or your deployment's secret store. Protect the `.env` file and never commit it.

For local Python development, migrations are still a manual prerequisite (`alembic upgrade head`) before starting `uvicorn`.

Open `http://localhost:8000/admlogin` to sign in as the bootstrap administrator. Members use `/login`, which offers enabled external sign-in methods. The password form appears there only when both SSO and Copilot login are disabled.

**Admin account** (first startup - requires env vars):
- Username: Set `BOOTSTRAP_ADMIN_USERNAME=admin` (defaults to `admin`)
- Password: Set `BOOTSTRAP_ADMIN_PASSWORD` to a strong initial password

The configured bootstrap administrator is created when missing and automatically added to the allowlist. Seed additional usernames with `PORTAL_USER_ALLOWLIST=alice,bob`, or manage access, roles, and usage in **Administration → User Management**. Members are created on their first SSO or GitHub Copilot sign-in; self-service password registration has been removed. Every authenticated request requires an active allowlist entry, so removing a member's access blocks subsequent requests from existing sessions. The roles are `user` and `admin`; administrators can manage every assistant. Migration `20260825_0033` converts the retired `viewer` role to `user`.

## Configuration

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `DATABASE_URL` | SQLite database path | `sqlite:///./portal.db` |
| `SECRET_KEY` | Session secret key | `change-me-in-production` |
| `EFP_CONFIG_KEY` | Optional field-encryption key for credentials in profile Kubernetes Secrets; runtime agents need the matching key. This does not encrypt the Portal database | (empty) |
| `BOOTSTRAP_ADMIN_USERNAME` | Admin username | `admin` |
| `BOOTSTRAP_ADMIN_PASSWORD` | Admin password | (empty - must be set) |
| `PORTAL_USER_ALLOWLIST` | Comma, semicolon, or newline-separated usernames seeded into the access allowlist on startup (`REGISTRATION_ALLOWLIST` is accepted as a legacy alias) | (empty) |
| `PORTAL_SUPPORT_CONTACT` | Shown on the "not on the allowlist" page so a blocked user knows who can grant access (a name, team channel, or `mailto:`/`https:` link) | (empty) |
| `BASE_URI` | Public origin of the portal (no trailing slash); required with SSO so the callback `BASE_URI/auth` matches the redirect URI registered at the IdP | (empty) |
| `SSO_ISSUER_URL` | OpenID Connect issuer / Keycloak realm base URL, e.g. `https://sso.example.com/realms/persons`. Empty disables SSO; `/login` shows the password form only if Copilot login is also disabled. `/admlogin` always offers the password form | (empty) |
| `SSO_INTERNAL_ISSUER_URL` | Issuer base the portal calls server-side for the token exchange, when it reaches the IdP through a different host than the browser (e.g. `http://keycloak.sso.svc.cluster.local:8080/realms/persons`); empty uses `SSO_ISSUER_URL` | (empty) |
| `SSO_CLIENT_ID` | OIDC client id | `webapp` |
| `SSO_CLIENT_SECRET` | OIDC client secret, only for confidential clients | (empty) |
| `SSO_SCOPE` | Scope requested on the authorize redirect | `read write` |
| `SSO_VERIFY_TLS` | Verify the IdP's TLS certificate during the token exchange. Install your internal CA in the trust store when needed; disabling verification is suitable only for isolated diagnosis | `true` |
| `GITHUB_USERNAME_SUFFIX` | Enterprise suffix on GitHub logins (`_emucompany` in `12345678_emucompany`); it is stripped so the portal username is the employee id, matching the allowlist and SSO. Empty keeps the GitHub login | (empty) |
| `GITHUB_PROXY_URL` | How the portal reaches github.com for the Copilot device flow and user lookup: empty honours `HTTP(S)_PROXY`/`NO_PROXY` from the pod environment, `direct` ignores them, or an explicit proxy URL such as `http://proxy.corp:3128` | (empty) |
| `GITHUB_HTTP_TIMEOUT_SECONDS` | Timeout for those github.com calls | `30` |
| `SSO_PROXY_URL` | Same semantics for the server-side SSO token exchange; set `direct` when `HTTPS_PROXY` is present in the pod but the IdP must be reached without it | (empty) |
| `COPILOT_LOGIN_ENABLED` | Offer "Sign in with GitHub Copilot" on the login page (GitHub device flow; a first sign-in creates the member and stores the Copilot token on their default runtime profile) | `true` |
| `GITHUB_ENTERPRISE_SSO_URL` | Enterprise SSO page members must sign in to (new tab) before the Copilot authorization starts, e.g. `https://github.com/enterprises/<slug>/sso`; shown as step 1 on the login page and in every "Authorize GitHub Copilot" card (runtime profile, agent settings, default connections); empty skips that step | (empty) |
| `PORTAL_INTERNAL_BASE_URL` | Required when Runtime must call back into Portal internal APIs (`adapter:portal:*` / internal callbacks); not a universal startup requirement | (empty) |
| `RUNTIME_CAPABILITY_CATALOG_SNAPSHOT_JSON` | Optional runtime capability snapshot JSON for Portal validation/alignment; invalid/empty falls back to deterministic local seed mappings | (empty) |
| `AI_PLATFORM_CHAT_HOST` | Centrally managed AI Platform chat service host | (empty) |
| `AI_PLATFORM_CHAT_URI` | Centrally managed AI Platform chat-completions path | `/v1/api/v1/chat/completions` |
| `AI_PLATFORM_RESPONSES_URI` | Optional AI Platform Responses API path on the chat host, e.g. `/v1/{usercase}/responses`; `{usercase}` is filled from the profile credential. Empty keeps the native runtime on chat/completions (OpenCode always uses chat/completions) | (empty) |
| `AI_PLATFORM_IB2B_HOST` | Centrally managed iB2B token service host | (empty) |
| `AI_PLATFORM_IB2B_URI` | Centrally managed iB2B token exchange path | (empty) |
| `AI_PLATFORM_TRUST_TOKEN_HEADER` | Header used to send the exchanged trust token | `X-XXXX-E2E-Trust-Token` |
| `AI_PLATFORM_TRACKING_PREFIX` | Prefix for AI Platform correlation/session IDs | `EFP` |
| `K8S_ENABLED` | Enable Kubernetes integration | `false` |
| `K8S_INCLUSTER` | Use in-cluster config | `true` |
| `K8S_KUBECONFIG` | Path to kubeconfig | `/etc/rancher/k3s/k3s.yaml` |
| `K8S_AGENT_SERVICE_TYPE` | Agent service type (ClusterIP/NodePort) | `ClusterIP` |
| `K8S_GIT_TOKEN_KEY` | Secret key name for git token in `efp-agents-secret` | `GIT_TOKEN` |
| `GIT_REPO_AUTH_USERNAME` | Username for Portal git branch lookups via `GIT_ASKPASS` | `x-access-token` |
| `GIT_REPO_AUTH_PAT` | PAT/token for Portal git branch lookups; `GIT_PAT` and `GIT_TOKEN` are accepted aliases | (empty) |
| `GIT_REPO_LS_REMOTE_TIMEOUT_SECONDS` | Timeout for Portal git branch lookup requests | `12` |
| `K8S_NODE_IP` | Node IP for NodePort proxy (auto-detected if not set) | (auto-detect) |
| `AGENTS_NAMESPACE` | Agents namespace | `efp-agents` |
| `K8S_STORAGE_CLASS` | Storage class for PVC | `local-path` |
| `K8S_PVC_ACCESS_MODES` | PVC access modes | `["ReadWriteOnce"]` |
| `DEFAULT_AGENT_IMAGE_REPO` | Default native agent image repository | `ghcr.io/dvnuo/engineering-flow-platform` |
| `DEFAULT_AGENT_IMAGE_TAG` | Default agent image tag | `latest` |
| `DEFAULT_AGENT_CPU` | CPU request for a new agent pod (empty leaves the request unset) | `250m` |
| `DEFAULT_AGENT_MEMORY` | Memory request for a new agent pod (empty leaves the request unset) | `512Mi` |
| `DEFAULT_AGENT_CPU_LIMIT` | CPU limit for agent pods (empty disables the limit) | `1` |
| `DEFAULT_AGENT_MEMORY_LIMIT` | Memory limit for agent pods (empty disables the limit) | `2Gi` |
| `EFP_MAX_UPLOAD_MB` | Per-file size cap for chatbox attachments and workspace uploads; passed to every agent pod so the runtime enforces the same cap (the ingress `proxy-body-size` must allow it too) | `25` |
| `EFP_CHAT_UPLOAD_EXTENSIONS` | Comma-separated file extensions the chatbox may attach (case-insensitive, dots optional); drives the composer's file picker, is enforced by the upload proxy, and is passed to every agent pod. The default is non-visual because the default model has no vision: add `jpg,jpeg,png,webp,gif` for a model that can see and they reach it as images. `pdf`, `docx`, `xlsx`, `pptx`, `csv` go through their parsers, a `zip` is projected as its listing plus the text files inside, and any text format (`txt`, `md`, `log`, `json`, `yaml`, `xml`, source files, ...; UTF-8 or GB18030/GBK) is projected as text; a listed binary format the runtime cannot parse is still rejected at upload. Running assistants pick up a change after a restart | `pdf,docx,xlsx,csv,txt,log,pptx,zip,md,yaml,yml,json,xml` |
| `DEFAULT_RUNTIME_TYPE` | Default runtime marker for new agents when `runtime_type` is omitted; supported values are `native` and `opencode` | `native` |
| `ENABLED_RUNTIME_TYPES` | Comma-separated runtime markers offered for new agents (the Engine step of the create wizard) and for assistant types: `native`, `opencode`, or both. Unknown markers are ignored and an empty result offers `native`. Existing agents keep their runtime; creating or switching to a marker that is not listed is rejected | `native` |
| `DEFAULT_OPENCODE_RUNTIME_IMAGE_REPO` | Default OpenCode runtime image repository | `ghcr.io/dvnuo/efp-opencode-runtime` |
| `DEFAULT_OPENCODE_RUNTIME_IMAGE_TAG` | Default OpenCode runtime image tag | `1.14.39` |
| `DEFAULT_AGENT_SETTINGS_REPO_URL` | Default behavior repository, containing `AGENTS.md` and `instructions/` | `https://github.com/dvnuo/engineering-flow-platform-agents` |
| `DEFAULT_AGENT_SETTINGS_BRANCH` | Behavior repository branch | `master` |
| `DEFAULT_AGENT_SETTINGS_REPO_SUBDIR` | Directory containing the behavior package; empty uses the repository root | (empty) |
| `DEFAULT_AGENT_SETTINGS_ASSET_VERSION` | Behavior package rollout marker; changing it forces a reclone on rollout | (empty) |
| `DEFAULT_SKILL_REPO_URL` | Default skills repository | `https://github.com/dvnuo/engineering-flow-platform-skills` |
| `DEFAULT_SKILL_BRANCH` | Skills repository branch | `master` |
| `DEFAULT_SKILL_REPO_SUBDIR` | Optional subdirectory within the skills repo to provision into `/app/skills`, for example `skills` or `packages/skills` | (empty) |
| `DEFAULT_SKILL_ASSET_VERSION` | Optional rollout marker for skill assets; change it to recreate pods and reclone when tracking the same git branch | (empty) |
| `CONNECTORS_ENABLED` | Show the Connectors menu and the `/api/connectors` routes (per-member connectors such as the local browser bridge; see `docs/CONNECTORS_CONTRACT.md`) | `true` |
| `LOCAL_BROWSER_CLI_DOWNLOAD_URL` | Download link template for the EFP browser bridge packages shown in Connectors → Local browser; `{platform}` expands to `windows-amd64`, `windows-arm64`, `darwin-arm64`, `darwin-amd64`, `linux-amd64`, or `linux-arm64` (a URL without it hands one package to every system); empty serves `app/static/downloads/efp-browser-bridge-{platform}.zip`, the zips built by `scripts/browser-bridge/package.sh` in the tools repository | (empty) |
| `LOCAL_BROWSER_CLI_VERSION` | Version label shown next to that download | (empty) |
| `LOCAL_BROWSER_START_URL` | First tab of the EFP browser window whenever the bridge opens or reopens it: an absolute http(s) URL, or a path such as `/app` resolved against this Portal's origin; empty opens the Portal origin | (empty) |
| `IDLE_AGENT_STOP_WORKER_ENABLED` | Automatically stop assistants with no recent user traffic | `true` |
| `AGENT_IDLE_STOP_AFTER_SECONDS` | Idle time before an assistant is stopped | `259200` (3 days) |

This table highlights commonly used settings. [app/config.py](app/config.py) is the complete source of defaults; the [Operations Guide](docs/OPERATIONS_GUIDE.md) explains deployment-specific settings, worker intervals, and credentials.

For K8s init clone (GitHub/GitHub Enterprise HTTPS), Portal uses token-only auth: `GIT_TOKEN` is injected via secret key mapping, and `GIT_ASKPASS` responds to username prompts with fixed `x-access-token` (no username setting and no credential-in-URL rewrite). The Kubernetes manifests also expose `efp-portal-secret.GIT_TOKEN` to the Portal main container as `GIT_REPO_AUTH_PAT` so `/api/git-repos/branches` can list private repository branches during agent creation.

Kubernetes runtime provisioning behavior:
- Portal provisions either the Python EFP native runtime or the OpenCode runtime based on `runtime_type`.
- The native runtime image comes from `DEFAULT_AGENT_IMAGE_REPO` / `DEFAULT_AGENT_IMAGE_TAG`; the OpenCode runtime image comes from `DEFAULT_OPENCODE_RUNTIME_IMAGE_REPO` / `DEFAULT_OPENCODE_RUNTIME_IMAGE_TAG`.
- `/api/agents/defaults` exposes `default_runtime_type` and the supported `runtime_types` matrix for agent creation/editing.
- Only markers listed in `ENABLED_RUNTIME_TYPES` are offered for new agents: the matrix flags each entry with `enabled`, `default_runtime_type` is always an enabled marker, and create or switch requests for a marker that is not enabled return 422.
- New agents mount `/workspace` by default for both runtimes.
- Portal has no alternate Python EFP runtime versions, no runtime source overlay, and no runtime/source settings surface.
- Portal mounts `/app/skills` when a skill repo/default exists.
- Portal does not parse slash commands and does not clone user-requested business repos at pod startup.
- Runtime owns on-demand checkout flows such as `/create-pull-request in git repo <url> from branch <head> to <base>`.
- Skills repo is cloned by Portal initContainers into `/app/skills`.
- Portal does not parse skills and does not copy only `SKILL.md`; it provisions the full selected skill package tree.
- Root-layout skill repos should contain entries such as `<skill-name>/SKILL.md`, `<skill-name>/scripts/...`, `<skill-name>/templates/...`, `<skill-name>/reference/...`, or `<skill-name>/examples/...`.
- Nested skill repo layouts can be enabled with `DEFAULT_SKILL_REPO_SUBDIR=skills`, which copies `repo/skills/.` directly into `/app/skills` instead of nesting it as `/app/skills/skills`.
- `DEFAULT_SKILL_ASSET_VERSION` is not used for git checkout. Change it to update the Deployment template annotation and force a pod rollout/reclone when the same branch content changes.
- Portal does not configure external tools repo/branch/mounts; runtime built-in tools are runtime-owned.
- `GIT_TOKEN` is used by git-clone initContainers. Portal's main container receives the Portal secret token as `GIT_REPO_AUTH_PAT` for branch listing only; agent runtime main containers still do not receive the broad clone token.
- Private business-repo checkout authorization should come from runtime profile/provider credentials (for example GitHub provider token), not from broad K8s clone token injection to main runtime.
- Runtime profiles are the Portal-owned control-plane source for Jira, Confluence, GitHub, and git user config. Portal stores and forwards those sections; the Python runtime writes `ATLASSIAN_CONFIG` / Atlassian CLI config, `gh` hosts config, and git user config inside the runtime container.
- Behavior repositories provision `AGENTS.md`, `instructions/`, and optional `portal/` personalization assets into the workspace. See the [assets contract](docs/PORTAL_RUNTIME_CONTRACT.md#4-assets-contract).
- Portal remains the control plane; runtime owns tools, skills execution, loop control, context shaping, compaction, sessions, permissions, and runtime tool availability (built-in tools + runtime profile + permission policy).

Local default is `K8S_ENABLED=false`. Kubernetes manifests set `K8S_ENABLED=true` explicitly. For production Kubernetes, configure storage class/access mode via env or manifests.

### Phase 5 control-plane contract

- Portal remains the only user-facing entry point and forwards Portal identity headers to Runtime.
- Portal runtime requests use the current trusted Portal source/header contract in the in-VPC topology.
- EFP `adapter:portal:*` callbacks require `PORTAL_INTERNAL_BASE_URL`.

### Session Metadata Registry (internal)

- Registry key semantics: **`(agent_id, session_id)`** (agent-scoped), not globally-unique `session_id`.
- Exact upsert/get:
  - `PUT /api/internal/agents/{agent_id}/sessions/{session_id}/metadata`
  - `GET /api/internal/agents/{agent_id}/sessions/{session_id}/metadata`
- List/query:
  - `GET /api/internal/agents/{agent_id}/sessions/metadata`
  - optional filters: `latest_event_state`, `current_task_id`

### GitHub review supersession lifecycle

- For GitHub `pull_request_review_requested`, Portal dedupes exact duplicates by `(owner/repo/pull_number/reviewer/head_sha)`.
- When a newer `head_sha` arrives, Portal creates a new review task and marks older active review tasks as `stale`.
- `stale` is treated as a superseded terminal state; late runtime results do not overwrite a task already marked `stale`.

### Schema upgrade

Portal requires Alembic migrations before startup for both first-time setup on a new database and upgrades of an existing database:

```bash
alembic upgrade head
```

---

## Architecture

### Components

```
┌─────────────┐     ┌─────────────────┐     ┌──────────────┐
│   Portal    │────▶│  Proxy Service  │────▶│ EFP Runtime  │
│  (FastAPI)  │     │  (/a/{id}/*)    │     │  (Port 8000) │
└─────────────┘     └─────────────────┘     └──────────────┘
       │                                              │
       ▼                                              ▼
┌─────────────┐                               ┌──────────────┐
│   SQLite    │                               │   Jira/      │
│  (Metadata) │                               │  Confluence  │
└─────────────┘                               └──────────────┘
```

### Project Structure

```
app/
├── main.py           # Application entry point
├── config.py         # Configuration loading
├── deps.py           # FastAPI dependencies
├── web.py            # Web routes & API
├── api/              # API endpoints
│   └── auth.py       # Authentication
├── help/             # Help centre topics, one markdown file each (see help/README.md)
├── models/           # SQLAlchemy models
├── repositories/     # Data access layer
├── schemas/          # Pydantic schemas
├── services/         # Business logic
│   ├── auth_service.py
│   ├── k8s_service.py
│   └── proxy_service.py
├── static/           # CSS, JS
│   ├── css/
│   └── js/
│       └── chat_ui.js  # Chat UI
└── templates/        # HTML templates
    ├── app.html
    ├── login.html
    └── partials/
```

---

## Agent Proxy

Portal proxies requests to EFP runtime at `/a/{agent_id}/*`:

- `/a/{agent_id}/api/chat/stream` - Streaming chat (SSE), used by the chat UI
- `/a/{agent_id}/api/chat` - Non-streaming chat API
- `/a/{agent_id}/api/files/*` - File operations
- `/a/{agent_id}/api/events` - WebSocket events

The proxy validates:
- User has active allowlist access; the agent belongs to them, is public, or they are an administrator
- Agent is in `running` state
- Workspace file operations and session mutations require the owner or an administrator

---

## Settings Panel

Assistants bind to a reusable **Connections** profile. In Kubernetes deployments, changing a profile updates its Secret and restarts bound running assistants so their runtime reads the new configuration. Stopped assistants receive it at their next start.

### LLM Configuration
- Provider selection: **GitHub Copilot** or **AI Platform**
- Model, default thinking level, and default context size
- Copilot authorization/API key, or AI Platform username, password, and usercase

Choose from the model catalog offered for the selected provider; standalone OpenAI and Anthropic providers are not offered. AI Platform service endpoints are deployment-managed.

### Integrations
- **Jira**, **Confluence**, and **Jenkins** - Multiple named instances and their applicable credentials
- **Nexus**, **Splunk**, and **PostgreSQL** - Read-only troubleshooting instances (artifact lookups, log searches, schema and query inspection), addressed by name with `--instance`
- **GitHub** - Personal access token
- **AWS** - Configured organizational credentials
- **Mobile / BrowserStack** - REST/Appium credentials and local-testing options
- **Proxy**, **Git identity**, and **Debug** settings

Follow the field-specific **Setup guide** in each panel or the [Beginner Guide](docs/BEGINNER_GUIDE.md). Runtime images must include the corresponding tools.

### File Upload

Files are proxied to EFP runtime:

```
POST /a/{agent_id}/api/files/upload
Content-Type: multipart/form-data
file: <binary>
```

---

## Deployment

### Kubernetes

Use [k8s/README.md](k8s/README.md) and the [Operations Guide](docs/OPERATIONS_GUIDE.md) to configure storage, secrets, service accounts, and ingress before applying the deployment. The main manifest is [k8s/efp-portal-deployment.yaml](k8s/efp-portal-deployment.yaml); the alternative is [k8s/portal-git-clone/efp-portal-deployment.yaml](k8s/portal-git-clone/efp-portal-deployment.yaml). They define the same deployment, so choose one.

These manifests use a git-clone overlay. Mount application code and migration assets from the same cloned revision (`/app/app`, `/app/alembic`, and `/app/alembic.ini`) so Alembic revisions match application code. An image tag alone does not pin application code if the initContainer still follows a moving branch.

### Docker

Container startup runs `alembic upgrade head` automatically before launching Uvicorn. Create a private `.env` containing `SECRET_KEY` and `BOOTSTRAP_ADMIN_PASSWORD` before this example; add any further deployment settings there.

```bash
docker run -d \
  --name portal \
  -p 8000:8000 \
  --env-file .env \
  -e K8S_ENABLED=false \
  -e DATABASE_URL=sqlite:////data/portal.db \
  -e FORWARDED_ALLOW_IPS=127.0.0.1 \
  -v portal-data:/data \
  ghcr.io/dvnuo/engineering-flow-platform-portal:latest
```

The named volume retains the database when the container is replaced. Open `/admlogin` for the initial administrator. For production, choose a tested image version and configure trusted proxy addresses to match your ingress. This example runs Portal only; it does not provision assistant containers.

---

## Development

### Local Development

See Quick Start section for setup. Run with debug logging:

```bash
uvicorn app.main:app --reload --log-level debug
```

Access `http://localhost:8000/app`

### Adding New Features

1. Add model in `app/models/`
2. Add schema in `app/schemas/`
3. Add repository in `app/repositories/`
4. Add service in `app/services/`
5. Add routes in `app/web.py` or `app/api/`

---

## API Endpoints

### Authentication

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/login` | GET | Enabled external sign-in methods; password fallback when both are disabled |
| `/admlogin` | GET | Administrator/password sign-in page |
| `/login/sso` | GET | Start configured company SSO flow |
| `/auth` | GET | SSO callback |
| `/api/auth/copilot/start` | POST | Start Copilot device sign-in |
| `/api/auth/copilot/check` | POST | Check device authorization and establish the session |
| `/api/auth/login` | POST | Password login for an existing account |
| `/api/auth/logout` | POST | Logout |
| `/api/auth/me` | GET | Get current user |

`/register` redirects to `/login`. There is no `/api/auth/register` route.

### Agents

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/agents/mine` | GET | List user's agents |
| `/api/agents/public` | GET | List public agents |
| `/api/agents` | POST | Create agent |
| `/api/agents/{id}` | GET | Get agent details |
| `/api/agents/{id}` | DELETE | Delete agent |
| `/api/agents/{id}/start` | POST | Start agent |
| `/api/agents/{id}/stop` | POST | Stop agent |
| `/api/agents/{id}/restart` | POST | Restart agent |
| `/api/agents/{id}/share` | POST | Share agent |
| `/api/agents/{id}/unshare` | POST | Unshare agent |
| `/api/agents/{id}/status` | GET | Get agent status |
| `/api/agents/{id}/delete-runtime` | POST | Delete the agent and its runtime. Workspace files on the shared volume are kept. |

### Agent Proxy

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/a/{agent_id}/api/chat` | POST | Chat with agent |
| `/a/{agent_id}/api/chat/stream` | POST | Stream chat response using SSE |
| `/a/{agent_id}/api/events` | WebSocket | Runtime progress, permission, question, and connector events |
| `/a/{agent_id}/api/files/upload` | POST | Upload file |
| `/a/{agent_id}/api/files/{id}` | GET | Retrieve an attachment |

This is an entrypoint list, not the full API reference. The running Portal exposes OpenAPI at `/docs` and `/openapi.json`; runtime-owned endpoints are described by the runtime and the [Portal / Runtime Contract](docs/PORTAL_RUNTIME_CONTRACT.md).

---

## Tech Stack

- **Backend**: FastAPI, SQLAlchemy, SQLite
- **Frontend**: HTMX, Alpine.js, Tailwind CSS, markdown-it, highlight.js, Mermaid (all vendored under `app/static/lib/`; `mermaid.min.js` 11.15.0 is fetched on demand when a reply carries a diagram)
- **Deployment**: Kubernetes (EKS), Docker

---

## License

MIT License
