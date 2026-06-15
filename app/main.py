import logging
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.api.admin import router as admin_router
from app.api.auth import router as auth_router
from app.api.proxy import router as proxy_router
from app.api.runtime_profiles import router as runtime_profiles_router
from app.api.agent_tasks import router as agent_tasks_router
from app.api.internal_session_metadata import router as internal_session_metadata_router
from app.api.delegation_rules import router as delegation_rules_router
from app.api.runtime_capability_catalog import router as runtime_capability_catalog_router
from app.api.agents import router as agents_router
from app.api.internal_agents import router as internal_agents_router
from app.api.users import router as users_router
from app.api.copilot import router as copilot_router
from app.api.requirement_bundles import router as requirement_bundles_router
from app.config import get_settings
from app.db import SessionLocal, engine
from app.log_context import bind_log_context, generate_span_id, generate_trace_id, reset_log_context
from app.repositories.user_repo import UserRepository
from app.logger import setup_logging
from app.services.auth_service import hash_password
from app.services.runtime_profile_service import RuntimeProfileService
from app.services.schema_guard import (
    assert_phase5_schema_compatibility,
    assert_portal_schema_ready,
    assert_runtime_profile_schema_compatibility,
)
from app.web import router as web_router
from app.services.delegation_worker import worker_singleton
from app.services.runtime_profile_sync_worker import runtime_profile_sync_worker_singleton
from app.services.agent_task_reconcile_worker import agent_task_reconcile_worker_singleton

settings = get_settings()
app = FastAPI(title=settings.app_name, debug=settings.debug)


@app.middleware("http")
async def bind_request_log_context(request, call_next):
    trace_id = generate_trace_id()
    token = bind_log_context(
        trace_id=trace_id,
        span_id=generate_span_id(),
        parent_span_id="-",
        path=request.url.path,
    )
    try:
        response = await call_next(request)
    finally:
        reset_log_context(token)
    response.headers["X-Trace-Id"] = trace_id
    return response


@app.on_event("startup")
def on_startup() -> None:
    setup_logging(logging.DEBUG if settings.debug else logging.INFO)
    assert_portal_schema_ready(engine)
    assert_phase5_schema_compatibility(engine)
    assert_runtime_profile_schema_compatibility(engine)

    db = SessionLocal()
    try:
        repo = UserRepository(db)
        runtime_profile_service = RuntimeProfileService(db)
        # Validate admin password is set
        if not settings.bootstrap_admin_password:
            print("WARNING: BOOTSTRAP_ADMIN_PASSWORD not set! Admin account will not be created.")
        elif not repo.get_by_username(settings.bootstrap_admin_username):
            admin_user = repo.create(
                settings.bootstrap_admin_username,
                hash_password(settings.bootstrap_admin_password),
                role="admin",
            )
            runtime_profile_service.ensure_user_has_default_profile(admin_user)

        runtime_profile_service.ensure_defaults_for_all_users(db)
    finally:
        db.close()

    if settings.delegation_rules_worker_enabled:
        worker_singleton.start()
    if settings.runtime_profile_sync_worker_enabled:
        runtime_profile_sync_worker_singleton.start()
    if settings.agent_task_reconcile_worker_enabled:
        agent_task_reconcile_worker_singleton.start()


@app.on_event("shutdown")
def shutdown_delegation_worker() -> None:
    worker_singleton.stop()
    runtime_profile_sync_worker_singleton.stop()
    agent_task_reconcile_worker_singleton.stop()


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}

@app.get("/actuator/health")
def actuator_health() -> dict[str, str]:
    return {"status": "ok"}

app.mount("/static", StaticFiles(directory="app/static"), name="static")

app.include_router(web_router)
app.include_router(auth_router)
app.include_router(users_router)
app.include_router(agents_router)
app.include_router(internal_agents_router)
app.include_router(runtime_profiles_router)
app.include_router(admin_router)
app.include_router(proxy_router)
app.include_router(copilot_router)
app.include_router(requirement_bundles_router)
app.include_router(agent_tasks_router)
app.include_router(delegation_rules_router)
app.include_router(runtime_capability_catalog_router)
app.include_router(internal_session_metadata_router)
