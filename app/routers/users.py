import datetime
import re

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import PlainTextResponse, RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.deps import get_optional_admin
from app.models import AUTH_MODES, AdminUser, TrafficSample, VpnUser, generate_vpn_password
from app.openvpn import certs, mgmt, tlscrypt
from app.openvpn.templates import build_ovpn
from app.relays import relay_names_by_ip
from app.templating import templates

router = APIRouter()

_USERNAME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9_-]{2,31}$")
# CNs already used by the PKI itself, and what OpenVPN calls a client
# that has no certificate CN.
_RESERVED_USERNAMES = {"server", "ca", "undef"}
# OpenVPN passes printable ASCII through untouched; spaces are excluded so
# nobody has to guess whether one was typed.
_PASSWORD_RE = re.compile(r"^[\x21-\x7e]{4,64}$")
_PASSWORD_ERROR = "رمز باید ۴ تا ۶۴ کاراکتر از حروف و اعداد انگلیسی یا نمادها باشد (بدون فاصله و حروف فارسی)."


def _iso(dt: datetime.datetime | None) -> str | None:
    """Timestamps are stored as naive UTC; tag them so browsers don't read
    them as local time (which shifted every date by the viewer's offset)."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    return dt.isoformat()


class CreateUserRequest(BaseModel):
    username: str
    note: str | None = None
    data_limit_gb: float | None = Field(default=None, ge=0)
    expire_days: int | None = Field(default=None, ge=0)
    auth_mode: str = "cert"
    # empty -> generated when the mode needs one
    password: str | None = None
    max_devices: int = Field(default=0, ge=0, le=100)


class UpdateUserRequest(BaseModel):
    note: str | None = None
    data_limit_gb: float | None = Field(default=None, ge=0)
    expire_days: int | None = Field(default=None, ge=0)
    clear_limit: bool = False
    clear_expiry: bool = False
    # one-tap renewals: extend from the later of now / current expiry, and
    # top up the quota on top of whatever is left
    add_days: int | None = Field(default=None, ge=1, le=3650)
    add_gb: float | None = Field(default=None, gt=0, le=100000)
    auth_mode: str | None = None
    password: str | None = None
    regenerate_password: bool = False
    max_devices: int | None = Field(default=None, ge=0, le=100)


def _require_admin(admin: AdminUser | None):
    if not admin:
        raise HTTPException(status_code=401, detail="unauthorized")
    return admin


def _check_auth(mode: str, password: str | None) -> None:
    if mode not in AUTH_MODES:
        raise HTTPException(status_code=400, detail="روش ورود نامعتبر است.")
    if password and not _PASSWORD_RE.match(password):
        raise HTTPException(status_code=400, detail=_PASSWORD_ERROR)


def _online_map() -> dict[str, list[str]]:
    from app.openvpn.scheduler import get_online_sessions

    protos: dict[str, list[str]] = {}
    for sess in get_online_sessions():
        protos.setdefault(sess["username"], []).append(sess["proto"])
    return protos


def _serialize(
    user: VpnUser, online: dict[str, list[str]] | None = None, with_secret: bool = False
) -> dict:
    if online is None:
        online = _online_map()
    protos = sorted(online.get(user.username, []))
    data = {
        "id": user.id,
        "username": user.username,
        "note": user.note,
        "enabled": user.enabled,
        "revoked": user.revoked,
        "status": user.status_label(),
        "ending_soon": user.is_ending_soon(),
        "online": bool(protos),
        "online_protos": sorted(set(protos)),
        "devices_online": len(protos),
        "auth_mode": user.auth_mode or "cert",
        "has_password": bool(user.auth_password),
        "max_devices": user.max_devices or 0,
        "data_limit_bytes": user.data_limit_bytes,
        "data_used_bytes": user.data_used_bytes,
        "expire_at": _iso(user.expire_at),
        "days_left": user.days_left(),
        "created_at": _iso(user.created_at),
        "last_connected_at": _iso(user.last_connected_at),
        "last_ip": user.last_ip,
        "last_via": relay_names_by_ip().get(user.last_ip or ""),
        "token": user.token,
        "sub_link": f"{settings.public_base_url}/sub/{user.token}",
        "tls_key": bool(user.tls_key),
        "tls_key_seen_at": _iso(user.tls_key_seen_at),
    }
    if with_secret:
        # only on single-user responses, never in the list
        data["password"] = user.auth_password
    return data


@router.get("/users")
def users_page(request: Request, admin: AdminUser | None = Depends(get_optional_admin)):
    if not admin:
        return RedirectResponse(url="/login", status_code=302)
    return templates.TemplateResponse(
        "users.html",
        {"request": request, "admin": admin, "panel_title": settings.PANEL_TITLE},
    )


@router.get("/api/users")
def list_users(admin: AdminUser | None = Depends(get_optional_admin), db: Session = Depends(get_db)):
    _require_admin(admin)
    online = _online_map()
    users = db.query(VpnUser).order_by(VpnUser.created_at.desc()).all()
    return [_serialize(u, online) for u in users]


@router.post("/api/users")
def create_user(
    payload: CreateUserRequest,
    admin: AdminUser | None = Depends(get_optional_admin),
    db: Session = Depends(get_db),
):
    _require_admin(admin)

    username = payload.username.strip()
    if not _USERNAME_RE.match(username):
        raise HTTPException(
            status_code=400,
            detail="نام کاربری باید با حرف شروع شود و فقط شامل حروف/عدد انگلیسی، خط تیره و "
            "زیرخط باشد (۳ تا ۳۲ کاراکتر).",
        )

    if username.lower() in _RESERVED_USERNAMES:
        raise HTTPException(status_code=400, detail="این نام کاربری رزرو شده است؛ نام دیگری انتخاب کنید.")

    # Case-insensitive: password logins accept "Reza" for "reza".
    if db.query(VpnUser).filter(func.lower(VpnUser.username) == username.lower()).first():
        raise HTTPException(status_code=409, detail="این نام کاربری قبلا استفاده شده است.")

    password = (payload.password or "").strip() or None
    _check_auth(payload.auth_mode, password)

    try:
        certs.build_client_cert(username)
    except certs.CertError as exc:
        raise HTTPException(status_code=500, detail=f"خطا در ساخت گواهی: {exc}") from exc

    user = VpnUser(
        username=username,
        note=payload.note,
        auth_mode=payload.auth_mode,
        max_devices=payload.max_devices,
        # generated up front even in certificate mode, so switching to a
        # password mode later just works
        auth_password=password or generate_vpn_password(),
    )
    if payload.data_limit_gb:
        user.data_limit_bytes = int(payload.data_limit_gb * (1024**3))
    if payload.expire_days:
        user.expire_at = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(
            days=payload.expire_days
        )

    if tlscrypt.get_mode(db) != "shared":
        try:
            tlscrypt.issue_user_key(user)
        except tlscrypt.TlsKeyError:
            pass  # made on the first download instead

    db.add(user)
    db.commit()
    db.refresh(user)

    return _serialize(user, with_secret=True)


@router.get("/api/users/{user_id}")
def get_user(
    user_id: int,
    admin: AdminUser | None = Depends(get_optional_admin),
    db: Session = Depends(get_db),
):
    _require_admin(admin)
    user = db.get(VpnUser, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="not found")

    data = _serialize(user, with_secret=True)
    data["tls_mode"] = tlscrypt.get_mode(db)

    since = datetime.date.today() - datetime.timedelta(days=13)
    rows = (
        db.query(TrafficSample)
        .filter(TrafficSample.vpn_user_id == user.id, TrafficSample.date >= since)
        .order_by(TrafficSample.date)
        .all()
    )
    by_date = {r.date.isoformat(): r.bytes_total for r in rows}
    dates = [(since + datetime.timedelta(days=i)).isoformat() for i in range(14)]
    data["chart_dates"] = dates
    data["chart_values"] = [by_date.get(d, 0) for d in dates]
    return data


@router.patch("/api/users/{user_id}")
def update_user(
    user_id: int,
    payload: UpdateUserRequest,
    admin: AdminUser | None = Depends(get_optional_admin),
    db: Session = Depends(get_db),
):
    _require_admin(admin)
    user = db.get(VpnUser, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="not found")

    if payload.note is not None:
        user.note = payload.note
    if payload.clear_limit:
        user.data_limit_bytes = None
    elif payload.data_limit_gb is not None:
        user.data_limit_bytes = int(payload.data_limit_gb * (1024**3)) if payload.data_limit_gb else None
    if payload.clear_expiry:
        user.expire_at = None
    elif payload.expire_days is not None:
        user.expire_at = (
            datetime.datetime.now(datetime.timezone.utc)
            + datetime.timedelta(days=payload.expire_days)
            if payload.expire_days
            else None
        )

    if payload.add_days:
        now = datetime.datetime.now(datetime.timezone.utc)
        current = user.expire_at.replace(tzinfo=datetime.timezone.utc) if user.expire_at else None
        base = current if current and current > now else now
        user.expire_at = base + datetime.timedelta(days=payload.add_days)
    if payload.add_gb and user.data_limit_bytes is not None:
        user.data_limit_bytes += int(payload.add_gb * (1024**3))

    # Login changes cut the user's open sessions, so the old way in stops
    # working right away instead of at the next reconnect.
    relogin = False
    if payload.auth_mode is not None and payload.auth_mode != user.auth_mode:
        _check_auth(payload.auth_mode, None)
        user.auth_mode = payload.auth_mode
        relogin = True
    new_password = (payload.password or "").strip()
    if payload.regenerate_password:
        new_password = generate_vpn_password()
    if new_password and new_password != user.auth_password:
        _check_auth(user.auth_mode, new_password)
        user.auth_password = new_password
        relogin = relogin or user.needs_password
    if user.needs_password and not user.auth_password:
        user.auth_password = generate_vpn_password()
    if payload.max_devices is not None:
        user.max_devices = payload.max_devices

    db.commit()
    db.refresh(user)
    if relogin:
        _kill_everywhere(user.username)
    elif payload.max_devices:
        from app.openvpn.scheduler import enforce_device_limit_soon

        enforce_device_limit_soon(user.username, user.max_devices, delay=0)
    return _serialize(user, with_secret=True)


def _kill_everywhere(username: str) -> None:
    mgmt.kill_everywhere((settings.OVPN_UDP_MGMT_PORT, settings.OVPN_TCP_MGMT_PORT), username)


@router.post("/api/users/{user_id}/toggle")
def toggle_user(
    user_id: int,
    admin: AdminUser | None = Depends(get_optional_admin),
    db: Session = Depends(get_db),
):
    _require_admin(admin)
    user = db.get(VpnUser, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="not found")

    user.enabled = not user.enabled
    db.commit()
    db.refresh(user)

    if not user.enabled:
        _kill_everywhere(user.username)

    return _serialize(user)


@router.post("/api/users/{user_id}/reset_usage")
def reset_usage(
    user_id: int,
    admin: AdminUser | None = Depends(get_optional_admin),
    db: Session = Depends(get_db),
):
    _require_admin(admin)
    user = db.get(VpnUser, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="not found")
    user.data_used_bytes = 0
    db.commit()
    db.refresh(user)
    return _serialize(user)


@router.post("/api/users/{user_id}/regenerate_token")
def regenerate_token(
    user_id: int,
    admin: AdminUser | None = Depends(get_optional_admin),
    db: Session = Depends(get_db),
):
    _require_admin(admin)
    user = db.get(VpnUser, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="not found")
    user.regenerate_token()
    db.commit()
    db.refresh(user)
    return _serialize(user)


@router.post("/api/users/{user_id}/regenerate_key")
def regenerate_key(
    user_id: int,
    admin: AdminUser | None = Depends(get_optional_admin),
    db: Session = Depends(get_db),
):
    """New personal tls-crypt-v2 key: every config file the user had stops
    working right away (open sessions are cut), nobody else is touched."""
    _require_admin(admin)
    user = db.get(VpnUser, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="not found")
    try:
        tlscrypt.issue_user_key(user)
    except tlscrypt.TlsKeyError as exc:
        raise HTTPException(status_code=500, detail=f"ساخت کلید ناموفق بود: {exc}") from exc
    db.commit()
    db.refresh(user)
    if tlscrypt.get_mode(db) != "shared":
        _kill_everywhere(user.username)
    return _serialize(user, with_secret=True)


@router.delete("/api/users/{user_id}")
def delete_user(
    user_id: int,
    admin: AdminUser | None = Depends(get_optional_admin),
    db: Session = Depends(get_db),
):
    _require_admin(admin)
    user = db.get(VpnUser, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="not found")

    username = user.username
    try:
        certs.revoke_client_cert(username)
    except certs.CertError:
        pass
    _kill_everywhere(username)
    certs.cleanup_client_files(username)

    db.query(TrafficSample).filter(TrafficSample.vpn_user_id == user.id).delete()
    db.delete(user)
    db.commit()
    return {"ok": True}


@router.get("/api/users/{user_id}/config/{proto}")
def download_config(
    user_id: int,
    proto: str,
    admin: AdminUser | None = Depends(get_optional_admin),
    db: Session = Depends(get_db),
):
    _require_admin(admin)
    if proto not in ("udp", "tcp"):
        raise HTTPException(status_code=400, detail="invalid proto")
    user = db.get(VpnUser, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="not found")

    try:
        content = build_ovpn(db, user, proto)
    except certs.CertError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    filename = f"{user.username}-{proto}.ovpn"
    return PlainTextResponse(
        content,
        media_type="application/x-openvpn-profile",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
