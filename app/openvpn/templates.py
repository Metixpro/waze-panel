"""Renders .ovpn client config files on the fly from the CA / issued client
cert / ta.key currently on disk, so a change to the server address in
Settings is reflected the next time a user (re)downloads their config --
no regeneration step needed."""
from app.config import settings
from app.openvpn import certs

_CLIENT_TEMPLATE = """\
client
dev tun
proto {proto}
remote {address} {port}
resolv-retry infinite
nobind
persist-key
persist-tun
remote-cert-tls server
cipher AES-256-GCM
auth SHA256
key-direction 1
verb 3
<ca>
{ca}
</ca>
<cert>
{cert}
</cert>
<key>
{key}
</key>
<tls-crypt>
{ta}
</tls-crypt>
"""


def build_ovpn(username: str, proto: str) -> str:
    """proto must be 'udp' or 'tcp'."""
    if proto not in ("udp", "tcp"):
        raise ValueError("proto must be 'udp' or 'tcp'")

    port = settings.OVPN_UDP_PORT if proto == "udp" else settings.OVPN_TCP_PORT

    return _CLIENT_TEMPLATE.format(
        proto=proto,
        address=settings.SERVER_ADDRESS,
        port=port,
        ca=certs.read_ca_cert().strip(),
        cert=certs.read_client_cert(username).strip(),
        key=certs.read_client_key(username).strip(),
        ta=certs.read_ta_key().strip(),
    )
