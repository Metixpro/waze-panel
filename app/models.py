import datetime
import secrets
import uuid

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


def _utcnow() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


# cert       certificate only (the classic per-user .ovpn)
# cert_pass  certificate AND username/password (two factors)
# pass       username/password only: every such user can share one
#            certificate-less config file
AUTH_MODES = ("cert", "cert_pass", "pass")

# OpenVPN replaces anything outside printable ASCII in passwords, and spaces
# only confuse people typing them into a phone, so keep to this set.
PASSWORD_ALPHABET = "abcdefghjkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def generate_vpn_password(length: int = 10) -> str:
    return "".join(secrets.choice(PASSWORD_ALPHABET) for _ in range(length))


def find_vpn_user(db, name: str) -> "VpnUser | None":
    """Look a user up by CN / login name: exact match first, then
    case-insensitively (phone keyboards like to capitalise the first
    letter of a typed username)."""
    if not name:
        return None
    user = db.query(VpnUser).filter(VpnUser.username == name).first()
    if user is None:
        user = db.query(VpnUser).filter(func.lower(VpnUser.username) == name.lower()).first()
    return user


class AdminUser(Base):
    __tablename__ = "admin_users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=_utcnow)
    last_login_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime, nullable=True
    )


class VpnUser(Base):
    """A single OpenVPN client identity (one certificate, usable over both
    the UDP and the TCP instance)."""

    __tablename__ = "vpn_users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    # Public, unguessable token used in the subscription link. Never the
    # username itself, so links can be shared without exposing the cert CN.
    token: Mapped[str] = mapped_column(
        String(64), unique=True, index=True, default=lambda: uuid.uuid4().hex
    )

    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    note: Mapped[str | None] = mapped_column(String(255), nullable=True)

    data_limit_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    data_used_bytes: Mapped[int] = mapped_column(BigInteger, default=0)

    expire_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime, nullable=True
    )

    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=_utcnow)
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)
    revoked_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime, nullable=True
    )

    # last time we saw this CN connected on either instance (for "online now")
    last_connected_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime, nullable=True
    )
    last_ip: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # How the client proves who it is (see AUTH_MODES). Every user still gets
    # a certificate, so switching modes never needs a new one.
    auth_mode: Mapped[str] = mapped_column(String(16), default="cert", server_default="cert")
    # Kept recoverable on purpose: the admin has to be able to show it to the
    # user again, and OpenVPN clients send it in clear inside the TLS tunnel
    # anyway. The database file is root-only (0600).
    auth_password: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # Simultaneous connections allowed across UDP+TCP; 0 = unlimited. The
    # newest connection wins, older ones are disconnected.
    max_devices: Mapped[int] = mapped_column(Integer, default=0, server_default="0")

    # Personal tls-crypt-v2 key (app/openvpn/tlscrypt.py). The id is sealed
    # inside the key and checked on every connection, so issuing a new key
    # retires every copy of the old config at once.
    tls_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    tls_key_id: Mapped[str | None] = mapped_column(String(16), nullable=True)
    tls_key_seen_at: Mapped[datetime.datetime | None] = mapped_column(DateTime, nullable=True)

    # Xray (app/xray): one UUID for VLESS/VMess/Trojan and a random key for
    # Shadowsocks 2022, made on first use. A new pair retires every old link.
    xray_enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default="1")
    xray_uuid: Mapped[str | None] = mapped_column(String(36), nullable=True)
    xray_key: Mapped[str | None] = mapped_column(String(64), nullable=True)

    @property
    def needs_password(self) -> bool:
        return self.auth_mode in ("cert_pass", "pass")

    def is_over_quota(self) -> bool:
        return (
            self.data_limit_bytes is not None
            and self.data_used_bytes >= self.data_limit_bytes
        )

    def is_expired(self) -> bool:
        if self.expire_at is None:
            return False
        return _utcnow() > self.expire_at.replace(tzinfo=datetime.timezone.utc)

    def is_usable(self) -> bool:
        return (
            self.enabled
            and not self.revoked
            and not self.is_expired()
            and not self.is_over_quota()
        )

    def status_label(self) -> str:
        if self.revoked:
            return "revoked"
        if not self.enabled:
            return "disabled"
        if self.is_expired():
            return "expired"
        if self.is_over_quota():
            return "over_quota"
        return "active"

    def days_left(self) -> int | None:
        if self.expire_at is None:
            return None
        delta = self.expire_at.replace(tzinfo=datetime.timezone.utc) - _utcnow()
        return max(0, delta.days + (1 if delta.seconds > 0 else 0))

    def is_ending_soon(self) -> bool:
        """Active but about to run out: <= 3 days or >= 85% of its quota."""
        if self.status_label() != "active":
            return False
        days = self.days_left()
        if days is not None and days <= 3:
            return True
        return bool(self.data_limit_bytes) and self.data_used_bytes >= 0.85 * self.data_limit_bytes

    def regenerate_token(self) -> None:
        self.token = uuid.uuid4().hex


class TrafficSample(Base):
    """Daily traffic aggregate per user, used to draw the dashboard chart
    and per-user history. One row per (vpn_user, date)."""

    __tablename__ = "traffic_samples"
    __table_args__ = (UniqueConstraint("vpn_user_id", "date", name="uq_user_date"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    vpn_user_id: Mapped[int] = mapped_column(ForeignKey("vpn_users.id"), index=True)
    date: Mapped[datetime.date] = mapped_column(Date, index=True)
    bytes_total: Mapped[int] = mapped_column(BigInteger, default=0)

    vpn_user: Mapped["VpnUser"] = relationship()


class Setting(Base):
    """Simple key/value store for panel-editable settings (server address,
    panel title, ...)."""

    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text)


class XrayInbound(Base):
    """One Xray listener (protocol + transport + security on a port). Every
    user with Xray access is a client of every enabled inbound; the rest of
    the options (paths, REALITY keys, ...) live in the JSON `options`."""

    __tablename__ = "xray_inbounds"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(40))
    protocol: Mapped[str] = mapped_column(String(16))      # vless vmess trojan shadowsocks
    transport: Mapped[str] = mapped_column(String(16))     # raw ws xhttp grpc httpupgrade
    security: Mapped[str] = mapped_column(String(16))      # none tls reality
    port: Mapped[int] = mapped_column(Integer)
    options: Mapped[str] = mapped_column(Text, default="{}")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    position: Mapped[int] = mapped_column(Integer, default=0)
    traffic_bytes: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=_utcnow)


class RelayServer(Base):
    """A server inside the country that forwards OpenVPN traffic to this
    one (set up there with relay.sh). Client configs list the enabled
    relays first, in `position` order, so users keep connecting through a
    domestic address when direct international routes are throttled or
    cut, with the direct address as the last fallback."""

    __tablename__ = "relay_servers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(40))
    address: Mapped[str] = mapped_column(String(253))
    # ports the relay listens on (usually the same as this server's)
    udp_port: Mapped[int] = mapped_column(Integer)
    tcp_port: Mapped[int] = mapped_column(Integer)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    position: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=_utcnow)
    # The address the relay's forwarded traffic arrives from, learned by the
    # health check (differs from `address` on multi-IP relays); lets the
    # panel say "via <relay>" instead of showing the relay's IP.
    source_ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
