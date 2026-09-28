"""Renders .ovpn client config files on the fly from the CA / issued client
cert / ta.key currently on disk, so a change to the server address in
Settings is reflected the next time a user (re)downloads their config --
no regeneration step needed."""
from app.openvpn import certs
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
auth SHA256
verb 3
{extra}<ca>
{ca}
</ca>
{identity}<tls-crypt>
{ta}
</tls-crypt>
"""


def _render(proto: str, identity: str, extra: str, key: str = "") -> str:
    if proto not in ("udp", "tcp"):
        raise ValueError("proto must be 'udp' or 'tcp'")

    # Relays (servers inside the country forwarding to this one) come
    # first, this server last; the client moves on to the next address when
    # one doesn't answer within `timeout` seconds.
    hops, timeout = client_remotes(proto, key)
    remotes = "".join(f"remote {address} {port}\n" for address, port in hops)
    if len(hops) > 1:
        remotes += f"server-poll-timeout {timeout}\n"
    # UDP has no connection teardown: without this the server only notices a
    # client left after the keepalive timeout (minutes), so it would keep
    # showing as online and its final traffic would be accounted late.
    if proto == "udp":
        extra += "explicit-exit-notify 2\n"

    return _CLIENT_TEMPLATE.format(
        proto=proto,
        extra=extra,
        remotes=remotes,
        ca=certs.read_ca_cert().strip(),
        identity=identity,
        ta=certs.read_ta_key().strip(),
    )


def build_ovpn(username: str, proto: str, auth_mode: str = "cert") -> str:
    """proto must be 'udp' or 'tcp'. Password-only users get the shared,
    certificate-less profile; the others their own certificate (plus a
    username/password prompt in cert_pass mode)."""
    if auth_mode == "pass":
        return build_shared_ovpn(proto, key=username)

    identity = "<cert>\n{cert}\n</cert>\n<key>\n{key}\n</key>\n".format(
        cert=certs.read_client_cert(username).strip(),
        key=certs.read_client_key(username).strip(),
    )
    extra = "auth-user-pass\n" if auth_mode == "cert_pass" else ""
    return _render(proto, identity, extra, key=username)


def build_shared_ovpn(proto: str, key: str = "") -> str:
    """One profile for every password-only user: no client certificate, the
    app asks for a username and password instead."""
    # Without CLIENT_CERT 0, OpenVPN Connect treats a profile with no
    # <cert>/<key> as "external certificate" and asks the phone's keystore
    # for one; OpenVPN 2.x just ignores the variable.
    extra = "auth-user-pass\nsetenv CLIENT_CERT 0\n"
    return _render(proto, "", extra, key=key)
