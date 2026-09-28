"""HTTPS cover for the OpenVPN TCP port.

With OpenVPN's port-share, every connection to the TCP port that isn't
OpenVPN is handed to a web server. A browser, a scanner or an active probe
opening https://<server>:<tcp port> then finds an ordinary HTTPS site
instead of a port that only answers VPN clients. This hides the port, not
the traffic: sessions of connected users are still OpenVPN.

The web server is Nginx when the installer set one up with a domain (a real
site with a real certificate), otherwise a small built-in one on a loopback
port that answers like a freshly installed web server.
"""
import email.utils
import ipaddress
import logging
import socket
import ssl
import subprocess
import threading
from pathlib import Path

from app.config import settings

logger = logging.getLogger("waze_panel.cover")

COVER_DIR = settings.DATA_DIR / "cover"

_WELCOME = b"""<!DOCTYPE html>
<html>
<head>
<title>Welcome to nginx!</title>
<style>
html { color-scheme: light dark; }
body { width: 35em; margin: 0 auto;
font-family: Tahoma, Verdana, Arial, sans-serif; }
</style>
</head>
<body>
<h1>Welcome to nginx!</h1>
<p>If you see this page, the nginx web server is successfully installed and
working. Further configuration is required.</p>

<p>For online documentation and support please refer to
<a href="http://nginx.org/">nginx.org</a>.<br/>
Commercial support is available at
<a href="http://nginx.com/">nginx.com</a>.</p>

<p><em>Thank you for using nginx.</em></p>
</body>
</html>
"""


def _error_page(title: str, heading: str = "", line: str = "") -> bytes:
    extra = f"<center>{line}</center>\r\n" if line else ""
    return (
        f"<html>\r\n<head><title>{title}</title></head>\r\n<body>\r\n"
        f"<center><h1>{heading or title}</h1></center>\r\n{extra}<hr><center>nginx</center>\r\n</body>\r\n</html>\r\n"
    ).encode()


_PLAIN_HTTP = _error_page(
    "400 The plain HTTP request was sent to HTTPS port", "400 Bad Request",
    "The plain HTTP request was sent to HTTPS port",
)


_REASONS = {200: "OK", 400: "Bad Request", 404: "Not Found", 405: "Not Allowed"}
_LAST_MODIFIED = "Tue, 11 Apr 2023 01:45:34 GMT"


# ------------------------------------------------------------- certificate
def _letsencrypt() -> tuple[Path, Path] | None:
    live = Path("/etc/letsencrypt/live") / settings.SERVER_ADDRESS
    cert, key = live / "fullchain.pem", live / "privkey.pem"
    return (cert, key) if cert.exists() and key.exists() else None


def _self_signed() -> tuple[Path, Path]:
    """Made once per server address (and again if the address changes)."""
    cert, key, name = COVER_DIR / "cert.pem", COVER_DIR / "key.pem", COVER_DIR / "name"
    address = settings.SERVER_ADDRESS
    if cert.exists() and key.exists() and name.exists() and name.read_text().strip() == address:
        return cert, key
    COVER_DIR.mkdir(parents=True, exist_ok=True)
    COVER_DIR.chmod(0o700)
    try:
        ipaddress.ip_address(address)
        san = f"IP:{address}"
    except ValueError:
        san = f"DNS:{address}"
    subprocess.run(
        [
            "openssl", "req", "-x509", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:prime256v1",
            "-nodes", "-days", "3650", "-subj", f"/CN={address}", "-addext", f"subjectAltName={san}",
            "-keyout", str(key), "-out", str(cert),
        ],
        check=True, capture_output=True, timeout=30,
    )
    key.chmod(0o600)
    name.write_text(address + "\n")
    return cert, key


def certificate() -> tuple[str, Path, Path]:
    le = _letsencrypt()
    if le:
        return "letsencrypt", *le
    return "self_signed", *_self_signed()


# ------------------------------------------------------ built-in web server
def _reply(conn, status: int, body: bytes, head_only: bool = False) -> None:
    lines = [
        f"HTTP/1.1 {status} {_REASONS[status]}",
        "Server: nginx",
        f"Date: {email.utils.formatdate(usegmt=True)}",
        "Content-Type: text/html",
        f"Content-Length: {len(body)}",
        "Connection: close",
    ]
    if status == 200:
        lines += [f"Last-Modified: {_LAST_MODIFIED}", 'ETag: "6434bbbe-267"', "Accept-Ranges: bytes"]
    conn.sendall(("\r\n".join(lines) + "\r\n\r\n").encode() + (b"" if head_only else body))


def _serve(conn, tls: bool) -> None:
    data = b""
    while b"\r\n\r\n" not in data and b"\n\n" not in data and len(data) < 8192:
        chunk = conn.recv(4096)
        if not chunk:
            break
        data += chunk
    if not data:
        return
    if not tls:
        return _reply(conn, 400, _PLAIN_HTTP)
    parts = data.split(b"\r\n", 1)[0].decode("latin-1").split(" ")
    if len(parts) != 3 or not parts[2].startswith("HTTP/1."):
        return _reply(conn, 400, _error_page("400 Bad Request"))
    method, path = parts[0], parts[1].split("?", 1)[0]
    if method not in ("GET", "HEAD"):
        return _reply(conn, 405, _error_page("405 Not Allowed"))
    if path in ("/", "/index.html"):
        return _reply(conn, 200, _WELCOME, head_only=method == "HEAD")
    return _reply(conn, 404, _error_page("404 Not Found"), head_only=method == "HEAD")


class _Server:
    MAX_CLIENTS = 64

    def __init__(self, ctx: ssl.SSLContext, port: int):
        self.ctx = ctx
        self.port = port
        self.slots = threading.BoundedSemaphore(self.MAX_CLIENTS)
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", port))
        self.sock.listen(64)
        self.closed = False
        threading.Thread(target=self._accept, name="cover-accept", daemon=True).start()

    def _accept(self) -> None:
        while not self.closed:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                break
            if not self.slots.acquire(blocking=False):
                conn.close()
                continue
            threading.Thread(target=self._handle, args=(conn,), daemon=True).start()

    def _handle(self, conn: socket.socket) -> None:
        try:
            conn.settimeout(10)
            first = conn.recv(1, socket.MSG_PEEK)
            if first == b"\x16":  # TLS handshake record
                with self.ctx.wrap_socket(conn, server_side=True) as tls_conn:
                    _serve(tls_conn, tls=True)
            elif first:
                _serve(conn, tls=False)
        except (OSError, ssl.SSLError):
            pass
        finally:
            try:
                conn.close()
            except OSError:
                pass
            self.slots.release()

    def close(self) -> None:
        self.closed = True
        # close() alone doesn't wake the thread blocked in accept(), and the
        # port would stay bound until the next connection
        for step in (lambda: self.sock.shutdown(socket.SHUT_RDWR), self.sock.close):
            try:
                step()
            except OSError:
                pass


_lock = threading.Lock()
_server: _Server | None = None
_state = {"running": False, "cert": "", "error": ""}


def _context() -> tuple[str, ssl.SSLContext]:
    kind, cert, key = certificate()
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.set_alpn_protocols(["http/1.1"])
    ctx.load_cert_chain(str(cert), str(key))
    return kind, ctx


def start() -> bool:
    global _server
    with _lock:
        if _server is not None:
            _server.close()
            _server = None
        try:
            kind, ctx = _context()
            _server = _Server(ctx, settings.COVER_PORT)
        except (OSError, ssl.SSLError, subprocess.SubprocessError) as exc:
            logger.warning("HTTPS cover could not start on 127.0.0.1:%s: %s", settings.COVER_PORT, exc)
            _state.update(running=False, cert="", error=str(exc)[:200])
            return False
        _state.update(running=True, cert=kind, error="")
        return True


def stop() -> None:
    global _server
    with _lock:
        if _server is not None:
            _server.close()
            _server = None
        _state.update(running=False, cert="", error="")


def state() -> dict:
    return dict(_state)


# ------------------------------------------------------------------ target
def local_site() -> bool:
    """Nginx (set up by the installer with a domain) serves HTTPS on 443 of
    this same server, and OpenVPN TCP runs on another port: hand probes to
    that real site."""
    if int(settings.OVPN_TCP_PORT) == 443:
        return False
    try:
        with socket.create_connection(("127.0.0.1", 443), timeout=2) as raw:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            with ctx.wrap_socket(raw, server_hostname=settings.SERVER_ADDRESS):
                return True
    except (OSError, ssl.SSLError):
        return False


def target(use_site: bool) -> tuple[str, int]:
    return ("127.0.0.1", 443) if use_site else ("127.0.0.1", settings.COVER_PORT)
