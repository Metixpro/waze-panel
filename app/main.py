import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from starlette.middleware.sessions import SessionMiddleware
from starlette.staticfiles import StaticFiles

from app.config import settings
from app.database import SessionLocal, init_db
from app.openvpn.scheduler import start_scheduler, stop_scheduler
from app.routers import auth, dashboard, internal, settings as settings_router, subscription, users
from app.settings_store import load_overrides

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

STATIC_DIR = Path(__file__).resolve().parent / "static"

@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    db = SessionLocal()
    try:
        load_overrides(db)
    finally:
        db.close()
    start_scheduler()
    try:
        yield
    finally:
        stop_scheduler()


app = FastAPI(title=settings.APP_NAME, docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)

app.add_middleware(
    SessionMiddleware,
    secret_key=settings.SECRET_KEY,
    session_cookie="waze_panel_session",
    same_site="lax",
    max_age=60 * 60 * 24 * 7,  # 7 days
)

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

app.include_router(auth.router)
app.include_router(dashboard.router)
app.include_router(users.router)
app.include_router(subscription.router)
app.include_router(internal.router)
app.include_router(settings_router.router)
