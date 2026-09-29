"""Share links (vless:// vmess:// trojan:// ss://) for a user, one per
enabled inbound, and the base64 subscription that v2rayNG, Hiddify,
Streisand, v2rayN... import and refresh on their own."""
import base64
import datetime
import json
from urllib.parse import quote, urlencode

from sqlalchemy.orm import Session

from app.config import settings
from app.models import VpnUser, XrayInbound
from app.xray import core


def _address(opts: dict) -> str:
    return opts.get("link_address") or settings.SERVER_ADDRESS


def _remark(ib: XrayInbound, user: VpnUser, via: str = "") -> str:
    return f"{ib.name} ({via}) | {user.username}" if via else f"{ib.name} | {user.username}"


def _transport_params(ib: XrayInbound, opts: dict) -> dict:
    p = {"type": "tcp" if ib.transport == "raw" else ib.transport}
    if ib.transport in ("ws", "httpupgrade", "xhttp"):
        p["path"] = opts.get("path", "/")
        if opts.get("host"):
            p["host"] = opts["host"]
    if ib.transport == "xhttp":
        p["mode"] = "auto"
    if ib.transport == "grpc":
        p["serviceName"] = opts.get("service_name", "grpc")
        p["mode"] = "gun"
    return p


def _security_params(ib: XrayInbound, opts: dict) -> dict:
    p = {"security": ib.security}
    if ib.security == "reality":
        p.update(sni=opts["sni"], fp=opts.get("fingerprint", "chrome"), pbk=opts["public_key"], sid=opts.get("short_id", ""), spx="/")
    elif ib.security == "tls":
        crt, _key, self_signed = core.tls_files(ib)
        sni = opts.get("sni") or opts.get("host") or settings.SERVER_ADDRESS
        p.update(sni=sni, fp=opts.get("fingerprint", "chrome"))
        if ib.transport in ("grpc", "xhttp"):
            p["alpn"] = "h2,http/1.1"
        if self_signed:
            p["pcs"] = core.cert_pin(crt)
    return p


def link(ib: XrayInbound, user: VpnUser, address: str = "", via: str = "") -> str:
    """Share link for one inbound; `address`/`via` for the copy that goes
    through a relay (same port there)."""
    opts = core.options(ib)
    addr, port, remark = address or _address(opts), ib.port, quote(_remark(ib, user, via))
    host = f"[{addr}]" if ":" in addr else addr

    if ib.protocol == "vless":
        params = {"encryption": "none", **_transport_params(ib, opts), **_security_params(ib, opts)}
        if opts.get("flow"):
            params["flow"] = opts["flow"]
        return f"vless://{user.xray_uuid}@{host}:{port}?{urlencode(params)}#{remark}"

    if ib.protocol == "trojan":
        params = {**_transport_params(ib, opts), **_security_params(ib, opts)}
        return f"trojan://{quote(user.xray_uuid)}@{host}:{port}?{urlencode(params)}#{remark}"

    if ib.protocol == "vmess":
        t = _transport_params(ib, opts)
        doc = {
            "v": "2", "ps": _remark(ib, user, via), "add": addr, "port": str(port), "id": user.xray_uuid, "aid": "0",
            "scy": "auto", "net": t["type"], "type": "gun" if ib.transport == "grpc" else "none",
            "host": t.get("host", ""), "path": t.get("path", t.get("serviceName", "")),
            "tls": "tls" if ib.security == "tls" else "",
        }
        if ib.security == "tls":
            s = _security_params(ib, opts)
            doc.update(sni=s["sni"], fp=s["fp"], alpn=s.get("alpn", ""))
            if s.get("pcs"):
                doc["pcs"] = s["pcs"]
        return "vmess://" + base64.b64encode(json.dumps(doc, ensure_ascii=False).encode()).decode()

    method = opts["method"]
    secret = f"{method}:{opts['server_key']}:{core.ss_user_key(user, method)}"
    userinfo = base64.urlsafe_b64encode(secret.encode()).decode().rstrip("=")
    return f"ss://{userinfo}@{host}:{port}#{remark}"


PROTOCOL_NAMES = {"vless": "VLESS", "vmess": "VMess", "trojan": "Trojan", "shadowsocks": "Shadowsocks"}
TRANSPORT_NAMES = {"raw": "", "ws": "WebSocket", "xhttp": "XHTTP", "grpc": "gRPC", "httpupgrade": "HTTPUpgrade"}
SECURITY_NAMES = {"none": "", "tls": "TLS", "reality": "Reality"}


def label(ib: XrayInbound) -> str:
    """"VLESS · XHTTP · Reality" """
    parts = [PROTOCOL_NAMES[ib.protocol], TRANSPORT_NAMES[ib.transport], SECURITY_NAMES[ib.security]]
    if ib.protocol == "vless" and ib.transport == "raw" and ib.security != "none":
        parts.append("Vision")
    return " · ".join(p for p in parts if p)


def user_links(db: Session, user: VpnUser) -> list[dict]:
    """[{id, name, protocol, label, via, link}] for every inbound the user
    has: through each relay that forwards its port first (the relays'
    order, rotated per user when balancing), then direct -- unless the
    relay options say relays only."""
    from app import relays as relay_lib

    if core.ensure_credentials(user):
        db.commit()
    inbounds = db.query(XrayInbound).filter(XrayInbound.enabled.is_(True)).order_by(XrayInbound.position, XrayInbound.id).all()
    relayed = {ib.id for ib in relay_lib.relayed_inbounds(db)}
    hops = relay_lib.xray_relays(db, user.username)
    direct_too = relay_lib.get_options(db)["fallback_direct"]
    out = []
    for ib in inbounds:
        if not user.uses_inbound(ib.id):
            continue
        base = {"id": ib.id, "name": ib.name, "protocol": ib.protocol, "label": label(ib)}
        via = [(r, ports) for r, ports in hops if ib.id in relayed and ib.port in ports]
        for relay, _ports in via:
            out.append({**base, "via": relay.name, "link": link(ib, user, address=relay.address, via=relay.name)})
        if not via or direct_too:
            out.append({**base, "via": "", "link": link(ib, user)})
    return out


def subscription(db: Session, user: VpnUser) -> tuple[str, dict]:
    """(body, headers) of the subscription the apps download."""
    links = [l["link"] for l in user_links(db, user)] if user.xray_enabled else []
    body = base64.b64encode("\n".join(links).encode()).decode()
    expire = int(user.expire_at.replace(tzinfo=datetime.timezone.utc).timestamp()) if user.expire_at else 0
    title = f"{settings.PANEL_TITLE} · {user.username}"
    headers = {
        "profile-title": "base64:" + base64.b64encode(title.encode()).decode(),
        "profile-update-interval": "12",
        "subscription-userinfo": f"upload=0; download={user.data_used_bytes or 0}; total={user.data_limit_bytes or 0}; expire={expire}",
        "profile-web-page-url": f"{settings.public_base_url}/sub/{user.token}",
        "content-disposition": f'attachment; filename="{user.username}"',
    }
    return body, headers
