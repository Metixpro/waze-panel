"""The part of each OpenVPN server config that the panel manages itself.

install.sh renders waze-udp.conf / waze-tcp.conf once; both end with
`config <dir>/waze-<proto>.panel.conf`. The panel rewrites that small file
when the key mode or the HTTPS cover changes, then restarts the instance.
If an instance doesn't come back up, the previous file is put back and the
instance restarted again, so a bad setting can't leave the VPN down.
"""
import logging
import shutil
import subprocess
import time
from pathlib import Path

from app.config import settings
from app.openvpn import mgmt, tlscrypt

logger = logging.getLogger("waze_panel.openvpn")

INSTANCES = {"udp": "waze-udp", "tcp": "waze-tcp"}
SCRIPTS_DIR = Path(__file__).resolve().parent.parent.parent / "scripts"


def conf_path(proto: str) -> Path:
    return settings.OPENVPN_SERVER_DIR / f"{INSTANCES[proto]}.conf"


def include_path(proto: str) -> Path:
    return settings.OPENVPN_SERVER_DIR / f"{INSTANCES[proto]}.panel.conf"


def wired() -> bool:
    """Both server configs load the managed include (configs written by
    installers before 1.7 don't, until `waze-panel update`)."""
    for proto in INSTANCES:
        try:
            if str(include_path(proto)) not in conf_path(proto).read_text():
                return False
        except OSError:
            return False
    return True


def render(proto: str, mode: str, port_share: tuple[str, int] | None = None) -> str:
    d = settings.OPENVPN_SERVER_DIR
    lines = ["# Managed by Waze Panel (Settings page) -- rewritten on every change."]
    if mode in ("shared", "compat"):
        lines.append(f"tls-crypt {d}/ta.key")
    if mode in ("compat", "per_user"):
        lines.append(f"tls-crypt-v2 {tlscrypt.server_key_path()}")
        lines.append(f"tls-crypt-v2-verify {SCRIPTS_DIR}/tls-crypt-verify.py")
    if proto == "tcp" and port_share:
        lines.append(f"port-share {port_share[0]} {port_share[1]}")
    return "\n".join(lines) + "\n"


def _read(path: Path) -> str | None:
    try:
        return path.read_text()
    except OSError:
        return None


def _write(path: Path, text: str) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(text)
    tmp.chmod(0o644)
    tmp.replace(path)


# ------------------------------------------------------------------ restart
def _mgmt_port(proto: str) -> int:
    return settings.OVPN_UDP_MGMT_PORT if proto == "udp" else settings.OVPN_TCP_MGMT_PORT


def _systemd() -> bool:
    return Path("/run/systemd/system").is_dir() and shutil.which("systemctl") is not None


def _wait(check, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if check():
            return True
        time.sleep(0.3)
    return False


def restart(proto: str) -> bool:
    port = _mgmt_port(proto)
    if _systemd():
        try:
            result = subprocess.run(
                ["systemctl", "restart", f"{settings.OVPN_SERVICE_PREFIX}{INSTANCES[proto]}"],
                capture_output=True, text=True, timeout=60,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            logger.warning("restarting %s failed: %s", proto, exc)
            return False
        if result.returncode != 0:
            logger.warning("restarting %s failed: %s", proto, result.stderr.strip())
            return False
    else:
        # No systemd (a container): stop it over the management interface
        # and let whatever supervises it start it again.
        mgmt.send_signal(port, "SIGTERM")
        _wait(lambda: not mgmt.is_reachable(port), 10)
    return _wait(lambda: mgmt.is_ready(port), 30)


def apply(texts: dict[str, str], restart_changed: bool = True) -> dict:
    """Write the include files that differ from what's on disk and restart
    those instances. On failure everything is rolled back."""
    old = {p: _read(include_path(p)) for p in texts}
    changed = [p for p, t in texts.items() if old[p] != t]
    for p in changed:
        _write(include_path(p), texts[p])
    if not restart_changed or not changed:
        return {"changed": changed, "restarted": [], "error": None}

    failed = [p for p in changed if not restart(p)]
    if not failed:
        return {"changed": changed, "restarted": changed, "error": None}

    logger.error("OpenVPN %s did not come back up; restoring the previous settings", ", ".join(failed))
    for p in changed:
        if old[p] is None:
            include_path(p).unlink(missing_ok=True)
        else:
            _write(include_path(p), old[p])
        restart(p)
    return {"changed": [], "restarted": [], "error": "restart_failed", "failed": failed}
