# Engineering Flow Platform Portal

A robot portal for internal teams. The goal is to quickly implement a runnable and evolvable v1:

## Features

- **FastAPI Portal**
- **SQLite (single instance)**
- **Single replica Deployment + EBS PVC on EKS**
- Portal dynamically creates robot resources via Kubernetes API

## Quick Start



Access `http://localhost:8000/login`

## Default Credentials

- username: `admin`
- password: `admin123` (configurable via environment variable)

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
```
