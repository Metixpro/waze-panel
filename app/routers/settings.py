import re

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app import connection
from app.backup import create_backup
from app.config import settings
from app.database import get_db
from app.deps import get_optional_admin
from app.models import AdminUser, VpnUser
from app.openvpn import certs, tlscrypt
from app.openvpn.templates import build_shared_ovpn
from app.security import hash_password, verify_password
from app.settings_store import set_value
from app.templating import templates

router = APIRouter()


class UpdatePanelSettings(BaseModel):
    server_address: str
    panel_title: str
    subscription_base_url: str = ""


class KeyMode(BaseModel):
    mode: str


class ExtraPorts(BaseModel):
    udp: list[int] = Field(default_factory=list, max_length=16)
    tcp: list[int] = Field(default_factory=list, max_length=16)


class Cover(BaseModel):
    enabled: bool


class ChangePassword(BaseModel):
    current_password: str
    new_password: str


@router.get("/settings")
def settings_page(
    request: Request,
    admin: AdminUser | None = Depends(get_optional_admin),
    db: Session = Depends(get_db),
):
    if not admin:
        return RedirectResponse(url="/login", status_code=302)
    return templates.TemplateResponse(
        "settings.html",
        {
            "request": request,
            "admin": admin,
            "panel_title": settings.PANEL_TITLE,
            "server_address": settings.SERVER_ADDRESS,
            "subscription_base_url": settings.SUBSCRIPTION_BASE_URL,
            "udp_port": settings.OVPN_UDP_PORT,
            "tcp_port": settings.OVPN_TCP_PORT,
            "panel_port": settings.PANEL_PORT,
            "pass_users": db.query(VpnUser).filter(VpnUser.auth_mode == "pass").count(),
            "tls_mode": tlscrypt.get_mode(db),
            "udp_extra": connection.extra_ports(db, "udp"),
            "tcp_extra": connection.extra_ports(db, "tcp"),
        },
    )


@router.post("/api/settings")
def update_settings(
    payload: UpdatePanelSettings,
    admin: AdminUser | None = Depends(get_optional_admin),
    db: Session = Depends(get_db),
):
    if not admin:
        raise HTTPException(status_code=401, detail="unauthorized")

    address = payload.server_address.strip()
    if not re.fullmatch(r"[A-Za-z0-9.\-:\[\]]{1,253}", address):
        raise HTTPException(status_code=400, detail="آدرس سرور باید یک IP یا دامنه معتبر باشد.")
    base_url = payload.subscription_base_url.strip()
    if base_url and not re.fullmatch(r"https?://[^\s\"'<>]+", base_url):
        raise HTTPException(status_code=400, detail="آدرس پایه لینک اشتراک باید با http:// یا https:// شروع شود.")

    set_value(db, "SERVER_ADDRESS", address)
    set_value(db, "PANEL_TITLE", payload.panel_title.strip() or "Waze Panel")
    set_value(db, "SUBSCRIPTION_BASE_URL", base_url)
    return {"ok": True}


@router.post("/api/settings/password")
def change_password(
    payload: ChangePassword,
    admin: AdminUser | None = Depends(get_optional_admin),
    db: Session = Depends(get_db),
):
    if not admin:
        raise HTTPException(status_code=401, detail="unauthorized")

    if not verify_password(payload.current_password, admin.password_hash):
        raise HTTPException(status_code=400, detail="رمز عبور فعلی اشتباه است.")
    if len(payload.new_password) < 6:
        raise HTTPException(status_code=400, detail="رمز عبور جدید باید حداقل ۶ کاراکتر باشد.")

    admin.password_hash = hash_password(payload.new_password)
    db.commit()
    return {"ok": True}


@router.get("/api/settings/backup")
def download_backup(admin: AdminUser | None = Depends(get_optional_admin)):
    if not admin:
        raise HTTPException(status_code=401, detail="unauthorized")
    filename, data = create_backup()
    return Response(
        content=data,
        media_type="application/gzip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/api/settings/shared-config/{proto}")
def download_shared_config(
    proto: str,
    admin: AdminUser | None = Depends(get_optional_admin),
    db: Session = Depends(get_db),
):
    """The certificate-less profile every password-only user can use."""
    if not admin:
        raise HTTPException(status_code=401, detail="unauthorized")
    if proto not in ("udp", "tcp"):
        raise HTTPException(status_code=400, detail="invalid proto")
    try:
        content = build_shared_ovpn(db, proto)
    except certs.CertError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    name = re.sub(r"[^A-Za-z0-9_-]+", "-", settings.SERVER_ADDRESS).strip("-") or "waze"
    return Response(
        content=content,
        media_type="application/x-openvpn-profile",
        headers={"Content-Disposition": f'attachment; filename="{name}-{proto}.ovpn"'},
    )


# ---------------------------------------------------------- connection options
def _admin(admin: AdminUser | None) -> None:
    if not admin:
        raise HTTPException(status_code=401, detail="unauthorized")


def _apply(fn, *args):
    try:
        return fn(*args)
    except connection.OptionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/api/settings/connection")
def connection_status(admin: AdminUser | None = Depends(get_optional_admin), db: Session = Depends(get_db)):
    _admin(admin)
    return connection.status(db)


@router.post("/api/settings/connection/keys")
def set_key_mode(payload: KeyMode, admin: AdminUser | None = Depends(get_optional_admin), db: Session = Depends(get_db)):
    """Restarts both OpenVPN instances (a few seconds of reconnecting)."""
    _admin(admin)
    result = _apply(connection.set_tls_mode, db, payload.mode)
    return {**connection.status(db), "restarted": result.get("restarted", [])}


@router.post("/api/settings/connection/ports")
def set_extra_ports(payload: ExtraPorts, admin: AdminUser | None = Depends(get_optional_admin), db: Session = Depends(get_db)):
    _admin(admin)
    _apply(connection.set_extra_ports, db, payload.udp, payload.tcp)
    return connection.status(db)


@router.post("/api/settings/connection/cover")
def set_cover(payload: Cover, admin: AdminUser | None = Depends(get_optional_admin), db: Session = Depends(get_db)):
    """Restarts the TCP instance."""
    _admin(admin)
    result = _apply(connection.set_cover, db, payload.enabled)
    return {**connection.status(db), "restarted": result.get("restarted", [])}


@router.post("/api/settings/shared-key")
def regenerate_shared_key(admin: AdminUser | None = Depends(get_optional_admin), db: Session = Depends(get_db)):
    """New key for the shared password-only profile; copies of the old file
    stop connecting."""
    _admin(admin)
    try:
        tlscrypt.regenerate_shared_key(db)
    except tlscrypt.TlsKeyError as exc:
        raise HTTPException(status_code=500, detail=f"ساخت کلید ناموفق بود: {exc}") from exc
    return {"ok": True}
