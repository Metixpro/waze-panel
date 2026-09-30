"""Background traffic-accounting & enforcement loop.

Every TRAFFIC_POLL_INTERVAL_SECONDS we ask both OpenVPN instances (UDP and
TCP) for their currently connected clients via the management interface,
turn the cumulative per-session byte counters into deltas, add those deltas
onto each VpnUser's usage total (and today's TrafficSample row for the
chart), and disconnect any session that belongs to a user who is disabled,
expired, now over quota, or over their device limit -- even mid-session, so
limits are enforced in near-real-time and not just at the next connection
attempt.

Sessions are keyed by (proto, "ip:port"): that is unique per live session
on one instance, works for password-only clients (which have no
certificate CN), and lets one account be online from several devices.

The client-disconnect hook (finalize_disconnect) accounts for the tail of
each session. Both paths share state, so they run under one lock, and a
session the hook has already finalized is remembered briefly ("tombstone")
so a poll snapshot taken just before the disconnect can't count it again.
"""
import datetime
import logging
import threading
import time

from apscheduler.schedulers.background import BackgroundScheduler

from app.config import settings
from app.database import SessionLocal
from app.models import TrafficSample, VpnUser, find_vpn_user
from app.openvpn import mgmt

logger = logging.getLogger("waze_panel.scheduler")

_TOMBSTONE_TTL_SECONDS = 300

_lock = threading.Lock()

# (proto, "ip:port") -> {"username", "proto", "ip", "since", "bytes",
#                        "recv", "sent"}   (username is the canonical one)
_sessions: dict[tuple[str, str], dict] = {}

# (proto, "ip:port") -> (finalized_session_start_t or None, finalized_at)
_finalized: dict[tuple[str, str], tuple[int | None, float]] = {}

_scheduler: BackgroundScheduler | None = None


def _instances() -> list[tuple[str, int]]:
    return [("udp", settings.OVPN_UDP_MGMT_PORT), ("tcp", settings.OVPN_TCP_MGMT_PORT)]


def record_usage(db, user: VpnUser, delta: int) -> None:
    user.data_used_bytes = (user.data_used_bytes or 0) + delta
    today = datetime.date.today()
    sample = (
        db.query(TrafficSample)
        .filter(TrafficSample.vpn_user_id == user.id, TrafficSample.date == today)
        .first()
    )
    if sample is None:
        sample = TrafficSample(vpn_user_id=user.id, date=today, bytes_total=0)
        db.add(sample)
    sample.bytes_total = (sample.bytes_total or 0) + delta


def _already_finalized(key: tuple[str, str], session_t: int) -> bool:
    tomb = _finalized.get(key)
    if tomb is None:
        return False
    finalized_session_t, finalized_at = tomb
    if finalized_session_t is not None:
        return session_t == finalized_session_t
    # Session start unknown (hook from an older install): anything that
    # started before the hook ran is that old session.
    return session_t < int(finalized_at)


def _device_victims(sessions: list[tuple[int, mgmt.ClientSession]], max_devices: int):
    """Newest connections win: everything but the latest `max_devices`."""
    if max_devices <= 0 or len(sessions) <= max_devices:
        return []
    ordered = sorted(sessions, key=lambda ps: (ps[1].connected_since_t, ps[1].client_id or 0))
    return ordered[: len(ordered) - max_devices]


def _fetch_snapshots() -> dict[str, tuple[int, list[mgmt.ClientSession]] | None]:
    snapshots: dict[str, tuple[int, list[mgmt.ClientSession]] | None] = {}
    for proto, port in _instances():
        try:
            snapshots[proto] = (port, mgmt.get_client_sessions(port))
        except mgmt.ManagementError:
            snapshots[proto] = None  # instance down / restarting
    return snapshots


def _kill(victims: list[tuple[int, mgmt.ClientSession]], why: str) -> None:
    for port, sess in victims:
        logger.info("disconnecting %s (%s) at %s: %s", sess.identity, port, sess.real_address, why)
        try:
            mgmt.kill_client(port, sess.real_address, sess.client_id)
        except mgmt.ManagementError:
            pass


def _poll_once() -> None:
    # Talk to OpenVPN *before* taking the lock: a client-disconnect hook
    # blocks its OpenVPN instance while it waits for finalize_disconnect(),
    # so holding the lock across a management call could stall both.
    snapshots = _fetch_snapshots()

    to_kill: list[tuple[int, mgmt.ClientSession]] = []
    over_devices: list[tuple[int, mgmt.ClientSession]] = []

    with _lock:
        now = time.time()
        for key in [k for k, (_, at) in _finalized.items() if now - at > _TOMBSTONE_TTL_SECONDS]:
            _finalized.pop(key, None)

        seen_keys: set[tuple[str, str]] = set()
        db = SessionLocal()
        try:
            users: dict[str, VpnUser | None] = {}
            by_user: dict[int, list[tuple[int, mgmt.ClientSession]]] = {}
            for proto, snap in snapshots.items():
                if snap is None:
                    # Unknown state: keep this instance's baselines as they are.
                    seen_keys.update(k for k in _sessions if k[0] == proto)
                    continue
                port, sessions = snap
                for sess in sessions:
                    key = (proto, sess.real_address)
                    if _already_finalized(key, sess.connected_since_t):
                        continue
                    seen_keys.add(key)

                    ident = sess.identity
                    if ident.lower() not in users:
                        users[ident.lower()] = find_vpn_user(db, ident)
                    user = users[ident.lower()]

                    prev = _sessions.get(key)
                    if prev and prev["since"] == sess.connected_since_t:
                        delta = max(0, sess.bytes_received - prev["recv"]) + max(0, sess.bytes_sent - prev["sent"])
                    else:
                        # First sight of this connection: count everything so far.
                        delta = sess.bytes_received + sess.bytes_sent
                    ip = sess.real_address.rsplit(":", 1)[0]
                    _sessions[key] = {
                        "username": user.username if user else ident,
                        "proto": proto,
                        "ip": ip,
                        "since": sess.connected_since_t,
                        "bytes": sess.bytes_received + sess.bytes_sent,
                        "recv": sess.bytes_received,
                        "sent": sess.bytes_sent,
                    }

                    if user is None:
                        to_kill.append((port, sess))
                        continue

                    if delta:
                        record_usage(db, user, delta)
                    user.last_connected_at = datetime.datetime.now(datetime.timezone.utc)
                    user.last_ip = ip
                    db.flush()

                    if not user.is_usable():
                        to_kill.append((port, sess))
                    else:
                        by_user.setdefault(user.id, []).append((port, sess))

            for user in {u.id: u for u in users.values() if u is not None}.values():
                over_devices += _device_victims(by_user.get(user.id, []), user.max_devices or 0)
            db.commit()
        except Exception:
            db.rollback()
            logger.exception("traffic poll failed")
        finally:
            db.close()

        for key in [k for k in _sessions if k not in seen_keys]:
            _sessions.pop(key, None)

    _kill(to_kill, "disabled/expired/over quota/unknown")
    _kill(over_devices, "device limit")


def enforce_device_limit_soon(username: str, max_devices: int, delay: float = 1.5) -> None:
    """Called from the client-connect hook. OpenVPN is blocked until the
    hook returns, so the older sessions are cut from a timer thread once the
    new one is fully up, using a fresh management snapshot."""
    if max_devices <= 0:
        return

    def run() -> None:
        mine: list[tuple[int, mgmt.ClientSession]] = []
        for _proto, snap in _fetch_snapshots().items():
            if snap is None:
                continue
            port, sessions = snap
            mine += [(port, s) for s in sessions if s.identity.lower() == username.lower()]
        _kill(_device_victims(mine, max_devices), "device limit")

    timer = threading.Timer(delay, run)
    timer.daemon = True
    timer.start()


def finalize_disconnect(
    proto: str,
    identity: str,
    bytes_received: int,
    bytes_sent: int,
    real_address: str = "",
    start_t: int = 0,
) -> None:
    """Called by the client-disconnect hook with the session's final byte
    counters. Accounts for whatever the periodic poll hasn't seen yet (or
    the whole session, if it was shorter than one poll interval)."""
    final_total = max(0, bytes_received) + max(0, bytes_sent)

    with _lock:
        key = (proto, real_address) if real_address else None
        prev = _sessions.get(key) if key else None
        if prev is not None and start_t and prev["since"] != start_t:
            prev = None  # that address now belongs to a newer session
        if key is None:
            # Hook without the session address: find it by user + start.
            for k, v in _sessions.items():
                if k[0] == proto and v["username"].lower() == identity.lower() and (
                    not start_t or v["since"] == start_t
                ):
                    key, prev = k, v
                    break

        if prev is not None:
            _sessions.pop(key, None)
            delta = max(0, final_total - (prev["recv"] + prev["sent"]))
            _finalized[key] = (prev["since"], time.time())
        else:
            delta = final_total
            if key is not None:
                _finalized[key] = (start_t or None, time.time())

        if delta <= 0:
            return

        db = SessionLocal()
        try:
            user = find_vpn_user(db, identity)
            if user is not None:
                record_usage(db, user, delta)
                db.commit()
        except Exception:
            db.rollback()
            logger.exception("finalize_disconnect failed for %s/%s", proto, identity)
        finally:
            db.close()


def _refresh_crl() -> None:
    from app.openvpn import certs

    try:
        certs.refresh_crl()
    except Exception:
        logger.exception("CRL refresh failed")


def _check_relays() -> None:
    from app import relays

    try:
        relays.run_checks()
    except Exception:
        logger.exception("relay check failed")


_xray_down_count = 0


def _poll_xray() -> None:
    global _xray_down_count
    from app.xray import core

    if not core.installed() or not core.config_path().exists():
        return

    if not core.api_up():
        _xray_down_count += 1
        if _xray_down_count >= 2:
            logger.warning("Xray-core appears down; watchdog resurrecting service...")
            try:
                db = SessionLocal()
                try:
                    core.apply(db, force=True)
                finally:
                    db.close()
                _xray_down_count = 0
            except Exception:
                logger.exception("Xray watchdog failed to restart core")
        return

    _xray_down_count = 0
    try:
        core.poll()
    except Exception:
        logger.exception("Xray poll failed")


def _check_updates() -> None:
    from app import about

    try:
        about.scheduled_refresh()
    except Exception:
        logger.exception("update check failed")


def start_scheduler() -> BackgroundScheduler:
    global _scheduler
    if _scheduler is not None:
        return _scheduler
    _scheduler = BackgroundScheduler(timezone="UTC")
    _scheduler.add_job(
        _poll_once,
        "interval",
        seconds=settings.TRAFFIC_POLL_INTERVAL_SECONDS,
        id="traffic_poll",
        max_instances=1,
        coalesce=True,
    )
    # OpenVPN rejects *every* client once crl.pem passes its nextUpdate
    # date, so keep re-signing it well before that can happen.
    _scheduler.add_job(
        _refresh_crl,
        "interval",
        hours=24,
        id="crl_refresh",
        next_run_time=datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=30),
        max_instances=1,
        coalesce=True,
    )
    # Xray: per-user traffic and online users, then drop whoever just ran
    # out of data or time (runs apart from the OpenVPN poll and its lock).
    _scheduler.add_job(
        _poll_xray,
        "interval",
        seconds=settings.TRAFFIC_POLL_INTERVAL_SECONDS,
        id="xray_poll",
        max_instances=1,
        coalesce=True,
    )
    # Relay health (Settings page + dashboard): cheap TCP round trips.
    _scheduler.add_job(
        _check_relays,
        "interval",
        minutes=3,
        id="relay_check",
        next_run_time=datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=20),
        max_instances=1,
        coalesce=True,
    )
    # About page / navigation badge: is a newer version out? (opt-out in About)
    _scheduler.add_job(
        _check_updates,
        "interval",
        hours=12,
        id="update_check",
        next_run_time=datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=90),
        max_instances=1,
        coalesce=True,
    )
    _scheduler.start()
    logger.info("traffic scheduler started (every %ss)", settings.TRAFFIC_POLL_INTERVAL_SECONDS)
    return _scheduler


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None


def get_online_usernames() -> set[str]:
    """Usernames currently connected (OpenVPN or Xray), based on the most
    recent poll. Cheap, in-memory, no DB/socket hit."""
    return {s["username"] for s in get_online_sessions()}


def get_online_sessions() -> list[dict]:
    """Live sessions from the most recent polls (newest first): one per
    OpenVPN connection, one per user connected over Xray."""
    from app.xray.core import online_sessions

    with _lock:
        sessions = [
            {k: v[k] for k in ("username", "proto", "ip", "since", "bytes")} for v in _sessions.values()
        ]
    sessions += online_sessions()
    return sorted(sessions, key=lambda s: s["since"], reverse=True)
