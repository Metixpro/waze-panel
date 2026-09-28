"""Full backup of everything needed to rebuild the server: the database,
panel settings, the whole PKI (CA, server and client keys, CRL) and the
OpenVPN server files. Without the PKI every issued config becomes useless,
so this is the one thing worth keeping off-server.

Paths inside the archive are absolute-relative (etc/waze-panel/...), so a
restore is just extracting it over / and restarting the services.
"""
import datetime
import io
import sqlite3
import tarfile
import tempfile
from pathlib import Path

from app.config import settings


def _add_bytes(tar: tarfile.TarFile, arcname: str, data: bytes, mode: int = 0o600) -> None:
    info = tarfile.TarInfo(arcname)
    info.size = len(data)
    info.mode = mode
    info.mtime = int(datetime.datetime.now().timestamp())
    tar.addfile(info, io.BytesIO(data))


def _db_snapshot() -> bytes:
    """Consistent copy of the live SQLite DB via the online backup API."""
    with tempfile.TemporaryDirectory() as tmp:
        copy_path = Path(tmp) / "snapshot.db"
        src = sqlite3.connect(str(settings.DB_PATH))
        dst = sqlite3.connect(str(copy_path))
        try:
            src.backup(dst)
        finally:
            dst.close()
            src.close()
        return copy_path.read_bytes()


def _arc(path: Path) -> str:
    return str(path).lstrip("/")


def create_backup() -> tuple[str, bytes]:
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M")
    filename = f"waze-panel-backup-{stamp}.tar.gz"

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        _add_bytes(tar, _arc(settings.DB_PATH), _db_snapshot())

        for name in ("panel.env", "hook.env", "setup-nat.sh"):
            p = settings.DATA_DIR / name
            if p.exists():
                tar.add(str(p), arcname=_arc(p))

        if settings.EASYRSA_PKI_DIR.exists():
            tar.add(str(settings.EASYRSA_PKI_DIR), arcname=_arc(settings.EASYRSA_PKI_DIR))

        server_dir = settings.OPENVPN_SERVER_DIR
        for pattern in ("ta.key", "tls-crypt-v2.key", "dh.pem", "crl.pem", "waze-*.conf"):
            for p in sorted(server_dir.glob(pattern)):
                tar.add(str(p), arcname=_arc(p))

    return filename, buf.getvalue()
