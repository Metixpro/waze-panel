"""Renders .ovpn client config files on the fly from the CA / issued client
cert / tls-crypt keys currently on disk, so a change to the server address
in Settings is reflected the next time a user (re)downloads their config --
no regeneration step needed."""
from sqlalchemy.orm import Session

from app.models import VpnUser
from app.openvpn import certs, tlscrypt
from app.relays import client_remotes

_CLIENT_TEMPLATE = """\
client
dev tun
proto {proto}
{remotes}resolv-retry infinite
nobind
persist-key
persist-tun
remote-cert-tls server
cipher AES-256-GCM
data-ciphers AES-256-GCM:AES-128-GCM:CHACHA20-POLY1305
auth SHA256
verb 3
sndbuf 524288
rcvbuf 524288
comp-lzo no
{extra}<ca>
{ca}
</ca>
{identity}{tls}"""


def _tls_block(db: Session, user: VpnUser | None) -> str:
    """The shared ta.key, or -- once personal keys are on -- the user's own
    tls-crypt-v2 key (the shared profile has one of its own)."""
    if tlscrypt.get_mode(db) == "shared":
        return f"<tls-crypt>\n{certs.read_ta_key().strip()}\n</tls-crypt>\n"
    try:
        key = tlscrypt.user_key(db, user) if user else tlscrypt.shared_key(db)
    except tlscrypt.TlsKeyError as exc:
        raise certs.CertError(f"tls-crypt-v2 key: {exc}") from exc
    return f"<tls-crypt-v2>\n{key.strip()}\n</tls-crypt-v2>\n"


def _render(proto: str, identity: str, extra: str, tls: str, key: str = "") -> str:
    if proto not in ("udp", "tcp"):
        raise ValueError("proto must be 'udp' or 'tcp'")

    # Relays (servers inside the country forwarding to this one) come
    # first, then this server on its own port and its extra ports; the
    # client moves on to the next one when an address doesn't answer within
    # `timeout` seconds.
    hops, timeout = client_remotes(proto, key)
    remotes = "".join(f"remote {address} {port}\n" for address, port in hops)
    if len(hops) > 1:
        remotes += f"server-poll-timeout {timeout}\n"
    # UDP has no connection teardown: without this the server only notices a
    # client left after the keepalive timeout (minutes), so it would keep
    # showing as online and its final traffic would be accounted late.
    if proto == "udp":
        extra += "explicit-exit-notify 2\nfast-io\n"
    elif proto == "tcp":
        extra += "tcp-nodelay\n"

    return _CLIENT_TEMPLATE.format(
        proto=proto,
        extra=extra,
        remotes=remotes,
        ca=certs.read_ca_cert().strip(),
        identity=identity,
        tls=tls,
    )


def build_ovpn(db: Session, user: VpnUser, proto: str) -> str:
    """proto must be 'udp' or 'tcp'. Password-only users get a
    certificate-less profile; the others their own certificate (plus a
    username/password prompt in cert_pass mode)."""
    if user.auth_mode == "pass":
        return _shared(db, proto, user)

    identity = "<cert>\n{cert}\n</cert>\n<key>\n{key}\n</key>\n".format(
        cert=certs.read_client_cert(user.username).strip(),
        key=certs.read_client_key(user.username).strip(),
    )
    extra = "auth-user-pass\n" if user.auth_mode == "cert_pass" else ""
    return _render(proto, identity, extra, _tls_block(db, user), key=user.username)


def build_shared_ovpn(db: Session, proto: str) -> str:
    """One profile for every password-only user: no client certificate, the
    app asks for a username and password instead."""
    return _shared(db, proto, None)


def _shared(db: Session, proto: str, user: VpnUser | None) -> str:
    # Without CLIENT_CERT 0, OpenVPN Connect treats a profile with no
    # <cert>/<key> as "external certificate" and asks the phone's keystore
    # for one; OpenVPN 2.x just ignores the variable.
    extra = "auth-user-pass\nsetenv CLIENT_CERT 0\n"
    return _render(proto, "", extra, _tls_block(db, user), key=user.username if user else "")
