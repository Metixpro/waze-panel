"""Connection options that change how OpenVPN itself is reached (Settings):

  TLS_CRYPT_MODE   shared / compat / per_user keys (app/openvpn/tlscrypt.py)
  EXTRA_UDP_PORTS  extra ports per instance, e.g. "53,2053". iptables sends
  EXTRA_TCP_PORTS  them to the real port; client configs list them after it,
                   so a client whose network blocks one port moves on to the
                   next by itself (server-poll-timeout)
  COVER_ENABLED    "1": HTTPS cover on the TCP port (app/cover.py)
  COVER_SITE       "1": the cover is the Nginx site on this server's 443
"""
import logging
import shutil
import subprocess

from sqlalchemy.orm import Session

from app import cover
from app.about import system_info
from app.config import settings
from app.models import Setting, VpnUser
from app.openvpn import service, tlscrypt

logger = logging.getLogger("waze_panel.connection")

MAX_EXTRA = 4
CHAIN = "WAZE_PORTS"
# ports that tend to stay open on filtered networks
SUGGESTED = {"udp": [53, 2053, 500, 4500, 123], "tcp": [80, 8080, 2083, 2087, 8443]}

_FA = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")


class OptionError(ValueError):
    pass


def _fa(n) -> str:
    return str(n).translate(_FA)


def _get(db: Session, key: str, default: str = "") -> str:
    row = db.get(Setting, key)
    return row.value if row is not None else default


def _put(db: Session, key: str, value: str) -> None:
    row = db.get(Setting, key)
    if row is None:
        db.add(Setting(key=key, value=value))
    else:
        row.value = value
    db.commit()


def main_port(proto: str) -> int:
    return int(settings.OVPN_UDP_PORT if proto == "udp" else settings.OVPN_TCP_PORT)


def extra_ports(db: Session, proto: str) -> list[int]:
    out = []
    for part in _get(db, f"EXTRA_{proto.upper()}_PORTS").split(","):
        if part.strip().isdigit() and int(part) != main_port(proto):
            out.append(int(part))
    return out


def cover_enabled(db: Session) -> bool:
    return _get(db, "COVER_ENABLED") == "1"


# ------------------------------------------------------------ extra ports
def _loopback(hex_addr: str) -> bool:
    if len(hex_addr) == 8:
        return hex_addr.upper().endswith("7F")
    return hex_addr.upper() in ("00000000000000000000000001000000",)


def listening_ports(proto: str) -> set[int]:
    """Ports some program on this server listens on from outside (loopback-
    only listeners, like systemd-resolved on 127.0.0.53:53, don't count:
    redirected traffic never reaches them)."""
    ports: set[int] = set()
    for table in (f"/proc/net/{proto}", f"/proc/net/{proto}6"):
        try:
            with open(table) as f:
                next(f, None)
                for line in f:
                    parts = line.split()
                    if len(parts) < 4 or (proto == "tcp" and parts[3] != "0A"):
                        continue
                    addr, port = parts[1].rsplit(":", 1)
                    if not _loopback(addr):
                        ports.add(int(port, 16))
        except OSError:
            continue
    return ports


def port_problem(proto: str, port: int, listening: set[int] | None = None) -> str | None:
    if not 1 <= port <= 65535:
        return "پورت باید عددی بین ۱ تا ۶۵۵۳۵ باشد."
    if port == main_port(proto):
        return f"پورت {_fa(port)} همین حالا پورت اصلی {proto.upper()} است."
    if proto == "tcp" and port == settings.PANEL_PORT:
        return f"پورت {_fa(port)} مال خود پنل است."
    if proto == "tcp" and port in (settings.OVPN_UDP_MGMT_PORT, settings.OVPN_TCP_MGMT_PORT, settings.COVER_PORT):
        return f"پورت {_fa(port)} را پنل برای کار داخلی لازم دارد."
    if port in (listening if listening is not None else listening_ports(proto)):
        return f"پورت {_fa(port)} ({proto.upper()}) را برنامه‌ی دیگری روی این سرور استفاده می‌کند."
    return None


def check_extra(proto: str, ports: list[int]) -> list[int]:
    clean: list[int] = []
    for p in ports:
        if p not in clean:
            clean.append(p)
    if len(clean) > MAX_EXTRA:
        raise OptionError(f"حداکثر {_fa(MAX_EXTRA)} پورت اضافه برای هر پروتکل.")
    listening = listening_ports(proto)
    for p in clean:
        problem = port_problem(proto, p, listening)
        if problem:
            raise OptionError(problem)
    return clean


def suggestions(proto: str, current: list[int]) -> list[int]:
    listening = listening_ports(proto)
    return [p for p in SUGGESTED[proto] if p not in current and port_problem(proto, p, listening) is None]


def _ipt(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["iptables", "-w", *args], capture_output=True, text=True, timeout=15)


def apply_port_rules(db: Session) -> str | None:
    """Idempotent: our own chain, flushed and refilled. Only traffic
    addressed to this server is redirected -- never what VPN clients send
    out through it."""
    udp, tcp = extra_ports(db, "udp"), extra_ports(db, "tcp")
    if shutil.which("iptables") is None:
        return "iptables not found" if udp or tcp else None
    jump = ["PREROUTING", "-m", "addrtype", "--dst-type", "LOCAL", "!", "-i", "tun-waze+", "-j", CHAIN]
    try:
        exists = _ipt("-t", "nat", "-C", *jump).returncode == 0
        if not udp and not tcp:
            if exists:
                _ipt("-t", "nat", "-D", *jump)
            _ipt("-t", "nat", "-F", CHAIN)
            _ipt("-t", "nat", "-X", CHAIN)
            return None
        _ipt("-t", "nat", "-N", CHAIN)  # fails harmlessly if it exists
        _ipt("-t", "nat", "-F", CHAIN)
        for proto, ports in (("udp", udp), ("tcp", tcp)):
            for p in ports:
                r = _ipt("-t", "nat", "-A", CHAIN, "-p", proto, "--dport", str(p),
                         "-j", "REDIRECT", "--to-ports", str(main_port(proto)))
                if r.returncode != 0:
                    return r.stderr.strip() or "iptables failed"
        if not exists:
            r = _ipt("-t", "nat", "-I", *jump)
            if r.returncode != 0:
                return r.stderr.strip() or "iptables failed"
    except (OSError, subprocess.SubprocessError) as exc:
        return str(exc)
    return None


# ------------------------------------------------------- OpenVPN includes
def openvpn_texts(db: Session) -> dict[str, str]:
    mode = tlscrypt.get_mode(db)
    share = cover.target(_get(db, "COVER_SITE") == "1") if cover_enabled(db) else None
    return {p: service.render(p, mode, share) for p in service.INSTANCES}


def write_openvpn(db: Session) -> None:
    """Bring the include files in line with the database, without a
    restart (install.sh restarts the services itself; at boot the panel
    starts before OpenVPN)."""
    if tlscrypt.get_mode(db) != "shared":
        tlscrypt.ensure_server_key()
    service.apply(openvpn_texts(db), restart_changed=False)


def startup(db: Session) -> None:
    if service.wired():
        try:
            write_openvpn(db)
        except (OSError, tlscrypt.TlsKeyError) as exc:
            logger.warning("could not write the OpenVPN include files: %s", exc)
    err = apply_port_rules(db)
    if err:
        logger.warning("extra ports not applied: %s", err)
    if cover_enabled(db) and _get(db, "COVER_SITE") != "1":
        cover.start()


# ------------------------------------------------------------------ changes
def _require_wired() -> None:
    if not service.wired():
        raise OptionError("کانفیگ سرور OpenVPN قدیمی است؛ اول روی سرور «waze-panel update» را بزنید.")


def _restart_error(result: dict) -> None:
    if result.get("error"):
        raise OptionError(
            "OpenVPN با این تنظیم بالا نیامد؛ تنظیم قبلی برگردانده شد. گزارش: journalctl -u "
            f"{settings.OVPN_SERVICE_PREFIX}waze-{result.get('failed', ['udp'])[0]} -e"
        )


def set_tls_mode(db: Session, mode: str) -> dict:
    if mode not in tlscrypt.MODES:
        raise OptionError("حالت کلید نامعتبر است.")
    old = tlscrypt.get_mode(db)
    if mode == old:
        return {"restarted": []}
    if mode != "shared" and not tlscrypt.supported():
        raise OptionError("کلید جدا برای هر کاربر به OpenVPN نسخه‌ی ۲٫۵ یا جدیدتر نیاز دارد.")
    _require_wired()
    try:
        if mode != "shared":
            tlscrypt.ensure_server_key()
    except tlscrypt.TlsKeyError as exc:
        raise OptionError(f"ساخت کلید اصلی سرور ناموفق بود: {exc}") from exc
    tlscrypt.set_mode(db, mode)
    result = service.apply(openvpn_texts(db))
    if result.get("error"):
        tlscrypt.set_mode(db, old)
    _restart_error(result)
    return result


def set_cover(db: Session, enabled: bool) -> dict:
    if enabled == cover_enabled(db):
        return {"restarted": []}
    _require_wired()
    old_site = _get(db, "COVER_SITE")
    site = enabled and cover.local_site()
    if enabled and not site and not cover.start():
        raise OptionError(
            f"سایت پوششی روی پورت داخلی {_fa(settings.COVER_PORT)} بالا نیامد: {cover.state()['error']}"
        )
    _put(db, "COVER_ENABLED", "1" if enabled else "0")
    _put(db, "COVER_SITE", "1" if site else "0")
    result = service.apply(openvpn_texts(db))
    if result.get("error"):
        _put(db, "COVER_ENABLED", "0" if enabled else "1")
        _put(db, "COVER_SITE", old_site)
        if enabled:
            cover.stop()
    elif not enabled:
        cover.stop()
    _restart_error(result)
    return result


def set_extra_ports(db: Session, udp: list[int], tcp: list[int]) -> None:
    udp, tcp = check_extra("udp", udp), check_extra("tcp", tcp)
    old = (_get(db, "EXTRA_UDP_PORTS"), _get(db, "EXTRA_TCP_PORTS"))
    _put(db, "EXTRA_UDP_PORTS", ",".join(map(str, udp)))
    _put(db, "EXTRA_TCP_PORTS", ",".join(map(str, tcp)))
    err = apply_port_rules(db)
    if err:
        _put(db, "EXTRA_UDP_PORTS", old[0])
        _put(db, "EXTRA_TCP_PORTS", old[1])
        apply_port_rules(db)
        raise OptionError(f"قانون فایروال ساخته نشد: {err}")


# -------------------------------------------------------------------- state
def status(db: Session) -> dict:
    mode = tlscrypt.get_mode(db)
    total = db.query(VpnUser).filter(VpnUser.revoked.is_(False)).count()
    moved = (
        db.query(VpnUser)
        .filter(VpnUser.revoked.is_(False), VpnUser.tls_key_seen_at.is_not(None))
        .count()
    )
    udp, tcp = extra_ports(db, "udp"), extra_ports(db, "tcp")
    from app.about import system_info

    site = _get(db, "COVER_SITE") == "1"
    cstate = cover.state()
    return {
        "wired": service.wired(),
        "keys": {
            "mode": mode,
            "supported": tlscrypt.supported(),
            "openvpn": system_info()["openvpn"],
            "users": total,
            "users_on_personal_key": moved,
        },
        "ports": {
            "udp": {"main": main_port("udp"), "extra": udp, "suggest": suggestions("udp", udp)},
            "tcp": {"main": main_port("tcp"), "extra": tcp, "suggest": suggestions("tcp", tcp)},
            "max": MAX_EXTRA,
        },
        "cover": {
            "enabled": cover_enabled(db),
            "port": main_port("tcp"),
            "backend": "site" if site else "builtin",
            "running": site or cstate["running"],
            "cert": "letsencrypt" if site else cstate["cert"],
            "error": cstate["error"],
            "url": f"https://{settings.SERVER_ADDRESS}" + ("" if main_port("tcp") == 443 else f":{main_port('tcp')}"),
        },
    }
