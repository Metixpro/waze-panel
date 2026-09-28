"""Background traffic-accounting & enforcement loop.

Every TRAFFIC_POLL_INTERVAL_SECONDS we ask both OpenVPN instances (UDP and
TCP) for their currently connected clients via the management interface,
turn the cumulative per-session byte counters into deltas, add those deltas
onto each VpnUser's usage total (and today's TrafficSample row for the
chart), and kill any session that belongs to a user who is disabled,
expired, or now over quota -- even mid-session, so limits are enforced in
near-real-time and not just at the next connection attempt.
"""
import datetime
import logging

from apscheduler.schedulers.background import BackgroundScheduler

from app.config import settings
from app.database import SessionLocal
from app.models import TrafficSample, VpnUser
from app.openvpn import mgmt

logger = logging.getLogger("waze_panel.scheduler")

_INSTANCES = [("udp", settings.OVPN_UDP_MGMT_PORT), ("tcp", settings.OVPN_TCP_MGMT_PORT)]

# key: (proto, common_name) -> (connected_since, last_bytes_received, last_bytes_sent)
_last_seen: dict[tuple[str, str], tuple[str, int, int]] = {}

_scheduler: BackgroundScheduler | None = None


def _poll_once() -> None:
    seen_keys: set[tuple[str, str]] = set()
    db = SessionLocal()
    try:
        today = datetime.date.today()
        for proto, port in _INSTANCES:
            try:
                sessions = mgmt.get_client_sessions(port)
            except mgmt.ManagementError:
                # Instance not up yet / not reachable -- skip this round.
                continue

            for sess in sessions:
                key = (proto, sess.common_name)
                seen_keys.add(key)

                prev = _last_seen.get(key)
                if prev and prev[0] == sess.connected_since:
                    delta_recv = max(0, sess.bytes_received - prev[1])
                    delta_sent = max(0, sess.bytes_sent - prev[2])
                else:
                    # First time we see this connection: count everything
                    # accumulated so far (best-effort; the poll interval is
                    # short so this is rarely more than a few KB).
                    delta_recv = sess.bytes_received
                    delta_sent = sess.bytes_sent

                _last_seen[key] = (sess.connected_since, sess.bytes_received, sess.bytes_sent)

                delta_total = delta_recv + delta_sent
                user = db.query(VpnUser).filter(VpnUser.username == sess.common_name).first()
                if user is None:
                    continue

                if delta_total:
                    user.data_used_bytes = (user.data_used_bytes or 0) + delta_total
                    sample = (
                        db.query(TrafficSample)
                        .filter(TrafficSample.vpn_user_id == user.id, TrafficSample.date == today)
                        .first()
                    )
                    if sample is None:
                        sample = TrafficSample(vpn_user_id=user.id, date=today, bytes_total=0)
                        db.add(sample)
                    sample.bytes_total += delta_total

                user.last_connected_at = datetime.datetime.now(datetime.timezone.utc)
                user.last_ip = sess.real_address.rsplit(":", 1)[0]
                db.flush()

                if not user.is_usable():
                    logger.info(
                        "killing session for '%s' on %s (disabled/expired/over-quota)",
                        user.username,
                        proto,
                    )
                    try:
                        mgmt.kill_client(port, user.username)
                    except mgmt.ManagementError:
                        pass

        db.commit()
    except Exception:
        db.rollback()
        logger.exception("traffic poll failed")
    finally:
        db.close()

    # Drop stale entries for connections that ended, so a later reconnect
    # is treated as fresh rather than diffing against a stale baseline.
    stale = [k for k in _last_seen if k not in seen_keys]
    for k in stale:
        _last_seen.pop(k, None)


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
    return {cn for (_proto, cn) in _last_seen.keys()}


def finalize_disconnect(proto: str, common_name: str, bytes_received: int, bytes_sent: int) -> None:
    """Called by the client-disconnect hook. Accounts for whatever traffic
    happened since the last periodic poll (or the whole session, if it was
    shorter than the poll interval), then drops the in-memory baseline so a
    reconnect starts clean."""
    key = (proto, common_name)
    prev = _last_seen.pop(key, None)

    final_total = max(0, bytes_received) + max(0, bytes_sent)
    if prev is not None:
        delta = max(0, final_total - (prev[1] + prev[2]))
    else:
        delta = final_total

    if delta <= 0:
        return

    db = SessionLocal()
    try:
        user = db.query(VpnUser).filter(VpnUser.username == common_name).first()
        if user is None:
            return
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
        sample.bytes_total += delta
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("finalize_disconnect failed for %s/%s", proto, common_name)
    finally:
        db.close()
