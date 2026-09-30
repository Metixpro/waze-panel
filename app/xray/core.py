"""Xray-core next to OpenVPN.

The panel owns Xray's whole config (DATA_DIR/xray/config.json): it is built
from the database -- every enabled inbound, with every user who may connect
as a client -- and nothing else. Whenever something changes, `apply()`
compares the new config with the one on disk:

  * only the client lists differ (a user was added, disabled, expired, ran
    out of data...): the difference is pushed to the running Xray through
    its API, nobody else is disconnected;
  * anything else (an inbound added or edited): the config is checked with
    `xray run -test`, written, and Xray restarted.

Traffic is read per user from Xray's stats API (and reset) on every poll,
and lands in the same usage counter and daily chart as OpenVPN traffic.
"""
import base64
import copy
import datetime
import grp
import hashlib
import json
import logging
import os
import re
import secrets
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import uuid
from pathlib import Path

from sqlalchemy.orm import Session

from app.config import settings
from app.database import SessionLocal
from app.models import VpnUser, XrayInbound, find_vpn_user

logger = logging.getLogger("waze_panel.xray")


class XrayError(RuntimeError):
    pass


# ------------------------------------------------------------------- paths
def xray_bin() -> Path:
    return settings.XRAY_HOME / "xray"


def config_dir() -> Path:
    return settings.DATA_DIR / "xray"


def config_path() -> Path:
    return config_dir() / "config.json"


def installed() -> bool:
    return xray_bin().exists()


_version_cache: tuple[float, str] = (0.0, "")


def version() -> str:
    global _version_cache
    try:
        mtime = xray_bin().stat().st_mtime
    except OSError:
        return ""
    if _version_cache[0] != mtime:
        m = re.search(r"Xray\s+(\d+\.\d+\.\d+)", _run("version").stdout or "")
        _version_cache = (mtime, m[1] if m else "")
    return _version_cache[1]


def _run(*args: str, timeout: float = 10) -> subprocess.CompletedProcess:
    env = {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "XRAY_LOCATION_ASSET": str(settings.XRAY_HOME)}
    try:
        return subprocess.run([str(xray_bin()), *args], capture_output=True, text=True, timeout=timeout, env=env)
    except (OSError, subprocess.SubprocessError) as exc:
        raise XrayError(str(exc)) from exc


def _group_gid() -> int | None:
    for name in ("nogroup", "nobody"):
        try:
            return grp.getgrnam(name).gr_gid
        except KeyError:
            continue
    return None


def _secure(path: Path, mode: int) -> None:
    """Root owns everything; Xray (nobody:nogroup) may only read it."""
    os.chmod(path, mode)
    gid = _group_gid()
    if os.geteuid() == 0 and gid is not None:
        os.chown(path, 0, gid)


def _write_private(path: Path, data: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    _secure(path.parent, 0o750)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(data)
    _secure(tmp, 0o640)
    tmp.replace(path)


def secure_files() -> None:
    """Ownership and modes of everything under the config directory (after
    a restore, the backup's owners may not match this server's)."""
    top = config_dir()
    if not top.is_dir():
        return
    _secure(top, 0o750)
    for path in top.rglob("*"):
        _secure(path, 0o750 if path.is_dir() else 0o640)


# -------------------------------------------------------------------- keys
def x25519() -> tuple[str, str]:
    """(private key, public key) for REALITY."""
    if not installed():
        raise XrayError("هسته‌ی Xray روی سرور نصب نیست؛ روی سرور بزنید: waze-panel xray install")
    out = _run("x25519").stdout
    priv = re.search(r"Private\s*key:\s*(\S+)", out, re.I)
    pub = re.search(r"(?:Public\s*key|Password)[^:]*:\s*(\S+)", out, re.I)
    if not priv or not pub:
        raise XrayError("xray x25519: unexpected output")
    return priv[1], pub[1]


def ensure_credentials(user: VpnUser) -> bool:
    changed = False
    if not user.xray_uuid:
        user.xray_uuid = str(uuid.uuid4())
        changed = True
    if not user.xray_key:
        user.xray_key = base64.b64encode(secrets.token_bytes(32)).decode()
        changed = True
    return changed


def regenerate_credentials(user: VpnUser) -> None:
    user.xray_uuid = str(uuid.uuid4())
    user.xray_key = base64.b64encode(secrets.token_bytes(32)).decode()


def _key_len(method: str) -> int:
    return 16 if "128" in method else 32


def ss_user_key(user: VpnUser, method: str) -> str:
    return base64.b64encode(base64.b64decode(user.xray_key)[: _key_len(method)]).decode()


def ss_server_key(method: str) -> str:
    return base64.b64encode(secrets.token_bytes(_key_len(method))).decode()


def options(ib: XrayInbound) -> dict:
    try:
        return json.loads(ib.options or "{}")
    except ValueError:
        return {}


# --------------------------------------------------------------------- TLS
def _cert_name(ib: XrayInbound, opts: dict) -> str:
    return opts.get("sni") or opts.get("host") or settings.SERVER_ADDRESS


def tls_files(ib: XrayInbound) -> tuple[Path, Path, bool]:
    """Certificate for a TLS inbound: the domain's Let's Encrypt one when
    this server has it (copied where Xray can read it), else a self-signed
    one that links pin by its hash. (crt, key, self_signed)"""
    opts = options(ib)
    name = _cert_name(ib, opts)
    safe = re.sub(r"[^A-Za-z0-9.\-]", "_", name)
    certs = config_dir() / "certs"
    live = Path("/etc/letsencrypt/live") / name
    if (live / "fullchain.pem").exists() and (live / "privkey.pem").exists():
        crt, key = certs / f"{safe}.crt", certs / f"{safe}.key"
        for src, dst in ((live / "fullchain.pem", crt), (live / "privkey.pem", key)):
            data = src.read_text()
            if not dst.exists() or dst.read_text() != data:
                _write_private(dst, data)
        return crt, key, False
    crt, key = certs / f"{safe}-self.crt", certs / f"{safe}-self.key"
    if not crt.exists() or not key.exists():
        certs.mkdir(parents=True, exist_ok=True)
        _secure(certs, 0o750)
        san = f"IP:{name}" if re.fullmatch(r"[\d.]+|[0-9a-fA-F:]+", name) else f"DNS:{name}"
        subprocess.run(
            ["openssl", "req", "-x509", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:prime256v1", "-nodes",
             "-days", "3650", "-subj", f"/CN={name}", "-addext", f"subjectAltName={san}",
             "-keyout", str(key), "-out", str(crt)],
            check=True, capture_output=True, timeout=30,
        )
        _secure(crt, 0o640)
        _secure(key, 0o640)
    return crt, key, True


def cert_pin(crt: Path) -> str:
    """SHA-256 of the certificate (DER), hex: what clients pin a self-signed
    certificate by (Xray's pinnedPeerCertSha256)."""
    pem = crt.read_text()
    b64 = pem.split("-----BEGIN CERTIFICATE-----", 1)[1].split("-----END CERTIFICATE-----", 1)[0]
    return hashlib.sha256(base64.b64decode("".join(b64.split()))).hexdigest()


# ------------------------------------------------------------------ config
def tag(ib: XrayInbound) -> str:
    return f"in-{ib.id}"


def xray_users(db: Session) -> list[VpnUser]:
    """Everyone who may connect over Xray right now, with credentials."""
    users = [u for u in db.query(VpnUser).order_by(VpnUser.id).all() if u.xray_enabled and u.is_usable()]
    if any([ensure_credentials(u) for u in users]):
        db.commit()
    return users


def _client(ib: XrayInbound, opts: dict, user: VpnUser) -> dict:
    if ib.protocol == "vless":
        c = {"id": user.xray_uuid, "email": user.username}
        if opts.get("flow"):
            c["flow"] = opts["flow"]
        return c
    if ib.protocol == "vmess":
        return {"id": user.xray_uuid, "email": user.username}
    if ib.protocol == "trojan":
        return {"password": user.xray_uuid, "email": user.username}
    return {"password": ss_user_key(user, opts["method"]), "email": user.username}


def _stream(ib: XrayInbound, opts: dict) -> dict:
    s: dict = {"network": ib.transport, "security": ib.security}
    host = opts.get("host") or ""
    if ib.transport == "ws":
        s["wsSettings"] = {"path": opts.get("path", "/"), **({"host": host} if host else {})}
    elif ib.transport == "httpupgrade":
        s["httpupgradeSettings"] = {"path": opts.get("path", "/"), **({"host": host} if host else {})}
    elif ib.transport == "xhttp":
        s["xhttpSettings"] = {"path": opts.get("path", "/"), "mode": "auto", **({"host": host} if host else {})}
    elif ib.transport == "grpc":
        s["grpcSettings"] = {"serviceName": opts.get("service_name", "grpc")}
    if ib.security == "reality":
        s["realitySettings"] = {
            "target": opts.get("target") or f"{opts['sni']}:443",
            "serverNames": [opts["sni"]],
            "privateKey": opts["private_key"],
            "shortIds": sorted({"", opts.get("short_id", "")}),
        }
    elif ib.security == "tls":
        crt, key, _ = tls_files(ib)
        tls = {"certificates": [{"certificateFile": str(crt), "keyFile": str(key)}]}
        if ib.transport == "grpc" or ib.transport == "xhttp":
            tls["alpn"] = ["h2", "http/1.1"]
        s["tlsSettings"] = tls
    return s


def inbound_config(ib: XrayInbound, users: list[VpnUser]) -> dict:
    opts = options(ib)
    clients = [_client(ib, opts, u) for u in users if u.uses_inbound(ib.id)]
    if ib.protocol == "vless":
        st = {"clients": clients, "decryption": "none"}
    elif ib.protocol == "shadowsocks":
        st = {"method": opts["method"], "password": opts["server_key"], "clients": clients, "network": "tcp,udp"}
    else:
        st = {"clients": clients}
    cfg = {"tag": tag(ib), "port": ib.port, "protocol": ib.protocol, "settings": st}
    if ib.protocol != "shadowsocks":
        cfg["streamSettings"] = _stream(ib, opts)
    return cfg


PRIVATE_NETS = [
    "0.0.0.0/8", "10.0.0.0/8", "100.64.0.0/10", "127.0.0.0/8", "169.254.0.0/16", "172.16.0.0/12",
    "192.168.0.0/16", "::1/128", "fc00::/7", "fe80::/10",
]


def build_config(db: Session) -> dict:
    inbounds = db.query(XrayInbound).filter(XrayInbound.enabled.is_(True)).order_by(XrayInbound.position, XrayInbound.id).all()
    users = xray_users(db) if inbounds else []
    return {
        "log": {"loglevel": "warning", "access": "none"},
        "api": {"tag": "api", "listen": f"127.0.0.1:{settings.XRAY_API_PORT}", "services": ["HandlerService", "StatsService"]},
        "stats": {},
        "policy": {
            "levels": {
                "0": {
                    "statsUserUplink": True,
                    "statsUserDownlink": True,
                    "statsUserOnline": True,
                    "handshake": 4,
                    "connIdle": 300,
                    "uplinkOnly": 2,
                    "downlinkOnly": 5,
                    "bufferSize": 512,
                }
            },
            "system": {"statsInboundUplink": True, "statsInboundDownlink": True},
        },
        "inbounds": [inbound_config(ib, users) for ib in inbounds],
        "outbounds": [
            {"protocol": "freedom", "tag": "direct", "settings": {"domainStrategy": "UseIP"}},
            {"protocol": "blackhole", "tag": "block"},
        ],
        # Nothing on this server's loopback or private networks for users
        # (Xray's freedom outbound refuses those by default as well).
        "routing": {"rules": [{"type": "field", "ip": PRIVATE_NETS, "outboundTag": "block"}]},
    }


def read_disk() -> dict | None:
    try:
        return json.loads(config_path().read_text())
    except (OSError, ValueError):
        return None


def _dump(cfg: dict) -> str:
    return json.dumps(cfg, indent=2, ensure_ascii=False) + "\n"


def write(cfg: dict) -> None:
    _write_private(config_path(), _dump(cfg))


def test(cfg: dict) -> str | None:
    """None if Xray accepts the config, else its complaint."""
    config_dir().mkdir(parents=True, exist_ok=True)
    fd, path = tempfile.mkstemp(dir=config_dir(), suffix=".json")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(_dump(cfg))
        r = _run("run", "-test", "-c", path, timeout=20)
        if r.returncode == 0:
            return None
        lines = [l for l in (r.stdout + r.stderr).splitlines() if "Failed" in l or "failed" in l or "error" in l.lower()]
        return (lines[-1] if lines else (r.stderr or r.stdout).strip()[-300:]) or "invalid config"
    finally:
        os.unlink(path)


# ------------------------------------------------------------------ service
def api_up() -> bool:
    try:
        with socket.create_connection(("127.0.0.1", settings.XRAY_API_PORT), timeout=1):
            return True
    except OSError:
        return False


def _systemd() -> bool:
    return Path("/run/systemd/system").is_dir() and shutil.which("systemctl") is not None


def _wait(check, timeout: float) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if check():
            return True
        time.sleep(0.25)
    return False


def restart() -> bool:
    if _systemd():
        try:
            r = subprocess.run(["systemctl", "restart", settings.XRAY_SERVICE], capture_output=True, text=True, timeout=30)
        except (OSError, subprocess.SubprocessError):
            return False
        if r.returncode != 0:
            logger.warning("restarting Xray failed: %s", r.stderr.strip())
            return False
    else:
        # No systemd (a container): stop it and let its supervisor start it.
        subprocess.run(["pkill", "-f", f"{xray_bin()} run -c {config_path()}"], capture_output=True)
        _wait(lambda: not api_up(), 5)
    return _wait(api_up, 12)


def service_state() -> str:
    if not installed():
        return "missing"
    return "running" if api_up() else "stopped"


# ---------------------------------------------------------------------- api
def _api(command: str, *args: str, timeout: float = 6) -> str:
    # flags must come before positional arguments (files, emails)
    r = _run("api", command, f"--server=127.0.0.1:{settings.XRAY_API_PORT}", *args, timeout=timeout)
    if r.returncode != 0:
        raise XrayError((r.stderr or r.stdout).strip()[-300:])
    return r.stdout


def _json(text: str) -> dict:
    start = text.find("{")
    if start < 0:
        return {}
    try:
        return json.loads(text[start:])
    except ValueError:
        return {}


def traffic_since_last() -> dict[str, int]:
    """Bytes (up + down) per user since the previous call."""
    out: dict[str, int] = {}
    for item in _json(_api("statsquery", "-pattern", "user>>>", "-reset")).get("stat", []):
        parts = item.get("name", "").split(">>>")
        if len(parts) == 4 and parts[2] == "traffic":
            out[parts[1]] = out.get(parts[1], 0) + int(item.get("value", 0) or 0)
    return out


def inbound_traffic_since_last() -> dict[int, int]:
    """Bytes through each inbound (by id) since the previous call."""
    out: dict[int, int] = {}
    for item in _json(_api("statsquery", "-pattern", "inbound>>>in-", "-reset")).get("stat", []):
        parts = item.get("name", "").split(">>>")
        if len(parts) == 4 and parts[2] == "traffic" and parts[1][3:].isdigit():
            out[int(parts[1][3:])] = out.get(int(parts[1][3:]), 0) + int(item.get("value", 0) or 0)
    return out


def online_ips() -> dict[str, list[tuple[str, int]]]:
    out: dict[str, list[tuple[str, int]]] = {}
    for u in _json(_api("statsonlineiplist", "-all")).get("users", []):
        ips = [(i.get("ip", ""), int(i.get("lastSeen", 0) or 0)) for i in u.get("ips", [])]
        if u.get("email") and ips:
            out[u["email"]] = sorted(ips, key=lambda x: -x[1])
    return out


def _add_users(inbound: dict, clients: list[dict]) -> bool:
    doc = {"inbounds": [{k: inbound[k] for k in ("tag", "port", "protocol")} | {"settings": {**inbound["settings"], "clients": clients}}]}
    fd, path = tempfile.mkstemp(dir=config_dir(), suffix=".json")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(doc, f)
        _secure(Path(path), 0o600)
        out = _api("adu", path)
    finally:
        os.unlink(path)
    m = re.search(r"Added (\d+) user", out)
    return bool(m) and int(m[1]) == len(clients)


def _remove_users(tag_: str, emails: list[str]) -> bool:
    out = _api("rmu", f"-tag={tag_}", *emails)
    m = re.search(r"Removed (\d+) user", out)
    return bool(m) and int(m[1]) == len(emails)


# -------------------------------------------------------------------- apply
# who is on Xray right now: {email: {since, seen, bytes, ip, devices}}
_online_lock = threading.Lock()
_online: dict[str, dict] = {}


def _without_clients(cfg: dict) -> dict:
    c = copy.deepcopy(cfg)
    for ib in c.get("inbounds", []):
        ib.get("settings", {}).pop("clients", None)
    return c


def _clients_by_tag(cfg: dict) -> dict[str, dict[str, dict]]:
    return {ib["tag"]: {c["email"]: c for c in ib.get("settings", {}).get("clients", [])} for ib in cfg.get("inbounds", [])}


def _live_update(old: dict, new: dict) -> bool:
    old_c, new_c = _clients_by_tag(old), _clients_by_tag(new)
    inbounds = {ib["tag"]: ib for ib in new["inbounds"]}
    try:
        for tag_, clients in new_c.items():
            before = old_c.get(tag_, {})
            gone = [e for e in before if e not in clients or before[e] != clients[e]]
            added = [c for e, c in clients.items() if e not in before or before[e] != c]
            if gone and not _remove_users(tag_, gone):
                return False
            if added and not _add_users(inbounds[tag_], added):
                return False
    except XrayError as exc:
        logger.warning("live Xray update failed (%s); restarting instead", exc)
        return False
    return True


def _dropped(old: dict, new: dict) -> set[str]:
    """Users removed from, or with changed credentials in, some inbound."""
    old_c, new_c = _clients_by_tag(old), _clients_by_tag(new)
    return {e for tag_, before in old_c.items() for e, c in before.items() if new_c.get(tag_, {}).get(e) != c}


_apply_lock = threading.Lock()


def apply(db: Session | None = None, force: bool = False) -> str:
    """Bring Xray in line with the database. Returns what it did:
    same / live / restarted / written (Xray not installed)."""
    with _apply_lock:
        own = db is None
        db = db or SessionLocal()
        try:
            new = build_config(db)
        finally:
            if own:
                db.close()
        old = read_disk()
        if not installed():
            write(new)
            return "written"
        up = api_up()
        if new == old and up and not force:
            return "same"
        # Removing a user over the API leaves their open connections running
        # (and no longer counted), so someone who is online right now and just
        # lost access -- quota, expiry, disabled, new link -- is cut off with a
        # restart; everyone else reconnects on their own within a second.
        with _online_lock:
            kick = _dropped(old, new) & set(_online) if old else set()
        if old and up and not force and not kick and _without_clients(old) == _without_clients(new) and _live_update(old, new):
            write(new)
            return "live"
        err = test(new)
        if err:
            raise XrayError(err)
        if up:
            collect()
        write(new)
        if restart():
            with _online_lock:
                for email in kick:
                    _online.pop(email, None)
            return "restarted"
        if old is not None and old != new:
            write(old)
            restart()
        raise XrayError("Xray با تنظیمات جدید بالا نیامد؛ تنظیم قبلی برگردانده شد. گزارش: journalctl -u waze-xray -e")


_pending = threading.Event()
_worker_lock = threading.Lock()


def sync_soon() -> None:
    """Apply in the background (after a user change), coalescing bursts."""
    _pending.set()
    if not _worker_lock.acquire(blocking=False):
        return

    def run() -> None:
        try:
            while _pending.is_set():
                _pending.clear()
                time.sleep(0.3)
                try:
                    apply()
                except Exception:
                    logger.exception("Xray sync failed")
        finally:
            _worker_lock.release()

    threading.Thread(target=run, name="xray-sync", daemon=True).start()


# --------------------------------------------------------------- firewall
def _ufw_active() -> bool:
    if shutil.which("ufw") is None:
        return False
    r = subprocess.run(["ufw", "status"], capture_output=True, text=True)
    return "Status: active" in r.stdout


def open_port(port: int, udp: bool = False) -> None:
    """Let the port in, where a firewall is in the way (ufw, or iptables
    with a DROP policy)."""
    protos = ("tcp", "udp") if udp else ("tcp",)
    for p in protos:
        if _ufw_active():
            subprocess.run(["ufw", "allow", f"{port}/{p}"], capture_output=True)
        if shutil.which("iptables"):
            rule = ["INPUT", "-p", p, "--dport", str(port), "-j", "ACCEPT"]
            if subprocess.run(["iptables", "-w", "-C", *rule], capture_output=True).returncode != 0:
                subprocess.run(["iptables", "-w", "-I", *rule], capture_output=True)


def close_port(port: int, udp: bool = False) -> None:
    protos = ("tcp", "udp") if udp else ("tcp",)
    for p in protos:
        if _ufw_active():
            subprocess.run(["ufw", "delete", "allow", f"{port}/{p}"], capture_output=True)
        if shutil.which("iptables"):
            subprocess.run(["iptables", "-w", "-D", "INPUT", "-p", p, "--dport", str(port), "-j", "ACCEPT"], capture_output=True)


# ------------------------------------------------------------------ polling

_stats_lock = threading.Lock()


def poll() -> None:
    """Traffic and online users since the last poll, then enforcement: the
    apply() at the end drops anyone who just ran out of data or time."""
    if not installed() or not api_up():
        with _online_lock:
            _online.clear()
        return
    collect()
    try:
        apply()
    except XrayError as exc:
        logger.warning("Xray sync: %s", exc)


def collect() -> None:
    """Book Xray's counters (reset on read) into users' usage, the inbounds'
    traffic and who is online. Also run right before every restart, which
    would otherwise throw away whatever was counted since the last poll."""
    with _stats_lock:
        _collect()


def _collect() -> None:
    try:
        traffic = traffic_since_last()
    except XrayError as exc:
        logger.warning("Xray stats unavailable: %s", exc)
        return
    try:
        ips = online_ips()
    except XrayError:
        ips = {}
    try:
        per_inbound = inbound_traffic_since_last()
    except XrayError:
        per_inbound = {}
    # Xray lists an IP only for ~20s after a user's last *new* connection,
    # so a long download drops off that list: traffic counts as presence too,
    # and a user stays online for a grace period after the last sign of life.
    active = {e for e, b in traffic.items() if b > 0} | set(ips)
    now = datetime.datetime.now(datetime.timezone.utc)
    stamp = int(time.time())
    grace = max(60, 3 * settings.TRAFFIC_POLL_INTERVAL_SECONDS)

    from app.openvpn.scheduler import record_usage

    last_ip: dict[str, str] = {}
    db = SessionLocal()
    try:
        for email in active:
            user = find_vpn_user(db, email)
            if user is None:
                continue
            last_ip[email] = user.last_ip or ""
            if traffic.get(email):
                record_usage(db, user, traffic[email])
            user.last_connected_at = now
            if email in ips:
                user.last_ip = ips[email][0][0]
        for ib in db.query(XrayInbound).filter(XrayInbound.id.in_(list(per_inbound))).all():
            ib.traffic_bytes = (ib.traffic_bytes or 0) + per_inbound[ib.id]
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("Xray accounting failed")
    finally:
        db.close()

    with _online_lock:
        for email in active:
            entry = _online.setdefault(email, {"since": stamp, "bytes": 0, "ip": last_ip.get(email, "")})
            entry["seen"] = stamp
            entry["bytes"] += traffic.get(email, 0)
            if email in ips:
                entry["ip"] = ips[email][0][0]
                entry["devices"] = len(ips[email])
        for email in [e for e, v in _online.items() if stamp - v["seen"] > grace]:
            _online.pop(email, None)


def online_sessions() -> list[dict]:
    with _online_lock:
        return [
            {"username": e, "proto": "xray", "ip": v.get("ip", ""), "since": v["since"], "bytes": v["bytes"]}
            for e, v in _online.items()
        ]


def startup() -> None:
    """At panel start: firewall openings for the inbounds, then the config."""
    db = SessionLocal()
    try:
        for ib in db.query(XrayInbound).filter(XrayInbound.enabled.is_(True)).all():
            try:
                open_port(ib.port, udp=ib.protocol == "shadowsocks")
            except OSError:
                pass
    finally:
        db.close()
    sync_soon()
