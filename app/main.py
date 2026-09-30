import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from starlette.middleware.sessions import SessionMiddleware
from starlette.staticfiles import StaticFiles

from app import connection
from app.xray import core as xray_core
from app.config import settings
from app.database import SessionLocal, init_db
from app.openvpn.scheduler import start_scheduler, stop_scheduler
from app.routers import about, auth, dashboard, internal, relays, settings as settings_router, subscription, users, xray
from app.pwa import web_manifest
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
        # extra ports, OpenVPN include files, the HTTPS cover site
        connection.startup(db)
    finally:
        db.close()
    start_scheduler()
    xray_core.startup()
    try:
        yield
    finally:
        stop_scheduler()


app = FastAPI(title=settings.APP_NAME, docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)


@app.middleware("http")
async def add_security_headers(request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "geolocation=(), camera=(), microphone=()"
    return response


app.add_middleware(
    SessionMiddleware,
    secret_key=settings.SECRET_KEY,
    session_cookie="waze_panel_session",
    same_site="lax",
    https_only=settings.COOKIE_SECURE,
    max_age=60 * 60 * 24 * 7,  # 7 days
)

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

app.include_router(auth.router)
app.include_router(dashboard.router)
app.include_router(users.router)
app.include_router(subscription.router)
app.include_router(internal.router)
app.include_router(settings_router.router)
app.include_router(relays.router)
app.include_router(relays.sync_router)
app.include_router(about.router)
app.include_router(xray.router)


@app.get("/manifest.webmanifest", include_in_schema=False)
def panel_manifest():
    return web_manifest(settings.PANEL_TITLE, settings.PANEL_TITLE, "/dashboard", "/")


@app.get("/relay.sh", include_in_schema=False)
def relay_script():
    """The relay installer, for relay servers that can't reach GitHub."""
    return FileResponse(STATIC_DIR.parent.parent / "relay.sh", media_type="text/x-shellscript")


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    return FileResponse(STATIC_DIR / "favicon.svg", media_type="image/svg+xml")
