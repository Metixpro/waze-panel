import datetime
import re

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import PlainTextResponse, RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.deps import get_optional_admin
from app.models import AdminUser, TrafficSample, VpnUser
from app.openvpn import certs, mgmt
from app.openvpn.templates import build_ovpn
from app.templating import templates

router = APIRouter()

_USERNAME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9_-]{2,31}$")


class CreateUserRequest(BaseModel):
    username: str
    note: str | None = None
    data_limit_gb: float | None = Field(default=None, ge=0)
    expire_days: int | None = Field(default=None, ge=0)


class UpdateUserRequest(BaseModel):
    note: str | None = None
    data_limit_gb: float | None = Field(default=None, ge=0)
    expire_days: int | None = Field(default=None, ge=0)
    clear_limit: bool = False
    clear_expiry: bool = False


def _require_admin(admin: AdminUser | None):
    if not admin:
        raise HTTPException(status_code=401, detail="unauthorized")
    return admin


def _serialize(user: VpnUser, online_usernames: set[str]) -> dict:
    if user.revoked:
        status_label = "revoked"
    elif not user.enabled:
        status_label = "disabled"
    elif user.is_expired():
        status_label = "expired"
    elif user.is_over_quota():
        status_label = "over_quota"
    else:
        status_label = "active"

    return {
        "id": user.id,
        "username": user.username,
        "note": user.note,
        "enabled": user.enabled,
        "revoked": user.revoked,
        "status": status_label,
        "online": user.username in online_usernames,
        "data_limit_bytes": user.data_limit_bytes,
        "data_used_bytes": user.data_used_bytes,
        "expire_at": user.expire_at.isoformat() if user.expire_at else None,
        "created_at": user.created_at.isoformat() if user.created_at else None,
        "last_connected_at": user.last_connected_at.isoformat()
        if user.last_connected_at
        else None,
        "last_ip": user.last_ip,
        "token": user.token,
        "sub_link": f"{settings.public_base_url}/sub/{user.token}",
    }


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
    from app.openvpn.scheduler import get_online_usernames

    online = get_online_usernames()
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

    if db.query(VpnUser).filter(VpnUser.username == username).first():
        raise HTTPException(status_code=409, detail="این نام کاربری قبلا استفاده شده است.")

    try:
        certs.build_client_cert(username)
    except certs.CertError as exc:
        raise HTTPException(status_code=500, detail=f"خطا در ساخت گواهی: {exc}") from exc

    user = VpnUser(username=username, note=payload.note)
    if payload.data_limit_gb:
        user.data_limit_bytes = int(payload.data_limit_gb * (1024**3))
    if payload.expire_days:
        user.expire_at = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(
            days=payload.expire_days
        )

    db.add(user)
    db.commit()
    db.refresh(user)

    from app.openvpn.scheduler import get_online_usernames

    return _serialize(user, get_online_usernames())


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

    from app.openvpn.scheduler import get_online_usernames

    data = _serialize(user, get_online_usernames())

    since = datetime.date.today() - datetime.timedelta(days=13)
    rows = (
        db.query(TrafficSample)
        .filter(TrafficSample.vpn_user_id == user.id, TrafficSample.date >= since)
        .order_by(TrafficSample.date)
        .all()
    )
    by_date = {r.date.isoformat(): r.bytes_total for r in rows}
    labels, values = [], []
    for i in range(14):
        d = since + datetime.timedelta(days=i)
        labels.append(d.strftime("%m-%d"))
        values.append(by_date.get(d.isoformat(), 0))
    data["chart_labels"] = labels
    data["chart_values"] = values
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

    db.commit()
    db.refresh(user)
    from app.openvpn.scheduler import get_online_usernames

    return _serialize(user, get_online_usernames())


def _kill_everywhere(username: str) -> None:
    for port in (settings.OVPN_UDP_MGMT_PORT, settings.OVPN_TCP_MGMT_PORT):
        try:
            mgmt.kill_client(port, username)
        except mgmt.ManagementError:
            pass


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

    from app.openvpn.scheduler import get_online_usernames

    return _serialize(user, get_online_usernames())


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
    from app.openvpn.scheduler import get_online_usernames

    return _serialize(user, get_online_usernames())


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
    from app.openvpn.scheduler import get_online_usernames

    return _serialize(user, get_online_usernames())


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
        content = build_ovpn(user.username, proto)
    except certs.CertError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    filename = f"{user.username}-{proto}.ovpn"
    return PlainTextResponse(
        content,
        media_type="application/x-openvpn-profile",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
