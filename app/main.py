from fastapi import FastAPI

from app.api.admin import router as admin_router
from app.api.auth import router as auth_router
from app.api.proxy import router as proxy_router
from app.api.robots import router as robots_router
from app.api.users import router as users_router
from app.config import get_settings
from app.db import Base, SessionLocal, engine
from app.repositories.user_repo import UserRepository
from app.services.auth_service import hash_password

settings = get_settings()
app = FastAPI(title=settings.app_name, debug=settings.debug)


@app.on_event("startup")
def on_startup() -> None:
    Base.metadata.create_all(bind=engine)

    db = SessionLocal()
    try:
        repo = UserRepository(db)
        if not repo.get_by_username("admin"):
            repo.create("admin", hash_password("admin123"), role="admin")
    finally:
        db.close()


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


app.include_router(auth_router)
app.include_router(users_router)
app.include_router(robots_router)
app.include_router(admin_router)
app.include_router(proxy_router)
