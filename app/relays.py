"""Relay servers: client configs get the relays as extra `remote` lines so
users connect through a server inside the country (which forwards to this
one), falling back to the direct address.

Options live in the key/value settings table:
  RELAY_FALLBACK_DIRECT  "1": this server's own address is the last remote
  RELAY_BALANCE          "1": spread users over the relays (each user gets
                         the relays in a rotated order, stable per user)
  RELAY_TIMEOUT          seconds a client waits for one address before
                         trying the next (OpenVPN server-poll-timeout)
"""
import hashlib
import socket
import threading
import time

from sqlalchemy.orm import Session

from app.config import settings
from app.database import SessionLocal
from app.models import RelayServer, Setting

RAW_BASE = "https://raw.githubusercontent.com/Metixpro/waze-panel/main"

_DEFAULTS = {"RELAY_FALLBACK_DIRECT": "1", "RELAY_BALANCE": "0", "RELAY_TIMEOUT": "8"}


def get_options(db: Session) -> dict:
    rows = {r.key: r.value for r in db.query(Setting).filter(Setting.key.in_(_DEFAULTS)).all()}
    values = {**_DEFAULTS, **rows}
    try:
        timeout = max(3, min(60, int(values["RELAY_TIMEOUT"])))
    except ValueError:
        timeout = int(_DEFAULTS["RELAY_TIMEOUT"])
    return {
        "fallback_direct": values["RELAY_FALLBACK_DIRECT"] == "1",
        "balance": values["RELAY_BALANCE"] == "1",
        "timeout": timeout,
    }


def set_options(db: Session, fallback_direct: bool, balance: bool, timeout: int) -> None:
    new = {
        "RELAY_FALLBACK_DIRECT": "1" if fallback_direct else "0",
        "RELAY_BALANCE": "1" if balance else "0",
        "RELAY_TIMEOUT": str(max(3, min(60, int(timeout)))),
    }
    for key, value in new.items():
        row = db.get(Setting, key)
        if row is None:
            db.add(Setting(key=key, value=value))
        else:
            row.value = value
    db.commit()


def ordered_relays(db: Session, enabled_only: bool = False) -> list[RelayServer]:
    q = db.query(RelayServer)
    if enabled_only:
        q = q.filter(RelayServer.enabled.is_(True))
    return q.order_by(RelayServer.position, RelayServer.id).all()


def client_remotes(proto: str, key: str = "") -> tuple[list[tuple[str, int]], int]:
    """(address, port) pairs in the order a client should try them, and the
    per-address timeout. `key` (the username) makes balancing stable."""
    db = SessionLocal()
    try:
        relays = ordered_relays(db, enabled_only=True)
        opts = get_options(db)
    finally:
        db.close()

    direct_port = settings.OVPN_UDP_PORT if proto == "udp" else settings.OVPN_TCP_PORT
    direct = (settings.SERVER_ADDRESS, int(direct_port))
    hops = [(r.address, r.udp_port if proto == "udp" else r.tcp_port) for r in relays]
    if not hops:
        return [direct], opts["timeout"]
    if opts["balance"] and key and len(hops) > 1:
        k = int(hashlib.sha256(key.encode()).hexdigest(), 16) % len(hops)
        hops = hops[k:] + hops[:k]
    if opts["fallback_direct"]:
        hops.append(direct)
    return hops, opts["timeout"]


def install_command(relay: RelayServer) -> str:
    """What to paste on the relay server."""
    return (
        f"bash <(curl -Ls {RAW_BASE}/relay.sh) --to {settings.SERVER_ADDRESS} "
        f"--udp {relay.udp_port}:{settings.OVPN_UDP_PORT} --tcp {relay.tcp_port}:{settings.OVPN_TCP_PORT}"
    )


def check(relay: RelayServer, timeout: float = 4.0) -> dict:
    """Open a TCP connection to the relay's TCP port. A working relay
    forwards it right back to this server's OpenVPN TCP port, so success
    means the whole path (relay up, forwarding rules, route back) works."""
    started = time.monotonic()
    try:
        with socket.create_connection((relay.address, relay.tcp_port), timeout=timeout):
            pass
    except socket.timeout:
        return {"ok": False, "error": "timeout"}
    except OSError as exc:
        return {"ok": False, "error": exc.strerror or str(exc)}
    return {"ok": True, "ms": round((time.monotonic() - started) * 1000)}


# ------------------------------------------------- relay IP -> name labels
_label_lock = threading.Lock()
_labels: dict[str, str] = {}
_labels_at = 0.0


def relay_names_by_ip() -> dict[str, str]:
    """Clients behind a relay show up with the relay's IP; this maps those
    IPs back to relay names for the dashboard (refreshed every 5 minutes,
    resolving relay hostnames)."""
    global _labels, _labels_at
    with _label_lock:
        if time.monotonic() - _labels_at < 300:
            return _labels
    db = SessionLocal()
    try:
        relays = [(r.address, r.name) for r in ordered_relays(db)]
    finally:
        db.close()
    labels: dict[str, str] = {}
    for address, name in relays:
        labels[address] = name
        try:
            for info in socket.getaddrinfo(address, None, socket.AF_INET):
                labels[info[4][0]] = name
        except OSError:
            pass
    with _label_lock:
        _labels, _labels_at = labels, time.monotonic()
    return labels


def forget_labels() -> None:
    global _labels_at
    with _label_lock:
        _labels_at = 0.0
