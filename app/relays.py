"""Relay servers: client configs get the relays as extra `remote` lines so
users connect through a server inside the country (which forwards to this
one), falling back to the direct address. Xray users get a link through
each relay as well.

Relays installed with --sync fetch what to forward from the panel every
minute (sync_config): the OpenVPN ports and every Xray inbound, so a new
inbound works through the relays within a minute, no commands to re-run.
They report back what they really forward (record_report); Xray links only
go through a relay for the ports it reported.

Options live in the key/value settings table:
  RELAY_FALLBACK_DIRECT  "1": this server's own address is the last remote
  RELAY_BALANCE          "1": spread users over the relays (each user gets
                         the relays in a rotated order, stable per user)
  RELAY_TIMEOUT          seconds a client waits for one address before
                         trying the next (OpenVPN server-poll-timeout)
"""
import datetime
import hashlib
import json
import secrets
import socket
import threading
import time

from sqlalchemy.orm import Session

from app.config import settings
from app.connection import extra_ports, main_port
from app.database import SessionLocal
from app.models import RelayServer, Setting, XrayInbound

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
        extras = extra_ports(db, proto)
    finally:
        db.close()

    # this server: its real port, then the extra ones (Settings > ports)
    direct = [(settings.SERVER_ADDRESS, p) for p in [main_port(proto), *extras]]
    hops = [(r.address, r.udp_port if proto == "udp" else r.tcp_port) for r in relays]
    if not hops:
        return direct, opts["timeout"]
    if opts["balance"] and key and len(hops) > 1:
        k = int(hashlib.sha256(key.encode()).hexdigest(), 16) % len(hops)
        hops = hops[k:] + hops[:k]
    if opts["fallback_direct"]:
        hops.extend(direct)
    return hops, opts["timeout"]


# ------------------------------------------------------------------ sync
def ensure_token(relay: RelayServer) -> bool:
    """Give the relay its sync token (secret: it names the relay). True if made."""
    if relay.sync_token:
        return False
    relay.sync_token = secrets.token_hex(16)
    return True


def sync_url(relay: RelayServer) -> str:
    return f"{settings.public_base_url}/relay-sync/{relay.sync_token}"


def relayed_inbounds(db: Session) -> list[XrayInbound]:
    """Enabled Xray inbounds that go through relays: all of them, except
    ones with their own address in the links (a CDN domain, say) or with
    relays switched off in their options."""
    out = []
    for ib in db.query(XrayInbound).filter(XrayInbound.enabled.is_(True)).order_by(XrayInbound.position, XrayInbound.id):
        try:
            opts = json.loads(ib.options or "{}")
        except ValueError:
            opts = {}
        if opts.get("link_address") or opts.get("relay") is False:
            continue
        out.append(ib)
    return out


def forward_maps(db: Session, relay: RelayServer) -> tuple[list[str], list[str]]:
    """(udp, tcp) "listen:target" pairs the relay should forward: OpenVPN
    on the relay's own ports, Xray inbounds on the same port as here."""
    udp = [f"{relay.udp_port}:{settings.OVPN_UDP_PORT}"]
    tcp = [f"{relay.tcp_port}:{settings.OVPN_TCP_PORT}"]
    for ib in relayed_inbounds(db):
        if ib.port != relay.tcp_port:
            tcp.append(f"{ib.port}:{ib.port}")
        if ib.protocol == "shadowsocks" and ib.port != relay.udp_port:
            udp.append(f"{ib.port}:{ib.port}")
    return udp, tcp


def sync_config(db: Session, relay: RelayServer) -> str:
    """What `waze-relay sync` reads: plain KEY=value lines."""
    udp, tcp = forward_maps(db, relay)
    return f"TO={settings.SERVER_ADDRESS}\nUDP_MAP={' '.join(udp)}\nTCP_MAP={' '.join(tcp)}\n"


def _ports(text: str) -> list[int]:
    return sorted({int(p) for p in text.replace(",", " ").split() if p.isdigit() and 0 < int(p) < 65536})


def record_report(db: Session, relay: RelayServer, tcp: str, udp: str, skipped: str) -> None:
    """The relay's word on what it forwards now (listen ports)."""
    relay.forwarded = json.dumps({
        "tcp": _ports(tcp), "udp": _ports(udp),
        "skipped": [s for s in skipped.split() if len(s) < 16][:64],
    })
    relay.synced_at = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
    db.commit()


def forwarded(relay: RelayServer) -> dict:
    try:
        data = json.loads(relay.forwarded or "{}")
    except ValueError:
        data = {}
    return {"tcp": data.get("tcp", []), "udp": data.get("udp", []), "skipped": data.get("skipped", [])}


def xray_relays(db: Session, key: str = "") -> list[tuple[RelayServer, set[int]]]:
    """Enabled relays that sync, with the TCP ports they forward, in the
    order users should try them (rotated per user when balancing)."""
    opts = get_options(db)
    hops = [(r, set(forwarded(r)["tcp"])) for r in ordered_relays(db, enabled_only=True) if r.synced_at]
    if opts["balance"] and key and len(hops) > 1:
        k = int(hashlib.sha256(key.encode()).hexdigest(), 16) % len(hops)
        hops = hops[k:] + hops[:k]
    return hops


def install_commands(relay: RelayServer) -> tuple[str, str]:
    """(main, alternative) command to paste on the relay server: from
    GitHub over HTTPS, or straight from this panel (the relay has to reach
    this server anyway). The panel copy comes first only over HTTPS, since
    the script runs as root. The ports are given too, so the relay forwards
    OpenVPN even while it can't reach the panel; --sync then keeps it
    current."""
    args = (
        f"--to {settings.SERVER_ADDRESS} "
        f"--udp {relay.udp_port}:{settings.OVPN_UDP_PORT} --tcp {relay.tcp_port}:{settings.OVPN_TCP_PORT}"
    )
    if relay.sync_token:
        args += f" --sync {sync_url(relay)}"
    github = f"bash <(curl -Ls {RAW_BASE}/relay.sh) {args}"
    base = settings.public_base_url
    panel = f"bash <(curl -Ls {base}/relay.sh) {args}"
    return (panel, github) if base.startswith("https://") else (github, panel)


def _hex_ipv4(h: str) -> str:
    try:
        return socket.inet_ntoa(bytes.fromhex(h)[::-1])
    except (ValueError, OSError):
        return ""


def _looped_back(server_port: int, peer_port: int) -> str | None:
    """If there is an established connection to our OpenVPN TCP port coming
    from `peer_port` (the relay masquerades but keeps the source port),
    return the address it came from ("" if not IPv4); else None."""
    for table in ("/proc/net/tcp", "/proc/net/tcp6"):
        try:
            with open(table) as f:
                next(f, None)
                for line in f:
                    parts = line.split()
                    if len(parts) < 4 or parts[3] != "01":  # 01 = ESTABLISHED
                        continue
                    local_port = int(parts[1].rsplit(":", 1)[1], 16)
                    remote_hex, remote_port = parts[2].rsplit(":", 1)
                    if local_port == server_port and int(remote_port, 16) == peer_port:
                        return _hex_ipv4(remote_hex) if table.endswith("tcp") else ""
        except OSError:
            continue
    return None


def check(relay: RelayServer, timeout: float = 4.0) -> dict:
    """Connect to the relay's TCP port and confirm the connection comes back
    to this server's OpenVPN TCP port -- i.e. the whole path works (relay
    up, forwarding rules in place, route back), not just "a port is open"."""
    started = time.monotonic()
    try:
        sock = socket.create_connection((relay.address, relay.tcp_port), timeout=timeout)
    except socket.timeout:
        return {"ok": False, "error": "timeout"}
    except ConnectionRefusedError:
        return {"ok": False, "error": "refused"}
    except OSError as exc:
        return {"ok": False, "error": exc.strerror or str(exc)}
    ms = round((time.monotonic() - started) * 1000)
    try:
        peer_port = sock.getsockname()[1]
        deadline = time.monotonic() + 1.5
        while time.monotonic() < deadline:
            source = _looped_back(int(settings.OVPN_TCP_PORT), peer_port)
            if source is not None:
                return {"ok": True, "ms": ms, "source_ip": source}
            time.sleep(0.1)
    finally:
        sock.close()
    return {"ok": False, "ms": ms, "error": "not_forwarded"}


# ------------------------------------------------------------ health cache
_health_lock = threading.Lock()
_health: dict[int, dict] = {}


def run_checks() -> dict[int, dict]:
    """Check every relay in parallel, remember the results (and each
    relay's source address). Called by the scheduler and the Settings page."""
    from concurrent.futures import ThreadPoolExecutor

    db = SessionLocal()
    try:
        relays = ordered_relays(db)
        if not relays:
            with _health_lock:
                _health.clear()
            return {}
        with ThreadPoolExecutor(max_workers=min(8, len(relays))) as pool:
            results = list(pool.map(check, relays))
        now = time.time()
        changed = False
        out: dict[int, dict] = {}
        for relay, res in zip(relays, results):
            res["at"] = now
            out[relay.id] = res
            if res.get("source_ip") and res["source_ip"] != relay.source_ip:
                relay.source_ip = res["source_ip"]
                changed = True
        if changed:
            db.commit()
    finally:
        db.close()
    forget_labels()
    relay_names_by_ip()  # re-resolve here, off the hooks' path
    with _health_lock:
        _health.clear()
        _health.update(out)
    return out


def forget_health(relay_id: int) -> None:
    with _health_lock:
        _health.pop(relay_id, None)


def get_health() -> dict[int, dict]:
    with _health_lock:
        return {k: dict(v) for k, v in _health.items()}


# ------------------------------------------------- relay IP -> name labels
_label_lock = threading.Lock()
_labels: dict[str, str] = {}
_labels_at = 0.0


def relay_names_by_ip(refresh: bool = True) -> dict[str, str]:
    """Clients behind a relay show up with the relay's IP; this maps those
    IPs back to relay names (refreshed every 5 minutes, resolving relay
    hostnames). refresh=False never touches the DB or DNS -- for the
    OpenVPN hooks, which must not block."""
    global _labels, _labels_at
    with _label_lock:
        if not refresh or time.monotonic() - _labels_at < 300:
            return _labels
    db = SessionLocal()
    try:
        relays = [(r.address, r.name, r.source_ip) for r in ordered_relays(db)]
    finally:
        db.close()
    labels: dict[str, str] = {}
    for address, name, source_ip in relays:
        labels[address] = name
        if source_ip:
            labels[source_ip] = name
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
