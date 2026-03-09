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



Access `http://localhost:8000/login`

The admin account will be created on first startup:

- username: `admin` (default, can be overridden via environment variable)
- password: `admin123` (default, for local development only)

### Kubernetes Configuration (Development/Production)

Control whether to actually call Kubernetes API via environment variables:

- `K8S_ENABLED=false` (default, local no-op)
- `K8S_ENABLED=true` (enable real K8s calls)
- `ROBOTS_NAMESPACE=agents`
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
| ROBOTS_NAMESPACE | Robots namespace | `robots` |
| AGENTS_NAMESPACE | Agents namespace | `agents` |
| K8S_STORAGE_CLASS | Storage class for PVC | `gp3` |
| BOOTSTRAP_ADMIN_USERNAME | Bootstrap admin username | `admin` |
| BOOTSTRAP_ADMIN_PASSWORD | Bootstrap admin password | `admin123` |
