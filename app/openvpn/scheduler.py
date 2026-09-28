"""Background traffic-accounting & enforcement loop.

Every TRAFFIC_POLL_INTERVAL_SECONDS we ask both OpenVPN instances (UDP and
TCP) for their currently connected clients via the management interface,
turn the cumulative per-session byte counters into deltas, add those deltas
onto each VpnUser's usage total (and today's TrafficSample row for the
chart), and disconnect any session that belongs to a user who is disabled,
expired, or now over quota -- even mid-session, so limits are enforced in
near-real-time and not just at the next connection attempt.

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
from app.models import TrafficSample, VpnUser
from app.openvpn import mgmt

logger = logging.getLogger("waze_panel.scheduler")

_TOMBSTONE_TTL_SECONDS = 300

_lock = threading.Lock()

# (proto, common_name) -> (session_start_t, last_bytes_received, last_bytes_sent)
_last_seen: dict[tuple[str, str], tuple[int, int, int]] = {}

# (proto, common_name) -> (finalized_session_start_t or None, finalized_at)
_finalized: dict[tuple[str, str], tuple[int | None, float]] = {}

# (proto, common_name) -> live details for the dashboard
_session_info: dict[tuple[str, str], dict] = {}

_scheduler: BackgroundScheduler | None = None


def _instances() -> list[tuple[str, int]]:
    return [("udp", settings.OVPN_UDP_MGMT_PORT), ("tcp", settings.OVPN_TCP_MGMT_PORT)]


def _record_usage(db, user: VpnUser, delta: int) -> None:
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
    # Session was shorter than one poll, so its start time was never seen:
    # anything that started before the hook ran is that old session.
    return session_t < int(finalized_at)


def _poll_once() -> None:
    # Talk to OpenVPN *before* taking the lock: a client-disconnect hook
    # blocks its OpenVPN instance while it waits for finalize_disconnect(),
    # so holding the lock across a management call could stall both.
    snapshots: dict[str, tuple[int, list[mgmt.ClientSession]] | None] = {}
    for proto, port in _instances():
        try:
            snapshots[proto] = (port, mgmt.get_client_sessions(port))
        except mgmt.ManagementError:
            snapshots[proto] = None  # instance down / restarting

    to_kill: list[tuple[int, str, int | None]] = []

    with _lock:
        now = time.time()
        for key in [k for k, (_, at) in _finalized.items() if now - at > _TOMBSTONE_TTL_SECONDS]:
            _finalized.pop(key, None)

        seen_keys: set[tuple[str, str]] = set()
        db = SessionLocal()
        try:
            for proto, snap in snapshots.items():
                if snap is None:
                    # Unknown state: keep this instance's baselines as they are.
                    seen_keys.update(k for k in _last_seen if k[0] == proto)
                    continue
                port, sessions = snap
                for sess in sessions:
                    key = (proto, sess.common_name)
                    if _already_finalized(key, sess.connected_since_t):
                        continue
                    seen_keys.add(key)

                    prev = _last_seen.get(key)
                    if prev and prev[0] == sess.connected_since_t:
                        delta = max(0, sess.bytes_received - prev[1]) + max(0, sess.bytes_sent - prev[2])
                    else:
                        # First sight of this connection: count everything so far.
                        delta = sess.bytes_received + sess.bytes_sent
                    _last_seen[key] = (sess.connected_since_t, sess.bytes_received, sess.bytes_sent)
                    _session_info[key] = {
                        "username": sess.common_name,
                        "proto": proto,
                        "ip": sess.real_address.rsplit(":", 1)[0],
                        "since": sess.connected_since_t,
                        "bytes": sess.bytes_received + sess.bytes_sent,
                    }

                    user = db.query(VpnUser).filter(VpnUser.username == sess.common_name).first()
                    if user is None:
                        to_kill.append((port, sess.common_name, sess.client_id))
                        continue

                    if delta:
                        _record_usage(db, user, delta)
                    user.last_connected_at = datetime.datetime.now(datetime.timezone.utc)
                    user.last_ip = sess.real_address.rsplit(":", 1)[0]
                    db.flush()

                    if not user.is_usable():
                        to_kill.append((port, user.username, sess.client_id))
            db.commit()
        except Exception:
            db.rollback()
            logger.exception("traffic poll failed")
        finally:
            db.close()

        for key in [k for k in _last_seen if k not in seen_keys]:
            _last_seen.pop(key, None)
            _session_info.pop(key, None)

    for port, username, client_id in to_kill:
        logger.info("disconnecting '%s' (disabled/expired/over quota/unknown)", username)
        try:
            mgmt.kill_client(port, username, client_id)
        except mgmt.ManagementError:
            pass


def finalize_disconnect(proto: str, common_name: str, bytes_received: int, bytes_sent: int) -> None:
    """Called by the client-disconnect hook with the session's final byte
    counters. Accounts for whatever the periodic poll hasn't seen yet (or
    the whole session, if it was shorter than one poll interval)."""
    key = (proto, common_name)
    final_total = max(0, bytes_received) + max(0, bytes_sent)

    with _lock:
        prev = _last_seen.pop(key, None)
        _session_info.pop(key, None)
        if prev is not None:
            delta = max(0, final_total - (prev[1] + prev[2]))
            _finalized[key] = (prev[0], time.time())
        else:
            delta = final_total
            _finalized[key] = (None, time.time())

        if delta <= 0:
            return

        db = SessionLocal()
        try:
            user = db.query(VpnUser).filter(VpnUser.username == common_name).first()
            if user is not None:
                _record_usage(db, user, delta)
                db.commit()
        except Exception:
            db.rollback()
            logger.exception("finalize_disconnect failed for %s/%s", proto, common_name)
        finally:
            db.close()


def _refresh_crl() -> None:
    from app.openvpn import certs

    try:
        certs.refresh_crl()
    except Exception:
        logger.exception("CRL refresh failed")


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
    _scheduler.start()
    logger.info("traffic scheduler started (every %ss)", settings.TRAFFIC_POLL_INTERVAL_SECONDS)
    return _scheduler


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None


def get_online_usernames() -> set[str]:
    """Usernames currently connected on either instance, based on the most
    recent poll. Cheap, in-memory, no DB/socket hit."""
    with _lock:
        return {cn for (_proto, cn) in _last_seen.keys()}


def get_online_sessions() -> list[dict]:
    """Live sessions from the most recent poll (newest first)."""
    with _lock:
        sessions = [dict(v) for k, v in _session_info.items() if k in _last_seen]
    return sorted(sessions, key=lambda s: s["since"], reverse=True)
