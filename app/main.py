import logging
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.api.admin import router as admin_router
from app.api.auth import router as auth_router
from app.api.proxy import router as proxy_router
from app.api.capability_profiles import router as capability_profiles_router
from app.api.policy_profiles import router as policy_profiles_router
from app.api.agent_identity_bindings import router as agent_identity_bindings_router
from app.api.runtime_router import router as runtime_router
from app.api.external_event_subscriptions import router as external_event_subscriptions_router
from app.api.agent_tasks import router as agent_tasks_router
from app.api.external_event_ingress import router as external_event_ingress_router
from app.api.agents import router as agents_router
from app.api.users import router as users_router
from app.api.copilot import router as copilot_router
from app.config import get_settings
from app.db import Base, SessionLocal, engine
from app.repositories.user_repo import UserRepository
from app.logger import setup_logging
from app.services.auth_service import hash_password
from app.web import router as web_router

settings = get_settings()
app = FastAPI(title=settings.app_name, debug=settings.debug)


@app.on_event("startup")
def on_startup() -> None:
    setup_logging(logging.DEBUG if settings.debug else logging.INFO)
    Base.metadata.create_all(bind=engine)

    db = SessionLocal()
    try:
        repo = UserRepository(db)
        # Validate admin password is set
        if not settings.bootstrap_admin_password:
            print("WARNING: BOOTSTRAP_ADMIN_PASSWORD not set! Admin account will not be created.")
        elif not repo.get_by_username(settings.bootstrap_admin_username):
            repo.create(
                settings.bootstrap_admin_username,
                hash_password(settings.bootstrap_admin_password),
                role="admin",
            )
    finally:
        db.close()


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
app.include_router(capability_profiles_router)
app.include_router(policy_profiles_router)
app.include_router(agent_identity_bindings_router)
app.include_router(admin_router)
app.include_router(proxy_router)
app.include_router(copilot_router)
app.include_router(runtime_router)
app.include_router(external_event_subscriptions_router)
app.include_router(agent_tasks_router)
app.include_router(external_event_ingress_router)
