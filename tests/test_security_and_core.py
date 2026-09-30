import os
import tempfile
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

# Configure environment before app imports
os.environ["ENV"] = "testing"
os.environ["DEBUG"] = "true"
os.environ["COOKIE_SECURE"] = "false"
os.environ["SECRET_KEY"] = "test-secret-key-12345678901234567890"

from app.version import __version__
from app.security import hash_password, verify_password
from app.database import engine, init_db, SessionLocal
from app.models import AdminUser, Setting
from app.deps import SESSION_KEY, SESSION_VERSION_KEY, get_current_admin, get_optional_admin
from app.openvpn.mgmt import _get_mgmt_lock
from app.routers import auth as auth_router
from app.routers import xray as xray_router
from app.main import app


def test_version_bump():
    assert __version__ == "2.2.0"


def test_password_hashing():
    pw = "super-secret-password-123"
    hashed = hash_password(pw)
    assert hashed != pw
    assert verify_password(pw, hashed)
    assert not verify_password("wrong-password", hashed)


def test_database_init_and_wal_mode():
    init_db()
    with engine.connect() as conn:
        result = conn.exec_driver_sql("PRAGMA journal_mode;").scalar()
        # In SQLite, WAL mode should be active or memory/delete depending on driver, but WAL was set
        assert result.lower() in ("wal", "memory")


def test_security_headers():
    client = TestClient(app)
    response = client.get("/login")
    assert response.headers.get("X-Content-Type-Options") == "nosniff"
    assert response.headers.get("X-Frame-Options") == "DENY"
    assert "strict-origin" in response.headers.get("Referrer-Policy", "")


def test_session_invalidation():
    init_db()
    db = SessionLocal()
    try:
        # Create test admin
        admin = db.query(AdminUser).filter(AdminUser.username == "test_session_admin").first()
        if not admin:
            admin = AdminUser(
                username="test_session_admin",
                password_hash=hash_password("adminpass123"),
                session_version=1,
            )
            db.add(admin)
            db.commit()
            db.refresh(admin)

        client = TestClient(app)

        # Login successfully
        resp = client.post(
            "/login",
            data={"username": "test_session_admin", "password": "adminpass123"},
            follow_redirects=False,
        )
        assert resp.status_code == 302
        assert "waze_panel_session" in resp.cookies

        # Subsequent dashboard request should succeed (redirects to /dashboard or gives 200)
        resp2 = client.get("/dashboard", follow_redirects=False)
        assert resp2.status_code in (200, 302)

        # Simulate password change / session invalidation by incrementing session_version in DB
        admin.session_version += 1
        db.commit()

        # The old session cookie should now be rejected as unauthorized
        resp3 = client.get("/dashboard", follow_redirects=False)
        assert resp3.status_code == 302
        assert "/login" in resp3.headers.get("location", "")
    finally:
        db.close()


def test_failures_memory_pruning():
    auth_router._failures.clear()
    now = 100000.0
    # Insert expired entries
    for i in range(100):
        auth_router._failures[f"192.0.2.{i}"] = [now - 1000.0]

    # Insert active entries
    auth_router._failures["192.0.2.200"] = [now - 10.0]

    # Force threshold lower temporarily to test pruning
    old_max = auth_router.MAX_FAILURES_ENTRIES
    try:
        auth_router.MAX_FAILURES_ENTRIES = 50
        attempts = auth_router._recent_failures("192.0.2.200", now)
        assert len(attempts) == 1
        # Expired entries should have been pruned
        assert len(auth_router._failures) < 50
    finally:
        auth_router.MAX_FAILURES_ENTRIES = old_max
        auth_router._failures.clear()


def test_openvpn_mgmt_locks():
    lock1 = _get_mgmt_lock(7505)
    lock2 = _get_mgmt_lock(7505)
    lock3 = _get_mgmt_lock(7506)
    assert lock1 is lock2
    assert lock1 is not lock3


def test_ssrf_protection_in_check_target():
    client = TestClient(app)
    # Login as admin first
    init_db()
    db = SessionLocal()
    try:
        admin = db.query(AdminUser).filter(AdminUser.username == "test_ssrf_admin").first()
        if not admin:
            admin = AdminUser(
                username="test_ssrf_admin",
                password_hash=hash_password("adminpass123"),
                session_version=1,
            )
            db.add(admin)
            db.commit()

        client.post(
            "/login",
            data={"username": "test_ssrf_admin", "password": "adminpass123"},
            follow_redirects=False,
        )

        # Attempt to target localhost / 127.0.0.1
        resp = client.post("/api/xray/check-target", json={"sni": "127.0.0.1"})
        assert resp.status_code == 400
        assert "خصوصی" in resp.json().get("detail", "")

        # Attempt to target private RFC1918 range
        resp_priv = client.post("/api/xray/check-target", json={"sni": "10.0.0.1"})
        assert resp_priv.status_code == 400
        assert "خصوصی" in resp_priv.json().get("detail", "")
    finally:
        db.close()


def test_atomic_env_sync(tmp_path, monkeypatch):
    from app import settings_store
    fake_env = tmp_path / "panel.env"
    fake_env.write_text('SERVER_ADDRESS="1.2.3.4"\nPANEL_TITLE="Old"\n', encoding="utf-8")
    monkeypatch.setattr(settings_store, "_ENV_FILE", fake_env)

    settings_store._sync_env_file("PANEL_TITLE", "NewTitle")
    content = fake_env.read_text(encoding="utf-8")
    assert 'PANEL_TITLE="NewTitle"' in content
    assert 'SERVER_ADDRESS="1.2.3.4"' in content

    # Check adding new key
    settings_store._sync_env_file("SUBSCRIPTION_BASE_URL", "https://sub.example.com")
    content = fake_env.read_text(encoding="utf-8")
    assert 'SUBSCRIPTION_BASE_URL="https://sub.example.com"' in content


def test_tarslip_validation_logic():
    import re
    safe_paths = [
        "etc/waze-panel/panel.env",
        "etc/waze-panel/waze-panel.db",
        "etc/openvpn/server/waze-udp.conf",
    ]
    unsafe_paths = [
        "etc/waze-panel/../../etc/shadow",
        "/etc/shadow",
        "root/.ssh/authorized_keys",
        "etc/openvpn/../shadow",
    ]

    prefix_pattern = re.compile(r"^(etc/waze-panel/|etc/openvpn/)")
    traversal_pattern = re.compile(r"(\.\./|^/)")

    for p in safe_paths:
        assert prefix_pattern.match(p) is not None
        assert traversal_pattern.search(p) is None

    for p in unsafe_paths:
        is_bad = (prefix_pattern.match(p) is None) or (traversal_pattern.search(p) is not None)
        assert is_bad is True


def test_openvpn_client_tuning_render(monkeypatch):
    from app.openvpn import templates as ovpn_tmpls
    from app.openvpn import certs, tlscrypt

    monkeypatch.setattr(certs, "read_ca_cert", lambda: "FAKE-CA-CERT")
    monkeypatch.setattr(certs, "read_ta_key", lambda: "FAKE-TA-KEY")

    db = SessionLocal()
    try:
        udp_out = ovpn_tmpls._render("udp", "", "", "<tls-crypt>KEY</tls-crypt>")
        assert "fast-io" in udp_out
        assert "sndbuf 524288" in udp_out
        assert "rcvbuf 524288" in udp_out
        assert "data-ciphers AES-256-GCM:AES-128-GCM:CHACHA20-POLY1305" in udp_out

        tcp_out = ovpn_tmpls._render("tcp", "", "", "<tls-crypt>KEY</tls-crypt>")
        assert "tcp-nodelay" in tcp_out
        assert "sndbuf 524288" in tcp_out
        assert "rcvbuf 524288" in tcp_out
    finally:
        db.close()


def test_xray_policy_tuning():
    from app.xray import core as xray_core

    init_db()
    db = SessionLocal()
    try:
        cfg = xray_core.build_config(db)
        level0 = cfg["policy"]["levels"]["0"]
        assert level0["handshake"] == 4
        assert level0["connIdle"] == 300
        assert level0["bufferSize"] == 512
        freedom_out = next(o for o in cfg["outbounds"] if o["protocol"] == "freedom")
        assert freedom_out["settings"]["domainStrategy"] == "UseIP"
    finally:
        db.close()


def test_clash_subscription_and_endpoints():
    import json
    import yaml
    from app.models import VpnUser, XrayInbound
    from app.xray import links as xray_links

    init_db()
    db = SessionLocal()
    try:
        # Create test user
        user = db.query(VpnUser).filter(VpnUser.username == "test_clash_user").first()
        if not user:
            user = VpnUser(
                username="test_clash_user",
                token="clash-test-token-12345678",
                xray_enabled=True,
                xray_uuid="00000000-0000-0000-0000-000000000001",
                data_limit_bytes=10 * 1024 * 1024 * 1024,
                data_used_bytes=1024 * 1024,
            )
            db.add(user)
            db.commit()
            db.refresh(user)

        # Create or fetch test inbound
        ib = db.query(XrayInbound).filter(XrayInbound.name == "Test Clash Inbound").first()
        if not ib:
            ib = XrayInbound(
                name="Test Clash Inbound",
                protocol="vless",
                transport="ws",
                security="reality",
                port=443,
                options=json.dumps({
                    "sni": "speedtest.net",
                    "public_key": "fake-pub-key-123",
                    "short_id": "0123456789abcdef",
                    "path": "/clash-ws",
                }),
                enabled=True,
            )
            db.add(ib)
            db.commit()

        # Test direct YAML generation
        yaml_content, headers = xray_links.clash_subscription(db, user)
        assert "subscription-userinfo" in headers
        assert "profile-title" in headers
        assert headers["content-disposition"] == 'attachment; filename="test_clash_user.yaml"'

        parsed = yaml.safe_load(yaml_content)
        assert parsed["port"] == 7890
        assert parsed["mode"] == "rule"
        assert len(parsed["proxies"]) >= 1
        p = parsed["proxies"][0]
        assert p["type"] == "vless"
        assert p["servername"] == "speedtest.net"
        assert p["reality-opts"]["public-key"] == "fake-pub-key-123"
        assert p["ws-opts"]["path"] == "/clash-ws"

        # Check proxy groups and rules
        group_names = [g["name"] for g in parsed["proxy-groups"]]
        assert any("AUTO" in g for g in group_names)
        assert any("PROXY" in g for g in group_names)
        assert any("IRAN" in g for g in group_names)
        assert any("GEOIP,IR" in r for r in parsed["rules"])

        # Test HTTP endpoints via TestClient
        client = TestClient(app)

        # 1. /sub/{token}/clash
        res_clash = client.get(f"/sub/{user.token}/clash")
        assert res_clash.status_code == 200
        assert "text/yaml" in res_clash.headers.get("content-type", "")
        assert "proxies:" in res_clash.text

        # 2. /sub/{token}/xray with Clash User-Agent
        res_ua = client.get(f"/sub/{user.token}/xray", headers={"user-agent": "ClashforWindows/0.20.39"})
        assert res_ua.status_code == 200
        assert "text/yaml" in res_ua.headers.get("content-type", "")

        # 3. /sub/{token}/xray with default User-Agent (base64 links)
        res_default = client.get(f"/sub/{user.token}/xray")
        assert res_default.status_code == 200
        assert "text/plain" in res_default.headers.get("content-type", "")

        # 4. /sub/{token} subscription web page
        res_page = client.get(f"/sub/{user.token}")
        assert res_page.status_code == 200
        assert "لینک اشتراک Clash / Stash / Mihomo" in res_page.text
        assert "FlClash" in res_page.text
    finally:
        db.close()


def test_xray_watchdog_resurrection(monkeypatch):
    from app.openvpn import scheduler
    from app.xray import core as xray_core

    applied = []
    monkeypatch.setattr(xray_core, "installed", lambda: True)
    monkeypatch.setattr(xray_core, "config_path", lambda: Path("/etc/xray/config.json"))
    monkeypatch.setattr(Path, "exists", lambda self: True)
    monkeypatch.setattr(xray_core, "api_up", lambda: False)
    monkeypatch.setattr(xray_core, "apply", lambda db, force=False: applied.append(force))

    scheduler._xray_down_count = 0
    # First poll: down count becomes 1, no apply yet
    scheduler._poll_xray()
    assert scheduler._xray_down_count == 1
    assert len(applied) == 0

    # Second poll: down count reaches 2, triggers resurrection apply(db, force=True)
    scheduler._poll_xray()
    assert scheduler._xray_down_count == 0
    assert len(applied) == 1
    assert applied[0] is True

