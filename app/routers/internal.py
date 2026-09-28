"""Endpoints called only from the local client-connect / client-disconnect
hook scripts (127.0.0.1, shared-secret header). Never exposed publicly --
main.py should make sure Uvicorn only binds where Nginx/the firewall keep
this reachable from localhost, and the shared token keeps other local
processes from being able to spoof hook calls."""
import hmac

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models import VpnUser
from app.openvpn.scheduler import finalize_disconnect

router = APIRouter(prefix="/internal")

_LOOPBACK = {"127.0.0.1", "::1"}


def _check_caller(request: Request, x_internal_token: str | None) -> None:
    # Hooks talk to us directly over loopback. Anything that came through a
    # reverse proxy (Nginx also connects from 127.0.0.1) carries forwarding
    # headers, so those are rejected too.
    host = request.client.host if request.client else ""
    proxied = "x-forwarded-for" in request.headers or "x-real-ip" in request.headers
    if host not in _LOOPBACK or proxied:
        raise HTTPException(status_code=404, detail="Not Found")
    if not x_internal_token or not hmac.compare_digest(x_internal_token, settings.INTERNAL_TOKEN):
        raise HTTPException(status_code=403, detail="forbidden")


class ConnectPayload(BaseModel):
    common_name: str
    proto: str


class DisconnectPayload(BaseModel):
    common_name: str
    proto: str
    bytes_sent: int = 0
    bytes_received: int = 0


@router.post("/hooks/connect")
def hook_connect(
    payload: ConnectPayload,
    request: Request,
    x_internal_token: str | None = Header(default=None),
    db: Session = Depends(get_db),
):
    _check_caller(request, x_internal_token)

    user = db.query(VpnUser).filter(VpnUser.username == payload.common_name).first()
    if user is None:
        return {"allow": False, "reason": "unknown_user"}
    if not user.is_usable():
        if user.revoked:
            reason = "revoked"
        elif not user.enabled:
            reason = "disabled"
        elif user.is_expired():
            reason = "expired"
        else:
            reason = "over_quota"
        return {"allow": False, "reason": reason}

    return {"allow": True}


@router.post("/hooks/disconnect")
def hook_disconnect(
    payload: DisconnectPayload,
    request: Request,
    x_internal_token: str | None = Header(default=None),
):
    _check_caller(request, x_internal_token)
    finalize_disconnect(
        payload.proto, payload.common_name, payload.bytes_received, payload.bytes_sent
    )
    return {"ok": True}
