"""Personal tls-crypt-v2 keys.

With plain tls-crypt every config carries the same key: one leaked file lets
anyone reach the server's TLS handshake, and the only fix is a new key for
everybody. With tls-crypt-v2 each user gets a key of their own. The server
keeps a single master key and learns whose key a client holds from metadata
sealed inside it ("u:<username>:<key id>"), which
scripts/tls-crypt-verify.py checks with the panel before the handshake
starts. A new key gets a new id, so every copy of the old config stops
working at once while nobody else is affected.

Modes (setting TLS_CRYPT_MODE):
  shared    only the old shared key (ta.key)
  compat    both: new downloads get personal keys, old configs keep working
  per_user  personal keys only
"""
import base64
import hmac
import re
import secrets
import subprocess
import tempfile
import threading
from pathlib import Path

from sqlalchemy.orm import Session

from app.config import settings
from app.models import Setting, VpnUser

MODES = ("shared", "compat", "per_user")
MODE_KEY = "TLS_CRYPT_MODE"
_SHARED_KEY = "TLS_SHARED_KEY"
_SHARED_ID = "TLS_SHARED_KEY_ID"


class TlsKeyError(RuntimeError):
    pass


def server_key_path() -> Path:
    return settings.OPENVPN_SERVER_DIR / "tls-crypt-v2.key"


def openvpn_version() -> tuple[int, int] | None:
    from app.about import system_info

    m = re.match(r"(\d+)\.(\d+)", system_info()["openvpn"] or "")
    return (int(m[1]), int(m[2])) if m else None


def supported() -> bool:
    """tls-crypt-v2 arrived in OpenVPN 2.5."""
    v = openvpn_version()
    return v is not None and v >= (2, 5)


# ---------------------------------------------------------------------- mode
def get_mode(db: Session) -> str:
    row = db.get(Setting, MODE_KEY)
    return row.value if row is not None and row.value in MODES else "shared"


def set_mode(db: Session, mode: str) -> None:
    if mode not in MODES:
        raise ValueError(mode)
    row = db.get(Setting, MODE_KEY)
    if row is None:
        db.add(Setting(key=MODE_KEY, value=mode))
    else:
        row.value = mode
    db.commit()


def init_mode(db: Session) -> str:
    """First run decides: a new server starts on personal keys right away,
    an upgraded one keeps working exactly as before until the admin opts in
    (its users hold configs with the shared key)."""
    row = db.get(Setting, MODE_KEY)
    if row is None:
        has_users = db.query(VpnUser.id).first() is not None
        set_mode(db, "per_user" if not has_users and supported() else "shared")
    return get_mode(db)


# ---------------------------------------------------------------------- keys
def _openvpn(*args: str) -> None:
    try:
        result = subprocess.run(["openvpn", *args], capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError) as exc:
        raise TlsKeyError(f"openvpn not runnable: {exc}") from exc
    if result.returncode != 0:
        raise TlsKeyError((result.stderr or result.stdout).strip()[-400:] or "openvpn --genkey failed")


def ensure_server_key() -> None:
    path = server_key_path()
    if path.exists():
        return
    tmp = path.with_suffix(".key.tmp")
    _openvpn("--genkey", "tls-crypt-v2-server", str(tmp))
    tmp.chmod(0o600)
    tmp.replace(path)


def _client_key(metadata: str) -> str:
    ensure_server_key()
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "client.key"
        _openvpn(
            "--tls-crypt-v2", str(server_key_path()),
            "--genkey", "tls-crypt-v2-client", str(out),
            base64.b64encode(metadata.encode()).decode(),
        )
        return out.read_text().strip()


# One config download asks for UDP and TCP at the same moment: without the
# lock both could mint a key and the user would end up with two files, one
# of them already dead.
_lock = threading.Lock()


def issue_user_key(user: VpnUser) -> None:
    key_id = secrets.token_hex(4)
    user.tls_key = _client_key(f"u:{user.username}:{key_id}")
    user.tls_key_id = key_id
    user.tls_key_seen_at = None


def user_key(db: Session, user: VpnUser) -> str:
    if user.tls_key:
        return user.tls_key
    with _lock:
        db.refresh(user)
        if not user.tls_key:
            issue_user_key(user)
            db.commit()
    return user.tls_key


def shared_key(db: Session) -> str:
    """Key of the shared password-only profile (Settings page). Leaking it
    still needs a valid username and password to get anywhere, and it can be
    replaced on its own."""
    row = db.get(Setting, _SHARED_KEY)
    if row is not None and row.value:
        return row.value
    with _lock:
        db.expire_all()
        row = db.get(Setting, _SHARED_KEY)
        if row is None or not row.value:
            return regenerate_shared_key(db)
    return row.value


def regenerate_shared_key(db: Session) -> str:
    key_id = secrets.token_hex(4)
    key = _client_key(f"s:{key_id}")
    for name, value in ((_SHARED_KEY, key), (_SHARED_ID, key_id)):
        row = db.get(Setting, name)
        if row is None:
            db.add(Setting(key=name, value=value))
        else:
            row.value = value
    db.commit()
    return key


def check_metadata(db: Session, metadata: str) -> tuple[bool, VpnUser | None]:
    """Is this still a current key? Only whether the key itself is valid:
    disabled or expired accounts are refused later by the auth hook, which
    can tell the app why."""
    parts = metadata.strip().split(":")
    if len(parts) == 3 and parts[0] == "u":
        user = db.query(VpnUser).filter(VpnUser.username == parts[1]).first()
        ok = bool(
            user
            and not user.revoked
            and user.tls_key_id
            and hmac.compare_digest(user.tls_key_id, parts[2])
        )
        return ok, user if ok else None
    if len(parts) == 2 and parts[0] == "s":
        row = db.get(Setting, _SHARED_ID)
        return bool(row and row.value and hmac.compare_digest(row.value, parts[1])), None
    return False, None
