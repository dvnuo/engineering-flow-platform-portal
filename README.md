# Engineering Flow Platform Portal (v1 Spec)

This is a robot portal project for internal teams. The goal is to quickly implement a runnable and evolvable v1 version.

- **FastAPI Portal**
- **SQLite (single instance)**
- **Single replica Deployment + EBS PVC on EKS**
- Portal dynamically creates robot resources via Kubernetes API

---

## Current Implementation Progress

- ✅ FastAPI application skeleton (`app/main.py`)
- ✅ SQLite + SQLAlchemy models (`users`/`agents`/`audit_logs`)
- ✅ Basic authentication API (login/logout/me, cookie session)
- ✅ Admin user API (create/list/change password)
- ✅ Robot API (mine/public/create/detail/start/stop/share/unshare/delete/status + delete-runtime/destroy)
- ✅ Admin API (/api/admin/agents, /api/admin/audit-logs)
- ✅ k8s_service abstraction for robot lifecycle (supports local no-op mode)
- ✅ `/a/{agent_id}` reverse proxy access with permission and running status validation
- ✅ Dockerfile and dependency list
- ✅ Clean style Web UI (login page + console, ChatGPT style)
- ✅ Robot status transition constraints (start/stop with state machine validation)

### Local Development

```bash
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Access `http://localhost:8000/login`

The admin account will be created on first startup:

- username: `admin` (default, can be overridden via environment variable)
- password: `admin123` (default, for local development only)

### Kubernetes Configuration (Development/Production)

Control whether to actually call Kubernetes API via environment variables:

- `K8S_ENABLED=false` (default, local no-op)
- `K8S_ENABLED=true` (enable real K8s calls)
- `K8S_STORAGE_CLASS=gp3`
- `BOOTSTRAP_ADMIN_USERNAME=admin`
- `BOOTSTRAP_ADMIN_PASSWORD=admin123`

---

## 1. v1 Scope

### Included

- Local username/password login (session)
- Admin manually creates users
- My Space (my robots) / Public Space (public robots)
- Robot create, view, start, stop, delete
- Robot share/unshare
- Robot running status and error message viewing
- Portal running on EKS
- Each robot has:
  - 1 Deployment (replicas=1)
  - 1 Service
  - 1 PVC (dedicated storage)

### Not Included

- SSO
- Per-user separate namespace
- Complex RBAC / approval flow
- Multiple replica Portal
- PostgreSQL / RDS
- Operator / CRD
- Fine-grained collaborator model

---

## 2. Overall Architecture

```text
[Browser]
   |
   v
[ALB/Ingress]
   |
   v
[Portal Web/API - FastAPI]
   |    |  \--> [SQLite on EBS PVC]
   |
   \--> [Kubernetes API]
            |
            +--> create Deployment for agent
            +--> create PVC for agent
            +--> create Service for agent
            +--> query Pod / Deployment status
```

Recommended namespaces:

- `portal-system` (Portal)
- `agents` (Robot workloads)

---

## 3. Data Models (Minimum Viable)

### `users`

- `id` INTEGER PK
- `username` TEXT UNIQUE NOT NULL
- `password_hash` TEXT NOT NULL
- `role` TEXT NOT NULL (`admin`/`user`)
- `is_active` BOOLEAN NOT NULL DEFAULT 1
- `created_at` DATETIME NOT NULL
- `updated_at` DATETIME NOT NULL

### `agents`

- `id` TEXT PK (uuid)
- `name` TEXT NOT NULL
- `description` TEXT
- `owner_user_id` INTEGER NOT NULL (FK users.id)
- `visibility` TEXT NOT NULL (`private`/`public`)
- `status` TEXT NOT NULL (`creating`/`running`/`stopped`/`deleting`/`failed`)
- `image` TEXT NOT NULL
- `cpu` TEXT
- `memory` TEXT
- `disk_size_gi` INTEGER NOT NULL
- `mount_path` TEXT NOT NULL DEFAULT `/data`
- `namespace` TEXT NOT NULL
- `deployment_name` TEXT NOT NULL
- `service_name` TEXT NOT NULL
- `pvc_name` TEXT NOT NULL
- `endpoint_path` TEXT
- `last_error` TEXT
- `created_at` DATETIME NOT NULL
- `updated_at` DATETIME NOT NULL

### `audit_logs`

- `id` INTEGER PK
- `user_id` INTEGER
- `action` TEXT NOT NULL
- `target_type` TEXT NOT NULL (`user`/`agent`)
- `target_id` TEXT NOT NULL
- `details_json` TEXT
- `created_at` DATETIME NOT NULL

---

## 4. API Contract (v1)

### Authentication & Users

- `POST /api/auth/login`
- `POST /api/auth/logout`
- `GET /api/auth/me`
- `POST /api/users` (admin)
- `GET /api/users` (admin)
- `PATCH /api/users/{id}/password`

### Robots/Agents

- `GET /api/agents/mine`
- `GET /api/agents/public`
- `POST /api/agents`
- `GET /api/agents/{id}`
- `POST /api/agents/{id}/start`
- `POST /api/agents/{id}/stop`
- `POST /api/agents/{id}/share`
- `POST /api/agents/{id}/unshare`
- `DELETE /api/agents/{id}`
- `POST /api/agents/{id}/delete-runtime`
- `POST /api/agents/{id}/destroy`
- `GET /api/agents/{id}/status`

### Admin

- `GET /api/admin/agents`
- `GET /api/admin/audit-logs`

---

## 5. Robot Lifecycle

State Machine:

```text
creating -> running -> stopped
   |          |         |
   +--------> failed <-+
                         -> deleting
```

Creation Flow (simplified):

1. User submits `name/image/disk_size_gi/cpu/memory`
2. Write to DB, status = `creating`
3. Create PVC
4. Create Deployment (`replicas=1`)
5. Create Service
6. Check PVC Bound + Pod Ready
7. Success update to `running`, failure update to `failed + last_error`

Stop: Deployment scale to 0.  
Start: Deployment scale to 1.  
Delete: Support two actions:

- `Delete Runtime` (Delete Deployment + Service, keep PVC)
- `Destroy Completely` (Deployment + Service + PVC)

---

## 6. Recommended Directory Structure

```text
portal/
  app/
    main.py
    config.py
    db.py
    deps.py

    models/
      user.py
      agent.py
      audit_log.py

    schemas/
      auth.py
      user.py
      agent.py

    api/
      auth.py
      users.py
      agents.py
      admin.py

    services/
      auth_service.py
      user_service.py
      agent_service.py
      k8s_service.py
      proxy_service.py
      audit_service.py

    repositories/
      user_repo.py
      agent_repo.py
      audit_repo.py

    templates/
      login.html
      my_agents.html
      create_agent.html
      agent_detail.html
      public_space.html
      admin_users.html

    static/
      css/
      js/

  migrations/
  tests/
  Dockerfile
  requirements.txt
  alembic.ini
```

---

## 7. Access Path Recommendations

v1 recommended unified entry proxy:

- User access: `https://portal.example.com/a/{agent_id}/...`
- Portal internal proxy to: `http://agent-<id>-svc.agents.svc.cluster.local`

Advantages:

- Permission check centralized in Portal
- No need to maintain independent Ingress for each robot
- More suitable for v1 multi-tenant management

---

## 8. Kubernetes Minimum Permissions Recommendation

Portal's ServiceAccount only grants necessary permissions to `agents` namespace:

- Deployments: `get/list/watch/create/delete/patch`
- PVC: `get/list/watch/create/delete`
- Services: `get/list/watch/create/delete`
- Pods: `get/list/watch`

Resource labels:

- `owner-id`
- `agent-id`
- `visibility`

---

## 9. Phased Rollout Plan

### Phase 1: Portal Basic Available

- FastAPI project initialization
- SQLite + Alembic
- Login/session
- User management
- My/Public Space basic pages

### Phase 2: Robot Management (without K8s)

- Agents CRUD
- State machine and pages
- Basic audit logs

### Phase 3: Kubernetes Integration

- `k8s_service.py` (PVC/Deployment/Service lifecycle)
- Status polling and error writing

### Phase 4: Proxy and Share

- `/a/{agent_id}` request proxy
- Public Space display connected
- Operational observability added

---

## 10. Codex Task Breakdown (Can Execute Directly)

1. Initialize project skeleton (FastAPI + Jinja2 + SQLAlchemy + Alembic + Dockerfile)
2. Define `users/agents/audit_logs` data models and migrations
3. Implement session login and user management API
4. Implement My Space / Public Space pages and query API
5. Implement agents create/start/stop/delete/share API
6. Implement Kubernetes resource management service (create/scale/delete/status)
7. Implement `/a/{agent_id}` reverse proxy
8. Add tests, README, deployment YAML and operational documentation

---

## 11. Success Criteria (Definition of Done)

- Admin can create users and log in
- Regular users can only manage their own robots
- Users can create robots and see `creating -> running` on page
- Users can stop and restart robots
- Shared robots visible in Public Space after sharing
- Delete runtime won't accidentally delete data (keep PVC by default)
- Audit logs can track key actions
- Can run stably with single replica deployment on EKS

## 12. GitHub Actions Auto Build Image

The repository provides `.github/workflows/docker-image.yml`:

- On `main` branch push, `v*` tag push, or manual trigger: build and push image to `ghcr.io/<owner>/<repo>`.
- On Pull Request to `main`: only build validation, no image push.
- Use Buildx + GHA cache to speed up subsequent builds.

To use this workflow, ensure the repository has write permission for `GITHUB_TOKEN` packages.

---

## Tech Stack

- FastAPI
- SQLite + SQLAlchemy
- Kubernetes API
- HTMX + Alpine.js

## Project Structure

```
app/
  api/        # API endpoints
  db/         # Database models
  models/     # SQLAlchemy models
  services/   # Business logic
  static/     # CSS, JS
  templates/  # HTML templates
  web.py      # Web routes
  main.py     # Application entry
```

## Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| DATABASE_URL | SQLite database path | `portal.db` |
| SECRET_KEY | Session secret key | `change-me-in-production` |
| ADMIN_USERNAME | Admin username | `admin` |
| ADMIN_PASSWORD | Admin password | `admin123` |
| K8S_ENABLED | Enable Kubernetes integration | `false` |
| K8S_INCLUSTER | Use in-cluster config | `false` |
| K8S_KUBECONFIG | Path to kubeconfig | - |
| AGENTS_NAMESPACE | Agents namespace | `agents` |
| K8S_STORAGE_CLASS | Storage class for PVC | `gp3` |
| BOOTSTRAP_ADMIN_USERNAME | Bootstrap admin username | `admin` |
| BOOTSTRAP_ADMIN_PASSWORD | Bootstrap admin password | `admin123` |
