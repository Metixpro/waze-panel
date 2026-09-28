"""
Thin wrapper around easy-rsa used to issue and revoke OpenVPN client
certificates. The panel never touches private keys directly -- it just
shells out to the same `easyrsa` binary used during install.
"""
import re
import shutil
import subprocess
from pathlib import Path

from app.config import settings

_CERT_BLOCK_RE = re.compile(
    r"-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----", re.DOTALL
)


class CertError(RuntimeError):
    pass


def _run_easyrsa(*args: str) -> subprocess.CompletedProcess:
    env_prefix = ["./easyrsa"]
    try:
        result = subprocess.run(
            env_prefix + list(args),
            cwd=str(settings.EASYRSA_DIR),
            env={"EASYRSA_BATCH": "1", "PATH": "/usr/bin:/bin:/usr/local/bin"},
            capture_output=True,
            text=True,
        )
    except OSError as exc:
        raise CertError(
            f"easyrsa not runnable at {settings.EASYRSA_DIR} ({exc}). "
            "Was install.sh's PKI setup step completed?"
        ) from exc
    if result.returncode != 0:
        raise CertError(
            f"easyrsa {' '.join(args)} failed (rc={result.returncode}):\n"
            f"{result.stdout}\n{result.stderr}"
        )
    return result


def client_cert_exists(username: str) -> bool:
    cert_path = settings.EASYRSA_PKI_DIR / "issued" / f"{username}.crt"
    return cert_path.exists()


def build_client_cert(username: str) -> None:
    """Issue a brand new, password-less client certificate for `username`."""
    if client_cert_exists(username):
        raise CertError(f"a certificate already exists for '{username}'")
    _run_easyrsa("build-client-full", username, "nopass")


def revoke_client_cert(username: str) -> None:
    """Revoke the client certificate and regenerate the CRL so the server
    instances stop accepting it on the next connection attempt."""
    if not client_cert_exists(username):
        return
    _run_easyrsa("revoke", username)
    _run_easyrsa("gen-crl")
    _install_crl()


def _install_crl() -> None:
    src = settings.EASYRSA_PKI_DIR / "crl.pem"
    dst = settings.OPENVPN_SERVER_DIR / "crl.pem"
    if src.exists():
        shutil.copyfile(src, dst)
        dst.chmod(0o644)


def _extract_cert_block(text: str) -> str:
    match = _CERT_BLOCK_RE.search(text)
    if not match:
        raise CertError("could not find a CERTIFICATE block")
    return match.group(0)


def read_ca_cert() -> str:
    path = settings.EASYRSA_PKI_DIR / "ca.crt"
    return path.read_text()


def read_client_cert(username: str) -> str:
    path = settings.EASYRSA_PKI_DIR / "issued" / f"{username}.crt"
    if not path.exists():
        raise CertError(f"no issued certificate for '{username}'")
    return _extract_cert_block(path.read_text())


def read_client_key(username: str) -> str:
    path = settings.EASYRSA_PKI_DIR / "private" / f"{username}.key"
    if not path.exists():
        raise CertError(f"no private key for '{username}'")
    return path.read_text()


def read_ta_key() -> str:
    path = settings.OPENVPN_SERVER_DIR / "ta.key"
    return path.read_text()


def cleanup_client_files(username: str) -> None:
    """Best-effort removal of leftover key/cert files after a hard delete.
    The CRL entry from revoke_client_cert() is what actually blocks
    reconnection; this just tidies up disk."""
    for sub, ext in (("issued", "crt"), ("private", "key"), ("reqs", "req")):
        p = settings.EASYRSA_PKI_DIR / sub / f"{username}.{ext}"
        if p.exists():
            try:
                p.unlink()
            except OSError:
                pass
