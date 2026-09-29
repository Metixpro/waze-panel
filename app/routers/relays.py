"""Relay servers (Settings page): CRUD, ordering, options and health checks,
and the endpoints relays sync from. See app/relays.py for how they end up
in client configs and Xray links."""
import datetime
import re

from fastapi import APIRouter, Depends, Form, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.config import settings
from app.connection import extra_ports
from app.database import get_db
from app.deps import get_optional_admin
from app.models import AdminUser, RelayServer
from app import relays as relay_lib

router = APIRouter(prefix="/api/relays")
# no login: a relay proves itself with its token
sync_router = APIRouter()

_ADDRESS_RE = re.compile(r"^[A-Za-z0-9.\-:\[\]]{1,253}$")


class RelayIn(BaseModel):
    name: str = Field(min_length=1, max_length=40)
    address: str
    udp_port: int | None = Field(default=None, ge=1, le=65535)
    tcp_port: int | None = Field(default=None, ge=1, le=65535)


class RelayPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=40)
    address: str | None = None
    udp_port: int | None = Field(default=None, ge=1, le=65535)
    tcp_port: int | None = Field(default=None, ge=1, le=65535)
    enabled: bool | None = None


class RelayOrder(BaseModel):
    ids: list[int]


class RelayOptions(BaseModel):
    fallback_direct: bool = True
    balance: bool = False
    timeout: int = Field(default=8, ge=3, le=60)


def _require_admin(admin: AdminUser | None) -> None:
    if not admin:
        raise HTTPException(status_code=401, detail="unauthorized")


def _clean_address(address: str) -> str:
    address = address.strip()
    if not _ADDRESS_RE.match(address):
        raise HTTPException(status_code=400, detail="آدرس سرور واسط باید یک IP یا دامنه معتبر باشد.")
    if address.lower() == (settings.SERVER_ADDRESS or "").lower():
        raise HTTPException(
            status_code=400,
            detail="این همان آدرس همین سرور است؛ آدرس سرور ایران (واسط) را وارد کنید. "
            "«آدرس سرور» در بخش بالا باید آدرس همین سرور خارج بماند.",
        )
    return address


def _iso(dt: datetime.datetime | None) -> str | None:
    return dt.replace(tzinfo=datetime.timezone.utc).isoformat() if dt else None


def _serialize(r: RelayServer, xray_ports: list[int] | None = None) -> dict:
    fw = relay_lib.forwarded(r)
    return {
        "sync_url": relay_lib.sync_url(r) if r.sync_token else "",
        "synced_at": _iso(r.synced_at),
        "forwarded": fw,
        # Xray inbound ports this relay should carry but doesn't (yet)
        "xray_missing": [p for p in (xray_ports or []) if p not in fw["tcp"]] if r.synced_at else [],
        "id": r.id,
        "name": r.name,
        "address": r.address,
        "udp_port": r.udp_port,
        "tcp_port": r.tcp_port,
        "enabled": r.enabled,
        "command": relay_lib.install_commands(r)[0],
        "command_alt": relay_lib.install_commands(r)[1],
    }


def _get(db: Session, relay_id: int) -> RelayServer:
    relay = db.get(RelayServer, relay_id)
    if relay is None:
        raise HTTPException(status_code=404, detail="not found")
    return relay


@router.get("")
def list_relays(admin: AdminUser | None = Depends(get_optional_admin), db: Session = Depends(get_db)):
    _require_admin(admin)
    relays = relay_lib.ordered_relays(db)
    if any([relay_lib.ensure_token(r) for r in relays]):
        db.commit()
    xray_ports = [ib.port for ib in relay_lib.relayed_inbounds(db)]
    return {
        "relays": [_serialize(r, xray_ports) for r in relays],
        "xray_ports": xray_ports,
        "options": relay_lib.get_options(db),
        "health": {str(k): v for k, v in relay_lib.get_health().items()},
        "direct": {
            "address": settings.SERVER_ADDRESS,
            "udp_port": settings.OVPN_UDP_PORT,
            "tcp_port": settings.OVPN_TCP_PORT,
            "udp_extra": extra_ports(db, "udp"),
            "tcp_extra": extra_ports(db, "tcp"),
        },
    }


@router.post("")
def create_relay(payload: RelayIn, admin: AdminUser | None = Depends(get_optional_admin), db: Session = Depends(get_db)):
    _require_admin(admin)
    last = relay_lib.ordered_relays(db)
    relay = RelayServer(
        name=payload.name.strip(),
        address=_clean_address(payload.address),
        udp_port=payload.udp_port or int(settings.OVPN_UDP_PORT),
        tcp_port=payload.tcp_port or int(settings.OVPN_TCP_PORT),
        position=(last[-1].position + 1) if last else 0,
    )
    relay_lib.ensure_token(relay)
    db.add(relay)
    db.commit()
    db.refresh(relay)
    relay_lib.forget_labels()
    return _serialize(relay)


@router.patch("/{relay_id}")
def update_relay(
    relay_id: int,
    payload: RelayPatch,
    admin: AdminUser | None = Depends(get_optional_admin),
    db: Session = Depends(get_db),
):
    _require_admin(admin)
    relay = _get(db, relay_id)
    if payload.name is not None:
        relay.name = payload.name.strip()
    if payload.address is not None:
        relay.address = _clean_address(payload.address)
    if payload.udp_port is not None:
        relay.udp_port = payload.udp_port
    if payload.tcp_port is not None:
        relay.tcp_port = payload.tcp_port
    if payload.enabled is not None:
        relay.enabled = payload.enabled
    db.commit()
    db.refresh(relay)
    relay_lib.forget_labels()
    return _serialize(relay)


@router.delete("/{relay_id}")
def delete_relay(relay_id: int, admin: AdminUser | None = Depends(get_optional_admin), db: Session = Depends(get_db)):
    _require_admin(admin)
    db.delete(_get(db, relay_id))
    db.commit()
    relay_lib.forget_labels()
    relay_lib.forget_health(relay_id)
    return {"ok": True}


@router.post("/order")
def reorder_relays(payload: RelayOrder, admin: AdminUser | None = Depends(get_optional_admin), db: Session = Depends(get_db)):
    _require_admin(admin)
    by_id = {r.id: r for r in relay_lib.ordered_relays(db)}
    if sorted(payload.ids) != sorted(by_id):
        raise HTTPException(status_code=400, detail="relay list changed; reload the page")
    for position, relay_id in enumerate(payload.ids):
        by_id[relay_id].position = position
    db.commit()
    return {"ok": True}


@router.post("/options")
def save_options(payload: RelayOptions, admin: AdminUser | None = Depends(get_optional_admin), db: Session = Depends(get_db)):
    _require_admin(admin)
    relay_lib.set_options(db, payload.fallback_direct, payload.balance, payload.timeout)
    return relay_lib.get_options(db)


@router.post("/check")
def check_relays(admin: AdminUser | None = Depends(get_optional_admin)):
    """Round trip through every relay now (in parallel)."""
    _require_admin(admin)
    return {str(k): v for k, v in relay_lib.run_checks().items()}


# ---------------------------------------------------------------- relay sync
_TOKEN_RE = re.compile(r"^[0-9a-f]{32}$")


def _by_token(db: Session, token: str) -> RelayServer:
    relay = db.query(RelayServer).filter(RelayServer.sync_token == token).first() if _TOKEN_RE.match(token) else None
    if relay is None:
        raise HTTPException(status_code=404, detail="Not Found")
    return relay


@sync_router.get("/relay-sync/{token}", include_in_schema=False)
def relay_sync(token: str, db: Session = Depends(get_db)):
    """What this relay should forward (`waze-relay sync`, every minute)."""
    return PlainTextResponse(relay_lib.sync_config(db, _by_token(db, token)))


@sync_router.post("/relay-sync/{token}/report", include_in_schema=False)
def relay_report(
    token: str,
    tcp: str = Form(default="", max_length=4000),
    udp: str = Form(default="", max_length=4000),
    skipped: str = Form(default="", max_length=1000),
    db: Session = Depends(get_db),
):
    """What it forwards now, after applying the last sync."""
    relay_lib.record_report(db, _by_token(db, token), tcp, udp, skipped)
    return PlainTextResponse("ok\n")
