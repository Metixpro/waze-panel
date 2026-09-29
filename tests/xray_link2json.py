#!/usr/bin/env python3
"""Turn a share link (vless:// vmess:// trojan:// ss://) into an Xray client
config with a local SOCKS inbound -- how tests/e2e.sh checks that the links
the panel hands out actually connect.

    xray_link2json.py <link> <socks port> [--address HOST]   > client.json
"""
import base64
import json
import sys
from urllib.parse import parse_qs, unquote, urlsplit


def b64d(s: str) -> bytes:
    s = s.strip()
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def stream(q: dict, net: str, security: str) -> dict:
    s: dict = {"network": "raw" if net in ("tcp", "raw") else net, "security": security}
    if net == "ws":
        s["wsSettings"] = {"path": q.get("path", "/"), "host": q.get("host", "")}
    elif net == "httpupgrade":
        s["httpupgradeSettings"] = {"path": q.get("path", "/"), "host": q.get("host", "")}
    elif net == "xhttp":
        s["xhttpSettings"] = {"path": q.get("path", "/"), "host": q.get("host", ""), "mode": q.get("mode", "auto")}
    elif net == "grpc":
        s["grpcSettings"] = {"serviceName": q.get("serviceName", "")}
    if security == "reality":
        s["realitySettings"] = {"serverName": q.get("sni", ""), "fingerprint": q.get("fp", "chrome"),
                                "publicKey": q["pbk"], "shortId": q.get("sid", ""), "spiderX": q.get("spx", "/")}
    elif security == "tls":
        t = {"serverName": q.get("sni", ""), "fingerprint": q.get("fp", "chrome")}
        if q.get("alpn"):
            t["alpn"] = q["alpn"].split(",")
        if q.get("pcs"):
            t["pinnedPeerCertSha256"] = q["pcs"]
        s["tlsSettings"] = t
    return s


def outbound(link: str, address: str | None) -> dict:
    scheme = link.split("://", 1)[0]
    if scheme == "vmess":
        d = json.loads(b64d(link[8:]))
        q = {"path": d.get("path", ""), "host": d.get("host", ""), "sni": d.get("sni", ""), "fp": d.get("fp", "chrome"),
             "alpn": d.get("alpn", ""), "pcs": d.get("pcs", ""), "serviceName": d.get("path", "")}
        return {"protocol": "vmess",
                "settings": {"vnext": [{"address": address or d["add"], "port": int(d["port"]),
                                        "users": [{"id": d["id"], "security": d.get("scy", "auto")}]}]},
                "streamSettings": stream(q, d.get("net", "tcp"), "tls" if d.get("tls") == "tls" else "none")}
    u = urlsplit(link)
    q = {k: v[0] for k, v in parse_qs(u.query).items()}
    host = address or u.hostname
    if scheme == "vless":
        user = {"id": unquote(u.username), "encryption": q.get("encryption", "none")}
        if q.get("flow"):
            user["flow"] = q["flow"]
        return {"protocol": "vless", "settings": {"vnext": [{"address": host, "port": u.port, "users": [user]}]},
                "streamSettings": stream(q, q.get("type", "tcp"), q.get("security", "none"))}
    if scheme == "trojan":
        return {"protocol": "trojan", "settings": {"servers": [{"address": host, "port": u.port, "password": unquote(u.username)}]},
                "streamSettings": stream(q, q.get("type", "tcp"), q.get("security", "tls"))}
    if scheme == "ss":
        method, password = b64d(unquote(u.username)).decode().split(":", 1)
        return {"protocol": "shadowsocks", "settings": {"servers": [{"address": host, "port": u.port, "method": method, "password": password}]}}
    raise SystemExit(f"unsupported link: {scheme}")


def main() -> None:
    link, port = sys.argv[1], int(sys.argv[2])
    address = sys.argv[sys.argv.index("--address") + 1] if "--address" in sys.argv else None
    print(json.dumps({
        "log": {"loglevel": "warning"},
        "inbounds": [{"listen": "127.0.0.1", "port": port, "protocol": "socks", "settings": {"udp": False}}],
        "outbounds": [outbound(link, address)],
    }, indent=2))


if __name__ == "__main__":
    main()
