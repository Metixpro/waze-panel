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


def clash_subscription(db: Session, user: VpnUser) -> tuple[str, dict]:
    """Generates a full Clash Meta / Mihomo compatible YAML configuration
    including auto-fallback/url-test groups and rules."""
    import yaml
    from app import relays as relay_lib

    if core.ensure_credentials(user):
        db.commit()

    inbounds = db.query(XrayInbound).filter(XrayInbound.enabled.is_(True)).order_by(XrayInbound.position, XrayInbound.id).all()
    relayed = {ib.id for ib in relay_lib.relayed_inbounds(db)}
    hops = relay_lib.xray_relays(db, user.username)
    direct_too = relay_lib.get_options(db)["fallback_direct"]

    proxies = []
    seen_names = set()

    def _unique_name(base: str) -> str:
        name = base
        count = 2
        while name in seen_names:
            name = f"{base} ({count})"
            count += 1
        seen_names.add(name)
        return name

    for ib in inbounds:
        if not user.uses_inbound(ib.id):
            continue
        opts = core.options(ib)
        via_list = [(r, ports) for r, ports in hops if ib.id in relayed and ib.port in ports]

        targets = []
        for relay, _ports in via_list:
            targets.append((relay.address, relay.name))
        if not via_list or direct_too:
            targets.append((_address(opts), ""))

        for addr, via_name in targets:
            name_label = f"{ib.name} ({via_name})" if via_name else ib.name
            proxy_name = _unique_name(name_label)
            crt, _key, self_signed = core.tls_files(ib) if ib.security == "tls" else (None, None, False)

            p = None
            if ib.protocol == "vless":
                p = {
                    "name": proxy_name,
                    "type": "vless",
                    "server": addr,
                    "port": ib.port,
                    "uuid": user.xray_uuid,
                    "udp": True,
                    "tls": (ib.security in ("tls", "reality")),
                    "skip-cert-verify": bool(self_signed),
                    "network": "ws" if ib.transport == "ws" else ("grpc" if ib.transport == "grpc" else ("xhttp" if ib.transport == "xhttp" else "tcp")),
                }
                if ib.security == "reality":
                    p["servername"] = opts.get("sni", "")
                    p["reality-opts"] = {
                        "public-key": opts.get("public_key", ""),
                        "short-id": opts.get("short_id", ""),
                    }
                    p["client-fingerprint"] = opts.get("fingerprint", "chrome")
                    if opts.get("flow"):
                        p["flow"] = opts["flow"]
                elif ib.security == "tls":
                    p["servername"] = opts.get("sni") or opts.get("host") or settings.SERVER_ADDRESS
                    p["client-fingerprint"] = opts.get("fingerprint", "chrome")
                    if opts.get("alpn"):
                        p["alpn"] = opts["alpn"].split(",")

                if ib.transport == "ws":
                    p["ws-opts"] = {
                        "path": opts.get("path", "/"),
                        "headers": {"Host": opts.get("host", "")} if opts.get("host") else {},
                    }
                elif ib.transport == "grpc":
                    p["grpc-opts"] = {
                        "grpc-service-name": opts.get("service_name", "grpc"),
                    }

            elif ib.protocol == "trojan":
                p = {
                    "name": proxy_name,
                    "type": "trojan",
                    "server": addr,
                    "port": ib.port,
                    "password": user.xray_uuid,
                    "udp": True,
                    "skip-cert-verify": bool(self_signed),
                    "sni": opts.get("sni") or opts.get("host") or settings.SERVER_ADDRESS,
                    "network": "ws" if ib.transport == "ws" else ("grpc" if ib.transport == "grpc" else "tcp"),
                }
                if ib.transport == "ws":
                    p["ws-opts"] = {
                        "path": opts.get("path", "/"),
                        "headers": {"Host": opts.get("host", "")} if opts.get("host") else {},
                    }
                elif ib.transport == "grpc":
                    p["grpc-opts"] = {
                        "grpc-service-name": opts.get("service_name", "grpc"),
                    }

            elif ib.protocol == "vmess":
                p = {
                    "name": proxy_name,
                    "type": "vmess",
                    "server": addr,
                    "port": ib.port,
                    "uuid": user.xray_uuid,
                    "alterId": 0,
                    "cipher": "auto",
                    "udp": True,
                    "tls": (ib.security == "tls"),
                    "skip-cert-verify": bool(self_signed),
                    "network": "ws" if ib.transport == "ws" else ("grpc" if ib.transport == "grpc" else "tcp"),
                }
                if ib.security == "tls":
                    p["servername"] = opts.get("sni") or opts.get("host") or settings.SERVER_ADDRESS
                if ib.transport == "ws":
                    p["ws-opts"] = {
                        "path": opts.get("path", "/"),
                        "headers": {"Host": opts.get("host", "")} if opts.get("host") else {},
                    }
                elif ib.transport == "grpc":
                    p["grpc-opts"] = {
                        "grpc-service-name": opts.get("service_name", "grpc"),
                    }

            elif ib.protocol == "shadowsocks":
                method = opts["method"]
                user_key = core.ss_user_key(user, method)
                p = {
                    "name": proxy_name,
                    "type": "ss",
                    "server": addr,
                    "port": ib.port,
                    "cipher": method,
                    "password": f"{opts['server_key']}:{user_key}",
                    "udp": True,
                }

            if p:
                proxies.append(p)

    proxy_names = [p["name"] for p in proxies]
    auto_proxies = list(proxy_names) if proxy_names else ["DIRECT"]
    select_proxies = ["⚡ AUTO - خودکار سریع‌ترین"] + proxy_names + ["DIRECT"]

    clash_doc = {
        "port": 7890,
        "socks-port": 7891,
        "allow-lan": False,
        "mode": "rule",
        "log-level": "info",
        "unified-delay": True,
        "tcp-concurrent": True,
        "dns": {
            "enable": True,
            "listen": "0.0.0.0:1053",
            "ipv6": False,
            "enhanced-mode": "fake-ip",
            "fake-ip-range": "198.18.0.1/16",
            "nameserver": ["1.1.1.1", "8.8.8.8"],
        },
        "proxies": proxies,
        "proxy-groups": [
            {
                "name": "⚡ AUTO - خودکار سریع‌ترین",
                "type": "url-test",
                "url": "http://www.gstatic.com/generate_204",
                "interval": 300,
                "tolerance": 50,
                "proxies": auto_proxies,
            },
            {
                "name": "🚀 PROXY - دستی",
                "type": "select",
                "proxies": select_proxies,
            },
            {
                "name": "🇮🇷 IRAN - داخلی",
                "type": "select",
                "proxies": ["DIRECT", "🚀 PROXY - دستی"],
            },
        ],
        "rules": [
            "GEOIP,private,DIRECT",
            "GEOIP,IR,🇮🇷 IRAN - داخلی",
            "GEOSITE,category-ir,🇮🇷 IRAN - داخلی",
            "MATCH,🚀 PROXY - دستی",
        ],
    }

    yaml_text = yaml.dump(clash_doc, allow_unicode=True, sort_keys=False)
    expire = int(user.expire_at.replace(tzinfo=datetime.timezone.utc).timestamp()) if user.expire_at else 0
    title = f"{settings.PANEL_TITLE} · {user.username}"
    headers = {
        "profile-title": "base64:" + base64.b64encode(title.encode()).decode(),
        "profile-update-interval": "12",
        "subscription-userinfo": f"upload=0; download={user.data_used_bytes or 0}; total={user.data_limit_bytes or 0}; expire={expire}",
        "profile-web-page-url": f"{settings.public_base_url}/sub/{user.token}",
        "content-disposition": f'attachment; filename="{user.username}.yaml"',
    }
    return yaml_text, headers
