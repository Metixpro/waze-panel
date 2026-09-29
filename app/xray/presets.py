"""What an inbound may look like: allowed protocol/transport/security
combinations, the ready-made presets shown in the panel, and validation of
what the admin fills in."""
import re

PROTOCOLS = ("vless", "vmess", "trojan", "shadowsocks")
TRANSPORTS = ("raw", "ws", "xhttp", "grpc", "httpupgrade")
SECURITIES = ("none", "tls", "reality")

ALLOWED = {
    "vless": {"raw": ("reality", "tls", "none"), "xhttp": ("reality", "tls", "none"), "grpc": ("reality", "tls", "none"),
              "ws": ("tls", "none"), "httpupgrade": ("tls", "none")},
    "vmess": {"ws": ("tls", "none"), "raw": ("tls", "none"), "grpc": ("tls", "none"), "httpupgrade": ("tls", "none"),
              "xhttp": ("tls", "none")},
    "trojan": {"raw": ("tls",), "ws": ("tls",), "grpc": ("tls",)},
    "shadowsocks": {"raw": ("none",)},
}

SS_METHODS = ("2022-blake3-aes-128-gcm", "2022-blake3-aes-256-gcm", "2022-blake3-chacha20-poly1305")

# Sites that speak TLS 1.3 + HTTP/2 and stay reachable from Iran: what a
# REALITY inbound impersonates (the admin can type any other one).
REALITY_TARGETS = ("www.microsoft.com", "www.apple.com", "www.samsung.com", "www.speedtest.net", "addons.mozilla.org", "www.yahoo.com")

PRESETS = [
    {
        "id": "vless-reality", "name": "Reality", "protocol": "vless", "transport": "raw", "security": "reality",
        "label": "VLESS · Reality · Vision", "badge": "پیشنهادی",
        "desc": "بدون دامنه و گواهی؛ از بیرون شبیه اتصال به یک سایت معروف است. بهترین انتخاب برای بیشتر شبکه‌ها.",
    },
    {
        "id": "vless-xhttp-reality", "name": "XHTTP", "protocol": "vless", "transport": "xhttp", "security": "reality",
        "label": "VLESS · XHTTP · Reality", "badge": "نسل جدید",
        "desc": "روش تازه‌ی Xray که ترافیک را در قالب درخواست‌های HTTP معمولی می‌فرستد؛ روی شبکه‌های سخت‌گیر پایدارتر است.",
    },
    {
        "id": "vless-ws", "name": "CDN", "protocol": "vless", "transport": "ws", "security": "none",
        "label": "VLESS · WebSocket", "badge": "برای CDN",
        "desc": "برای عبور از پشت CDN (مثل کلادفلر) با یک دامنه. اگر IP سرور فیلتر شد، از این راه وصل می‌شوند.",
    },
    {
        "id": "vmess-ws", "name": "VMess", "protocol": "vmess", "transport": "ws", "security": "none",
        "label": "VMess · WebSocket", "badge": "سازگاری",
        "desc": "برای اپ‌های قدیمی‌تر که VLESS ندارند.",
    },
    {
        "id": "trojan-tls", "name": "Trojan", "protocol": "trojan", "transport": "raw", "security": "tls",
        "label": "Trojan · TLS", "badge": "سازگاری",
        "desc": "با گواهی دامنه‌ی خودتان؛ بدون دامنه، گواهی خودامضا ساخته و داخل لینک‌ها سنجاق می‌شود.",
    },
    {
        "id": "shadowsocks", "name": "Shadowsocks", "protocol": "shadowsocks", "transport": "raw", "security": "none",
        "label": "Shadowsocks 2022", "badge": "سازگاری",
        "desc": "ساده و سبک؛ برای بعضی روترها و اپ‌هایی که فقط Shadowsocks دارند.",
    },
]

_PATH_RE = re.compile(r"^/[A-Za-z0-9._~\-/]{0,63}$")
_HOST_RE = re.compile(r"^[A-Za-z0-9.\-]{1,253}$")
_NAME_RE = re.compile(r"^[A-Za-z0-9_\-]{1,32}$")
_SID_RE = re.compile(r"^[0-9a-f]{0,16}$")
_TARGET_RE = re.compile(r"^[a-z0-9.\-]{1,253}:\d{1,5}$")


class InboundError(ValueError):
    pass


def check_combo(protocol: str, transport: str, security: str) -> None:
    if protocol not in ALLOWED:
        raise InboundError("پروتکل نامعتبر است.")
    if transport not in ALLOWED[protocol]:
        raise InboundError(f"{protocol.upper()} با انتقال {transport} پشتیبانی نمی‌شود.")
    if security not in ALLOWED[protocol][transport]:
        raise InboundError(f"{protocol.upper()} روی {transport} با امنیت {security} پشتیبانی نمی‌شود.")


def clean_options(protocol: str, transport: str, security: str, raw: dict) -> dict:
    """Keep only the options that make sense for this combination, checked."""
    out: dict = {}
    if transport in ("ws", "xhttp", "httpupgrade"):
        path = (raw.get("path") or "/").strip()
        if not _PATH_RE.match(path):
            raise InboundError("مسیر باید با / شروع شود و فقط حروف و اعداد انگلیسی داشته باشد.")
        out["path"] = path
    if transport == "grpc":
        name = (raw.get("service_name") or "grpc").strip()
        if not _NAME_RE.match(name):
            raise InboundError("نام سرویس gRPC فقط حروف و اعداد انگلیسی.")
        out["service_name"] = name
    if transport in ("ws", "xhttp", "httpupgrade", "grpc") or security == "tls":
        host = (raw.get("host") or "").strip().lower()
        if host and not _HOST_RE.match(host):
            raise InboundError("دامنه نامعتبر است.")
        out["host"] = host
    link_address = (raw.get("link_address") or "").strip().lower()
    if link_address and not _HOST_RE.match(link_address):
        raise InboundError("آدرس داخل لینک باید IP یا دامنه باشد.")
    out["link_address"] = link_address

    if security == "reality":
        sni = (raw.get("sni") or REALITY_TARGETS[0]).strip().lower()
        if not _HOST_RE.match(sni):
            raise InboundError("سایت هدف REALITY باید یک دامنه باشد.")
        out["sni"] = sni
        # where REALITY forwards strangers; normally the same site on 443
        target = (raw.get("target") or "").strip().lower()
        if target and target != f"{sni}:443":
            if not _TARGET_RE.match(target):
                raise InboundError("مقصد REALITY باید به شکل دامنه:پورت باشد.")
            out["target"] = target
        sid = (raw.get("short_id") or "").strip().lower()
        if not _SID_RE.match(sid) or len(sid) % 2:
            raise InboundError("Short ID باید هگز با طول زوج (تا ۱۶ کاراکتر) باشد.")
        out["short_id"] = sid
        out["private_key"] = raw.get("private_key") or ""
        out["public_key"] = raw.get("public_key") or ""
    if security == "tls":
        sni = (raw.get("sni") or out.get("host") or "").strip().lower()
        if sni and not _HOST_RE.match(sni):
            raise InboundError("دامنه‌ی TLS نامعتبر است.")
        out["sni"] = sni
    if security in ("reality", "tls"):
        fp = raw.get("fingerprint") or "chrome"
        out["fingerprint"] = fp if fp in ("chrome", "firefox", "safari", "edge", "ios", "android", "random") else "chrome"
    if protocol == "vless" and transport == "raw" and security in ("reality", "tls"):
        out["flow"] = "xtls-rprx-vision"
    if protocol == "shadowsocks":
        method = raw.get("method") or SS_METHODS[0]
        if method not in SS_METHODS:
            raise InboundError("روش رمزنگاری Shadowsocks نامعتبر است.")
        out["method"] = method
        out["server_key"] = raw.get("server_key") or ""
    return out
