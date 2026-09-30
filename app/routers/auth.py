import datetime
import threading
import time

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.deps import SESSION_KEY, SESSION_VERSION_KEY, get_optional_admin
from app.models import AdminUser
from app.security import hash_password, verify_password
from app.templating import templates

router = APIRouter()

# Brute-force protection: after MAX_FAILURES failed logins from one IP
# within WINDOW_SECONDS, that IP is refused until the window slides past.
MAX_FAILURES = 5
WINDOW_SECONDS = 600
MAX_FAILURES_ENTRIES = 5000

_failures: dict[str, list[float]] = {}
_failures_lock = threading.Lock()

# Compared against when the username doesn't exist, so a wrong username
# takes as long as a wrong password (no username enumeration by timing).
_DUMMY_HASH = hash_password("waze-panel-dummy-password")


def _client_ip(request: Request) -> str:
    host = request.client.host if request.client else "unknown"
    # Only trust forwarding headers when the direct peer is a local proxy.
    if host in ("127.0.0.1", "::1"):
        forwarded = request.headers.get("x-real-ip") or request.headers.get("x-forwarded-for", "")
        forwarded = forwarded.split(",")[0].strip()
        if forwarded:
            return forwarded
    return host


def _recent_failures(ip: str, now: float) -> list[float]:
    # Prune expired entries if dictionary grows too large
    if len(_failures) > MAX_FAILURES_ENTRIES:
        expired_ips = [k for k, v in _failures.items() if not v or (now - v[-1] >= WINDOW_SECONDS)]
        for k in expired_ips:
            _failures.pop(k, None)

    attempts = [t for t in _failures.get(ip, []) if now - t < WINDOW_SECONDS]
    if attempts:
        _failures[ip] = attempts
    else:
        _failures.pop(ip, None)
    return attempts


def _login_error(request: Request, message: str, status_code: int, username: str = ""):
    # keep the typed username so only the password has to be retyped
    return templates.TemplateResponse(
        "login.html",
        {"request": request, "error": message, "panel_title": settings.PANEL_TITLE, "username": username[:64]},
        status_code=status_code,
    )


@router.get("/login")
def login_page(request: Request, admin: AdminUser | None = Depends(get_optional_admin)):
    if admin:
        return RedirectResponse(url="/dashboard", status_code=302)
    return templates.TemplateResponse(
        "login.html", {"request": request, "error": None, "panel_title": settings.PANEL_TITLE}
    )


@router.post("/login")
def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    ip = _client_ip(request)
    now = time.time()
    with _failures_lock:
        attempts = _recent_failures(ip, now)
        if len(attempts) >= MAX_FAILURES:
            wait_min = int((WINDOW_SECONDS - (now - attempts[0])) // 60) + 1
            return _login_error(
                request,
                f"تعداد تلاش‌های ناموفق زیاد است. {wait_min} دقیقه دیگر دوباره امتحان کنید.",
                429,
                username,
            )

    admin = db.query(AdminUser).filter(AdminUser.username == username).first()
    ok = verify_password(password, admin.password_hash if admin else _DUMMY_HASH)
    if not admin or not ok:
        with _failures_lock:
            _failures.setdefault(ip, []).append(now)
        return _login_error(request, "نام کاربری یا رمز عبور اشتباه است.", 401, username)

    with _failures_lock:
        _failures.pop(ip, None)

    admin.last_login_at = datetime.datetime.now(datetime.timezone.utc)
    db.commit()

    request.session.clear()
    request.session[SESSION_KEY] = admin.id
    request.session[SESSION_VERSION_KEY] = admin.session_version
    return RedirectResponse(url="/dashboard", status_code=302)


@router.get("/logout")
def logout(
    request: Request,
    admin: AdminUser | None = Depends(get_optional_admin),
    db: Session = Depends(get_db),
):
    if admin:
        admin.session_version = (admin.session_version or 1) + 1
        db.commit()
    request.session.clear()
    return RedirectResponse(url="/login", status_code=302)
