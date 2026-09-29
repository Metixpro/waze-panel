"""
Central configuration for Waze Panel.

All runtime settings are read from environment variables which, in a
production install, live in /etc/waze-panel/panel.env and are loaded by the
systemd unit (EnvironmentFile=). For local development a plain .env file in
the project root is also picked up.
"""
import os
import secrets
from pathlib import Path

from dotenv import load_dotenv

# Load /etc/waze-panel/panel.env first (production), then fall back to a
# local .env next to the project (development). Values already present in
# the real environment always win.
_PROD_ENV_FILE = Path("/etc/waze-panel/panel.env")
if _PROD_ENV_FILE.exists():
    load_dotenv(_PROD_ENV_FILE, override=False)
else:
    load_dotenv(Path(__file__).resolve().parent.parent / ".env", override=False)


def _get_bool(name: str, default: bool) -> bool:
    val = os.getenv(name)
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes", "on")


class Settings:
    # --- Core ---
    APP_NAME: str = "Waze Panel"
    SECRET_KEY: str = os.getenv("SECRET_KEY", secrets.token_hex(32))
    INTERNAL_TOKEN: str = os.getenv("INTERNAL_TOKEN", secrets.token_hex(32))
    ENV: str = os.getenv("ENV", "production")
    DEBUG: bool = _get_bool("DEBUG", False)

    # --- Paths ---
    DATA_DIR: Path = Path(os.getenv("DATA_DIR", "/etc/waze-panel"))
    DB_PATH: Path = Path(os.getenv("DB_PATH", str(DATA_DIR / "waze-panel.db")))

    OPENVPN_DIR: Path = Path(os.getenv("OPENVPN_DIR", "/etc/openvpn"))
    OPENVPN_SERVER_DIR: Path = Path(
        os.getenv("OPENVPN_SERVER_DIR", str(OPENVPN_DIR / "server"))
    )
    EASYRSA_DIR: Path = Path(os.getenv("EASYRSA_DIR", str(OPENVPN_DIR / "easy-rsa")))
    EASYRSA_PKI_DIR: Path = Path(os.getenv("EASYRSA_PKI_DIR", str(EASYRSA_DIR / "pki")))

    # --- Network / OpenVPN instances ---
    SERVER_ADDRESS: str = os.getenv("SERVER_ADDRESS", "YOUR_SERVER_IP")
    OVPN_UDP_PORT: int = int(os.getenv("OVPN_UDP_PORT", "1194"))
    OVPN_TCP_PORT: int = int(os.getenv("OVPN_TCP_PORT", "443"))
    OVPN_UDP_MGMT_PORT: int = int(os.getenv("OVPN_UDP_MGMT_PORT", "7505"))
    OVPN_TCP_MGMT_PORT: int = int(os.getenv("OVPN_TCP_MGMT_PORT", "7506"))
    OVPN_UDP_SUBNET: str = os.getenv("OVPN_UDP_SUBNET", "10.8.0.0")
    OVPN_TCP_SUBNET: str = os.getenv("OVPN_TCP_SUBNET", "10.9.0.0")
    OVPN_SUBNET_MASK: str = os.getenv("OVPN_SUBNET_MASK", "255.255.255.0")
    # systemd template the two instances run under (openvpn-server@ or openvpn@)
    OVPN_SERVICE_PREFIX: str = os.getenv("OVPN_SERVICE_PREFIX", "openvpn-server@")
    # loopback-only port of the built-in HTTPS cover site (app/cover.py)
    COVER_PORT: int = int(os.getenv("COVER_PORT", "7543"))

    # --- Xray-core (app/xray) ---
    XRAY_HOME: Path = Path(os.getenv("XRAY_HOME", "/usr/local/share/waze-panel/xray"))
    XRAY_API_PORT: int = int(os.getenv("XRAY_API_PORT", "10085"))
    XRAY_SERVICE: str = os.getenv("XRAY_SERVICE", "waze-xray")

    # --- Web panel ---
    PANEL_PORT: int = int(os.getenv("PANEL_PORT", "8000"))
    PANEL_TITLE: str = os.getenv("PANEL_TITLE", "Waze Panel")
    SUBSCRIPTION_BASE_URL: str = os.getenv("SUBSCRIPTION_BASE_URL", "")

    # --- Traffic accounting ---
    TRAFFIC_POLL_INTERVAL_SECONDS: int = int(
        os.getenv("TRAFFIC_POLL_INTERVAL_SECONDS", "20")
    )

    @property
    def public_base_url(self) -> str:
        if self.SUBSCRIPTION_BASE_URL:
            return self.SUBSCRIPTION_BASE_URL.rstrip("/")
        return f"http://{self.SERVER_ADDRESS}:{self.PANEL_PORT}"


settings = Settings()

# Make sure the data directory exists even in dev.
settings.DATA_DIR.mkdir(parents=True, exist_ok=True)
