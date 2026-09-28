"""Endpoints called only from the local OpenVPN hook scripts (127.0.0.1,
shared-secret header). Never exposed publicly -- main.py should make sure
Uvicorn only binds where Nginx/the firewall keep this reachable from
localhost, and the shared token keeps other local processes from being able
to spoof hook calls."""
import datetime
import hmac
import logging
import threading
import time
from collections import deque

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models import VpnUser, find_vpn_user
from app.openvpn import tlscrypt
from app.openvpn.scheduler import enforce_device_limit_soon, finalize_disconnect
from app.relays import relay_names_by_ip

router = APIRouter(prefix="/internal")
logger = logging.getLogger("waze_panel.auth")

_LOOPBACK = {"127.0.0.1", "::1"}

# Shown by the VPN app next to AUTH_FAILED. Plain ASCII: clients don't all
# render Persian, and password failures stay deliberately vague.
_REASONS = {
    "disabled": "Account disabled",
    "expired": "Account expired - renew it from your subscription link",
    "over_quota": "Data limit reached - renew it from your subscription link",
    "revoked": "Account removed",
    "unknown_user": "Unknown account",
    "cert_required": "This account needs its own config file (download it from your subscription link)",
    "password_required": "This account now needs a username and password - download the new config from your subscription link",
    "bad_credentials": "Wrong username or password",
    "throttled": "Too many failed attempts - try again in a few minutes",
}


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


def _deny(reason: str) -> dict:
    return {"allow": False, "reason": reason, "message": _REASONS.get(reason, "Access denied")}


# ---------------------------------------------------------------- throttling
# Failed password logins, remembered per (username, ip) and per ip. Guessing
# needs the tls-crypt key first (i.e. a copy of some config), so this is
# about stopping one customer from brute-forcing another's password.
_FAIL_WINDOW = 600
_MAX_FAILS_PER_LOGIN = 8
_MAX_FAILS_PER_IP = 30
_fail_lock = threading.Lock()
_fails: dict[str, deque] = {}


def _recent(key: str, now: float) -> deque:
    q = _fails.setdefault(key, deque())
    while q and now - q[0] > _FAIL_WINDOW:
        q.popleft()
    return q


def _shared_ip(ip: str) -> bool:
    """Everyone behind a relay arrives from the relay's IP: an IP-wide limit
    there would lock all of them out over a few typos."""
    return ip in relay_names_by_ip(refresh=False)


def _throttled(login: str, ip: str) -> bool:
    now = time.time()
    with _fail_lock:
        if len(_recent(f"u:{login}|{ip}", now)) >= _MAX_FAILS_PER_LOGIN:
            return True
        return not _shared_ip(ip) and len(_recent(f"ip:{ip}", now)) >= _MAX_FAILS_PER_IP


def _note_failure(login: str, ip: str) -> None:
    now = time.time()
    with _fail_lock:
        _recent(f"u:{login}|{ip}", now).append(now)
        if not _shared_ip(ip):
            _recent(f"ip:{ip}", now).append(now)
        if len(_fails) > 5000:  # forget idle entries
            for key in [k for k, q in _fails.items() if not q or now - q[-1] > _FAIL_WINDOW]:
                _fails.pop(key, None)


def _password_ok(user: VpnUser, password: str) -> bool:
    if not user.auth_password or not password:
        return False
    return hmac.compare_digest(user.auth_password.encode(), password.encode())


# --------------------------------------------------------------------- hooks
class AuthPayload(BaseModel):
    proto: str
    # CN of the client certificate, empty when the client presented none
    common_name: str = ""
    username: str = ""
    password: str = ""
    ip: str = ""


class ConnectPayload(BaseModel):
    proto: str
    common_name: str = ""
    username: str = ""
    # "ip:port" + start time identify the session (see scheduler.py)
    real_address: str = ""
    start_t: int = 0


class TlsCryptPayload(BaseModel):
    # what the panel sealed into the key: "u:<username>:<key id>" / "s:<key id>"
    metadata: str = ""
    ip: str = ""


class DisconnectPayload(ConnectPayload):
    bytes_sent: int = 0
    bytes_received: int = 0


def _authenticate(db: Session, p: AuthPayload) -> tuple[VpnUser | None, str | None]:
    """(user, None) on success, (user-or-None, reason) on failure."""
    if p.common_name:
        user = find_vpn_user(db, p.common_name)
        if user is None:
            return None, "unknown_user"
        if user.auth_mode == "cert":
            return user, None  # any username/password sent along is ignored
        # cert_pass -- and a password-only user who happens to present
        # their certificate -- must also log in, as themselves.
        if not p.username:
            return user, "password_required"
        if p.username.lower() != user.username.lower() or not _password_ok(user, p.password):
            return user, "bad_credentials"
        return user, None

    # No certificate: only password-only accounts may log in like this.
    if not p.username:
        return None, "cert_required"
    user = find_vpn_user(db, p.username)
    if user is None or not _password_ok(user, p.password):
        return user, "bad_credentials"
    if user.auth_mode != "pass":
        # right password, but this account also needs its certificate
        return user, "cert_required"
    return user, None


@router.post("/hooks/auth")
def hook_auth(
    payload: AuthPayload,
    request: Request,
    x_internal_token: str | None = Header(default=None),
    db: Session = Depends(get_db),
):
    _check_caller(request, x_internal_token)

    login = (payload.username or payload.common_name).lower()
    if _throttled(login, payload.ip):
        logger.warning("auth throttled: login=%r ip=%s", login, payload.ip)
        return _deny("throttled")

    user, reason = _authenticate(db, payload)
    if reason:
        if reason == "bad_credentials":
            _note_failure(login, payload.ip)
        logger.info("auth failed (%s): cn=%r login=%r ip=%s", reason, payload.common_name, payload.username, payload.ip)
        return _deny(reason)
    if not user.is_usable():
        return _deny(user.status_label())
    return {"allow": True}


@router.post("/hooks/connect")
def hook_connect(
    payload: ConnectPayload,
    request: Request,
    x_internal_token: str | None = Header(default=None),
    db: Session = Depends(get_db),
):
    _check_caller(request, x_internal_token)

    user = find_vpn_user(db, payload.common_name or payload.username)
    if user is None:
        return _deny("unknown_user")
    # The auth hook already enforced this; checked again so a server config
    # without it can never let a certificate-less client in.
    if not payload.common_name and user.auth_mode != "pass":
        return _deny("cert_required")
    if not user.is_usable():
        return _deny(user.status_label())

    if user.max_devices:
        enforce_device_limit_soon(user.username, user.max_devices)
    return {"allow": True}


@router.post("/hooks/disconnect")
def hook_disconnect(
    payload: DisconnectPayload,
    request: Request,
    x_internal_token: str | None = Header(default=None),
):
    _check_caller(request, x_internal_token)
    finalize_disconnect(
        payload.proto,
        payload.common_name or payload.username,
        payload.bytes_received,
        payload.bytes_sent,
        real_address=payload.real_address,
        start_t=payload.start_t,
    )
    return {"ok": True}


@router.post("/hooks/tlscrypt")
def hook_tlscrypt(
    payload: TlsCryptPayload,
    request: Request,
    x_internal_token: str | None = Header(default=None),
    db: Session = Depends(get_db),
):
    """Before the TLS handshake of a client with a personal key: is the key
    still the current one of an existing user?"""
    _check_caller(request, x_internal_token)
    ok, user = tlscrypt.check_metadata(db, payload.metadata[:256])
    if not ok:
        logger.info("tls-crypt-v2 key refused: %r ip=%s", payload.metadata[:80], payload.ip)
        return {"allow": False}
    if user is not None:
        now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
        if user.tls_key_seen_at is None or (now - user.tls_key_seen_at).total_seconds() > 60:
            user.tls_key_seen_at = now
            db.commit()
    return {"allow": True}
