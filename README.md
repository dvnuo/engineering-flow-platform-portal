# Engineering Flow Platform Portal

A robot portal for internal teams. The goal is to quickly implement a runnable and evolvable v1 version.

## Features

- **FastAPI Portal**
- **SQLite (single instance)**
- **Single replica Deployment + EBS PVC on EKS**
- Portal dynamically creates robot resources via Kubernetes API

## Completed Items

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

## Quick Start



Access `http://localhost:8000/login`

## Default Credentials

- username: `admin` (default, can be overridden via environment variable)
- password: `admin123`

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
  static/    # CSS, JS
  templates/ # HTML templates
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
