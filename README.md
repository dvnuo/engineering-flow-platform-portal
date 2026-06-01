# Engineering Flow Platform Portal

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115.0-green.svg)](https://fastapi.tiangolo.com/)

Portal is the web interface for Engineering Flow Platform. It provides agent management, chat UI, and integration with EFP runtime.

---

## Features

- **Agent Management** - Create, start, stop, delete, share agents
- **Web Chat UI** - Chat with EFP agents via reverse proxy
- **Settings Panel** - Configure LLM, Jira, Confluence, GitHub integrations per agent
- **File Management** - Upload files, preview attachments
- **Session History** - View past conversations
- **Usage Tracking** - Monitor agent usage and costs
- **Skills Panel** - Browse available agent skills

---

## Quick Start

### Prerequisites

- Python 3.11+
- SQLite

### Setup

```bash
# Install dependencies
pip install -r requirements.txt

# Apply schema migrations (required for both first-time setup and upgrades)
alembic upgrade head

# Start server
uvicorn app.main:app --reload
```

For local Python development, migrations are still a manual prerequisite (`alembic upgrade head`) before starting `uvicorn`.

Access `http://localhost:8000/login`

**Admin account** (first startup - requires env vars):
- Username: `admin`
- Password: Set `BOOTSTRAP_ADMIN_PASSWORD=admin123` env var

## Configuration

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `DATABASE_URL` | SQLite database path | `sqlite:///./portal.db` |
| `SECRET_KEY` | Session secret key | `change-me-in-production` |
| `BOOTSTRAP_ADMIN_USERNAME` | Admin username | `admin` |
| `BOOTSTRAP_ADMIN_PASSWORD` | Admin password | (empty - must be set) |
| `PORTAL_INTERNAL_BASE_URL` | Required when Runtime must call back into Portal internal APIs (`adapter:portal:*` / internal callbacks); not a universal startup requirement | (empty) |
| `RUNTIME_CAPABILITY_CATALOG_SNAPSHOT_JSON` | Optional runtime capability snapshot JSON for Portal validation/alignment; invalid/empty falls back to deterministic local seed mappings | (empty) |
| `K8S_ENABLED` | Enable Kubernetes integration | `false` |
| `K8S_INCLUSTER` | Use in-cluster config | `true` |
| `K8S_KUBECONFIG` | Path to kubeconfig | `/etc/rancher/k3s/k3s.yaml` |
| `K8S_AGENT_SERVICE_TYPE` | Agent service type (ClusterIP/NodePort) | `ClusterIP` |
| `K8S_GIT_TOKEN_KEY` | Secret key name for git token in `efp-agents-secret` | `GIT_TOKEN` |
| `K8S_NODE_IP` | Node IP for NodePort proxy (auto-detected if not set) | (auto-detect) |
| `AGENTS_NAMESPACE` | Agents namespace | `efp-agents` |
| `K8S_STORAGE_CLASS` | Storage class for PVC | `local-path` |
| `K8S_PVC_ACCESS_MODES` | PVC access modes | `["ReadWriteOnce"]` |
| `DEFAULT_AGENT_IMAGE_REPO` | Default agent image repository | - |
| `DEFAULT_AGENT_IMAGE_TAG` | Default agent image tag | `latest` |
| `DEFAULT_SKILL_REPO_SUBDIR` | Optional subdirectory within the skills repo to provision into `/app/skills`, for example `skills` or `packages/skills` | (empty) |
| `DEFAULT_SKILL_ASSET_VERSION` | Optional rollout marker for skill assets; change it to recreate pods and reclone when tracking the same git branch | (empty) |

For K8s init clone (GitHub/GitHub Enterprise HTTPS), Portal uses token-only auth: `GIT_TOKEN` is injected via secret key mapping, and `GIT_ASKPASS` responds to username prompts with fixed `x-access-token` (no username setting and no credential-in-URL rewrite).

Kubernetes runtime provisioning behavior:
- Portal provisions the Python EFP runtime image from `DEFAULT_AGENT_IMAGE_REPO` / `DEFAULT_AGENT_IMAGE_TAG`.
- Portal provisions a single Python EFP runtime. It has no runtime selector, no alternate Python runtime versions, no runtime source overlay, and no EFP runtime settings surface.
- New agents mount `/workspace` by default.
- Portal mounts `/app/skills` when a skill repo/default exists.
- Portal does not parse slash commands and does not clone user-requested business repos at pod startup.
- Runtime owns on-demand checkout flows such as `/create-pull-request in git repo <url> from branch <head> to <base>`.
- Skills repo is cloned by Portal initContainers into `/app/skills`.
- Portal does not parse skills and does not copy only `SKILL.md`; it provisions the full selected skill package tree.
- Runtime owns tools, skills execution, loop control, context shaping, compaction, sessions, and permission behavior.
- Root-layout skill repos should contain entries such as `<skill-name>/SKILL.md`, `<skill-name>/scripts/...`, `<skill-name>/templates/...`, `<skill-name>/reference/...`, or `<skill-name>/examples/...`.
- Nested skill repo layouts can be enabled with `DEFAULT_SKILL_REPO_SUBDIR=skills`, which copies `repo/skills/.` directly into `/app/skills` instead of nesting it as `/app/skills/skills`.
- `DEFAULT_SKILL_ASSET_VERSION` is not used for git checkout. Change it to update the Deployment template annotation and force a pod rollout/reclone when the same branch content changes.
- Portal does not configure external tools repo/branch/mounts; runtime built-in tools are runtime-owned.
- `GIT_TOKEN` is used only in git-clone initContainers and is not injected into the main runtime container environment.
- Private business-repo checkout authorization should come from runtime profile/provider credentials (for example GitHub provider token), not from broad K8s clone token injection to main runtime.
- Runtime profiles are the Portal-owned control-plane source for Jira, Confluence, GitHub, and git user config. Portal stores and forwards those sections; the Python runtime writes `ATLASSIAN_CONFIG` / Atlassian CLI config, `gh` hosts config, and git user config inside the runtime container.
- Runtime tool availability is runtime-owned (built-in tool surface + runtime profile + permission policy), not Portal repo/branch/mount driven.

Local default is `K8S_ENABLED=false`. Kubernetes manifests set `K8S_ENABLED=true` explicitly. For production Kubernetes, configure storage class/access mode via env or manifests.

Runtime/control-plane contract: `docs/PORTAL_RUNTIME_CONTRACT.md`.

Phase 5 productization closure notes (upgrade path + capability snapshot contract): `docs/PHASE5_PRODUCTIZATION.md`.

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

- `/a/{agent_id}/api/chat` - Chat API
- `/a/{agent_id}/api/files/*` - File operations
- `/a/{agent_id}/api/events` - WebSocket events

The proxy validates:
- Agent exists and belongs to user (or is shared)
- Agent is in `running` state

---

## Settings Panel

Each agent can configure:

### LLM Configuration
- Provider selection (OpenAI, GitHub Copilot, Anthropic)
- Model selection
- API key

### Integrations
- **Jira** - Multiple instances supported
  - Basic Auth (username + API token)
- **Confluence** - Multiple instances supported
  - Username + API token
- **GitHub** - Personal access token

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

For the git-clone deployment pattern used in this repo, mount runtime code and migration assets from the same cloned revision (`/app/app`, `/app/alembic`, and `/app/alembic.ini`) so Alembic revisions always match application code.

```yaml
# See k8s/portal-deployment-nfs.yaml for full example
apiVersion: apps/v1
kind: Deployment
metadata:
  name: portal
spec:
  replicas: 1
  selector:
    matchLabels:
      app: portal
  template:
    spec:
      containers:
      - name: portal
        image: ghcr.io/dvnuo/engineering-flow-platform-portal:latest
        ports:
        - containerPort: 8000
        env:
        - name: K8S_ENABLED
          value: "true"
        - name: SECRET_KEY
          valueFrom:
            secretKeyRef:
              name: portal-secrets
              key: secret-key
```

### Docker

Container startup runs `alembic upgrade head` automatically before launching Uvicorn.

```bash
docker run -d \
  --name portal \
  -p 8000:8000 \
  -e K8S_ENABLED=false \
  -e SECRET_KEY=your-secret \
  ghcr.io/dvnuo/engineering-flow-platform-portal:latest
```

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
| `/login` | GET | Login page |
| `/register` | GET | Registration page |
| `/api/auth/login` | POST | Login |
| `/api/auth/register` | POST | Register |
| `/api/auth/logout` | POST | Logout |
| `/api/auth/me` | GET | Get current user |

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
| `/api/agents/{id}/destroy` | POST | Destroy agent |

### Agent Proxy

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/a/{agent_id}/api/chat` | POST | Chat with agent |
| `/a/{agent_id}/api/files/upload` | POST | Upload file |
| `/a/{agent_id}/api/files/{id}/preview` | GET | Preview file |

---

## Tech Stack

- **Backend**: FastAPI, SQLAlchemy, SQLite
- **Frontend**: HTMX, Alpine.js, Tailwind CSS
- **Deployment**: Kubernetes (EKS), Docker

---

## License

MIT License
