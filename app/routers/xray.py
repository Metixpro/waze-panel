"""Xray page and API: inbounds (create from a preset, edit, order, enable,
delete), core status, REALITY target check."""
import json
import secrets
import socket
import ssl
import time

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app import connection
from app.config import settings
from app.database import get_db
from app.deps import get_optional_admin
from app.models import AdminUser, RelayServer, VpnUser, XrayInbound
from app.templating import templates
from app.xray import core, links, presets

router = APIRouter()

# Ports that look like HTTPS (and that Cloudflare proxies with TLS), and the
# ones Cloudflare proxies as plain HTTP -- what a CDN inbound without TLS needs.
TLS_PORTS = (443, 8443, 2053, 2083, 2087, 2096, 10443, 9443)
HTTP_PORTS = (80, 8080, 8880, 2052, 2082, 2086, 2095)
SECRET_OPTIONS = ("private_key", "server_key")


class InboundIn(BaseModel):
    name: str = Field(min_length=1, max_length=40)
    protocol: str
    transport: str
    security: str
    port: int = Field(ge=1, le=65535)
    options: dict = Field(default_factory=dict)
    enabled: bool = True


class InboundPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=40)
    port: int | None = Field(default=None, ge=1, le=65535)
    options: dict | None = None
    enabled: bool | None = None


class Order(BaseModel):
    ids: list[int]


class Target(BaseModel):
    sni: str


def _admin(admin: AdminUser | None) -> None:
    if not admin:
        raise HTTPException(status_code=401, detail="unauthorized")


def _serialize(ib: XrayInbound, listening: set[int] | None = None, users: list[VpnUser] | None = None) -> dict:
    opts = core.options(ib)
    out = {
        "id": ib.id,
        "name": ib.name,
        "protocol": ib.protocol,
        "transport": ib.transport,
        "security": ib.security,
        "label": links.label(ib),
        "port": ib.port,
        "enabled": ib.enabled,
        "traffic": ib.traffic_bytes or 0,
        "options": {k: v for k, v in opts.items() if k not in SECRET_OPTIONS},
    }
    if listening is not None:
        out["listening"] = ib.port in listening
    if users is not None:
        # who has this inbound: active users with Xray, limited to it or not
        out["users"] = sum(1 for u in users if u.uses_inbound(ib.id))
    if ib.security == "tls":
        try:
            out["cert"] = "self_signed" if core.tls_files(ib)[2] else "letsencrypt"
        except Exception:
            out["cert"] = "error"
    return out


# ---------------------------------------------------------------- ports
def _xray_ports(db: Session, skip_id: int | None = None) -> dict[int, str]:
    return {ib.port: ib.name for ib in db.query(XrayInbound).all() if ib.id != skip_id}


def port_problem(db: Session, port: int, udp: bool, own_id: int | None = None) -> str | None:
    fa = connection._fa
    if not 1 <= port <= 65535:
        return "پورت باید عددی بین ۱ تا ۶۵۵۳۵ باشد."
    others = _xray_ports(db, own_id)
    if port in others:
        return f"پورت {fa(port)} مال ورودی «{others[port]}» است."
    reserved_tcp = {
        settings.PANEL_PORT: "پنل", settings.OVPN_UDP_MGMT_PORT: "پنل", settings.OVPN_TCP_MGMT_PORT: "پنل",
        settings.COVER_PORT: "پنل", settings.XRAY_API_PORT: "Xray", connection.main_port("tcp"): "OpenVPN TCP",
    }
    for p in connection.extra_ports(db, "tcp"):
        reserved_tcp[p] = "پورت پشتیبان OpenVPN"
    if port in reserved_tcp:
        return f"پورت {fa(port)} را {reserved_tcp[port]} استفاده می‌کند."
    if udp and (port == connection.main_port("udp") or port in connection.extra_ports(db, "udp")):
        return f"پورت {fa(port)} (UDP) را OpenVPN استفاده می‌کند."
    mine = {ib.port for ib in db.query(XrayInbound).all() if own_id is not None and ib.id == own_id}
    for proto in ("tcp", "udp") if udp else ("tcp",):
        if port in connection.listening_ports(proto) - mine - set(others):
            return f"پورت {fa(port)} ({proto.upper()}) را برنامه‌ی دیگری روی این سرور استفاده می‌کند."
    return None


def suggest_ports(db: Session, ports: tuple[int, ...], n: int = 4) -> list[int]:
    return [p for p in ports if port_problem(db, p, udp=True) is None][:n]


# ------------------------------------------------------------------ page
@router.get("/xray")
def xray_page(request: Request, admin: AdminUser | None = Depends(get_optional_admin)):
    if not admin:
        return RedirectResponse(url="/login", status_code=302)
    return templates.TemplateResponse("xray.html", {"request": request, "admin": admin, "tcp_port": connection.main_port("tcp")})


@router.get("/api/xray")
def xray_status(admin: AdminUser | None = Depends(get_optional_admin), db: Session = Depends(get_db)):
    _admin(admin)
    inbounds = db.query(XrayInbound).order_by(XrayInbound.position, XrayInbound.id).all()
    online = core.online_sessions()
    listening = connection.listening_ports("tcp") if inbounds else set()
    xray_users = [u for u in db.query(VpnUser).all() if u.xray_enabled]
    active = [u for u in xray_users if u.is_usable()]
    return {
        "state": core.service_state(),
        "version": core.version() if core.installed() else "",
        "inbounds": [_serialize(ib, listening, active) for ib in inbounds],
        "users": len(xray_users),
        "users_active": len(active),
        "online": len(online),
        "online_bytes": sum(s["bytes"] for s in online),
        "presets": presets.PRESETS,
        "allowed": presets.ALLOWED,
        "reality_targets": presets.REALITY_TARGETS,
        "ss_methods": presets.SS_METHODS,
        "suggest_ports": {"tls": suggest_ports(db, TLS_PORTS), "http": suggest_ports(db, HTTP_PORTS)},
        "server_address": settings.SERVER_ADDRESS,
        # relays that sync (and so carry Xray)
        "relays": sum(1 for r in db.query(RelayServer).filter(RelayServer.enabled.is_(True)) if r.synced_at),
    }


def inbound_choices(db: Session) -> list[dict]:
    """What a user can be limited to (users page: create form, drawer)."""
    return [
        {"id": ib.id, "name": ib.name, "label": links.label(ib), "protocol": ib.protocol, "enabled": ib.enabled}
        for ib in db.query(XrayInbound).order_by(XrayInbound.position, XrayInbound.id)
    ]


@router.get("/api/xray/inbounds")
def list_inbounds(admin: AdminUser | None = Depends(get_optional_admin), db: Session = Depends(get_db)):
    _admin(admin)
    return {"inbounds": inbound_choices(db), "installed": core.installed()}


def _apply(db: Session, force: bool = False) -> str:
    try:
        return core.apply(db, force=force)
    except core.XrayError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _fill_secrets(ib_protocol: str, security: str, opts: dict, old: dict | None = None) -> dict:
    old = old or {}
    if security == "reality":
        if old.get("private_key") and old.get("public_key"):
            opts["private_key"], opts["public_key"] = old["private_key"], old["public_key"]
        else:
            try:
                opts["private_key"], opts["public_key"] = core.x25519()
            except core.XrayError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
        if not opts.get("short_id"):
            opts["short_id"] = old.get("short_id") or secrets.token_hex(4)
    if ib_protocol == "shadowsocks":
        if old.get("server_key") and old.get("method") == opts["method"]:
            opts["server_key"] = old["server_key"]
        else:
            opts["server_key"] = core.ss_server_key(opts["method"])
    return opts


@router.post("/api/xray/inbounds")
def create_inbound(payload: InboundIn, admin: AdminUser | None = Depends(get_optional_admin), db: Session = Depends(get_db)):
    _admin(admin)
    try:
        presets.check_combo(payload.protocol, payload.transport, payload.security)
        opts = presets.clean_options(payload.protocol, payload.transport, payload.security, payload.options)
    except presets.InboundError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    udp = payload.protocol == "shadowsocks"
    problem = port_problem(db, payload.port, udp)
    if problem:
        raise HTTPException(status_code=400, detail=problem)
    opts = _fill_secrets(payload.protocol, payload.security, opts)
    last = db.query(XrayInbound).order_by(XrayInbound.position.desc()).first()
    ib = XrayInbound(
        name=payload.name.strip(), protocol=payload.protocol, transport=payload.transport, security=payload.security,
        port=payload.port, options=json.dumps(opts), enabled=payload.enabled, position=(last.position + 1) if last else 0,
    )
    db.add(ib)
    db.commit()
    db.refresh(ib)
    try:
        state = core.apply(db)
    except core.XrayError as exc:
        db.delete(ib)
        db.commit()
        raise HTTPException(status_code=400, detail=f"Xray این تنظیم را نپذیرفت: {exc}") from exc
    if ib.enabled:
        core.open_port(ib.port, udp=udp)
    return {**_serialize(ib), "applied": state}


def _get(db: Session, inbound_id: int) -> XrayInbound:
    ib = db.get(XrayInbound, inbound_id)
    if ib is None:
        raise HTTPException(status_code=404, detail="not found")
    return ib


@router.patch("/api/xray/inbounds/{inbound_id}")
def update_inbound(inbound_id: int, payload: InboundPatch, admin: AdminUser | None = Depends(get_optional_admin), db: Session = Depends(get_db)):
    _admin(admin)
    ib = _get(db, inbound_id)
    udp = ib.protocol == "shadowsocks"
    before = {"name": ib.name, "port": ib.port, "options": ib.options, "enabled": ib.enabled}
    if payload.port is not None and payload.port != ib.port:
        problem = port_problem(db, payload.port, udp, own_id=ib.id)
        if problem:
            raise HTTPException(status_code=400, detail=problem)
        ib.port = payload.port
    if payload.name is not None:
        ib.name = payload.name.strip()
    if payload.options is not None:
        old = core.options(ib)
        try:
            opts = presets.clean_options(ib.protocol, ib.transport, ib.security, {**old, **payload.options})
        except presets.InboundError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        ib.options = json.dumps(_fill_secrets(ib.protocol, ib.security, opts, old))
    if payload.enabled is not None:
        ib.enabled = payload.enabled
    db.commit()
    try:
        state = core.apply(db)
    except core.XrayError as exc:
        for k, v in before.items():
            setattr(ib, k, v)
        db.commit()
        raise HTTPException(status_code=400, detail=f"Xray این تنظیم را نپذیرفت: {exc}") from exc
    if before["port"] != ib.port or before["enabled"] != ib.enabled:
        if before["enabled"]:
            core.close_port(before["port"], udp=udp)
        if ib.enabled:
            core.open_port(ib.port, udp=udp)
    return {**_serialize(ib), "applied": state}


@router.delete("/api/xray/inbounds/{inbound_id}")
def delete_inbound(inbound_id: int, admin: AdminUser | None = Depends(get_optional_admin), db: Session = Depends(get_db)):
    _admin(admin)
    ib = _get(db, inbound_id)
    port, udp, enabled = ib.port, ib.protocol == "shadowsocks", ib.enabled
    db.delete(ib)
    db.commit()
    state = _apply(db)
    if enabled:
        core.close_port(port, udp=udp)
    return {"ok": True, "applied": state}


@router.post("/api/xray/inbounds/order")
def order_inbounds(payload: Order, admin: AdminUser | None = Depends(get_optional_admin), db: Session = Depends(get_db)):
    _admin(admin)
    for pos, inbound_id in enumerate(payload.ids):
        ib = db.get(XrayInbound, inbound_id)
        if ib is not None:
            ib.position = pos
    db.commit()
    return {"ok": True, "applied": _apply(db)}


@router.post("/api/xray/restart")
def restart_xray(admin: AdminUser | None = Depends(get_optional_admin), db: Session = Depends(get_db)):
    _admin(admin)
    return {"applied": _apply(db, force=True), "state": core.service_state()}


@router.post("/api/xray/check-target")
def check_target(payload: Target, admin: AdminUser | None = Depends(get_optional_admin)):
    """Can this server reach the site a REALITY inbound impersonates, over
    TLS 1.3 (what REALITY needs)?"""
    _admin(admin)
    sni = payload.sni.strip().lower()
    if not presets._HOST_RE.match(sni):
        raise HTTPException(status_code=400, detail="دامنه نامعتبر است.")
    ctx = ssl.create_default_context()
    ctx.minimum_version = ssl.TLSVersion.TLSv1_3
    ctx.set_alpn_protocols(["h2", "http/1.1"])
    started = time.monotonic()
    try:
        with socket.create_connection((sni, 443), timeout=6) as raw, ctx.wrap_socket(raw, server_hostname=sni) as tls:
            return {"ok": True, "ms": round((time.monotonic() - started) * 1000), "tls": tls.version(), "h2": tls.selected_alpn_protocol() == "h2"}
    except ssl.SSLError as exc:
        return {"ok": False, "error": "no_tls13" if "VERSION" in str(exc).upper() else "tls", "detail": str(exc)[:160]}
    except OSError as exc:
        return {"ok": False, "error": "unreachable", "detail": str(exc)[:160]}
