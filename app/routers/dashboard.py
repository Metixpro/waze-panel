import datetime
import time

import psutil
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.deps import get_optional_admin
from app.models import AdminUser, TrafficSample, VpnUser
from app.openvpn import mgmt
from app.openvpn.scheduler import get_online_sessions
from app.relays import get_health, ordered_relays, relay_names_by_ip
from app.templating import templates

router = APIRouter()

_BOOT_TIME = psutil.boot_time()


@router.get("/")
def root(admin: AdminUser | None = Depends(get_optional_admin)):
    return RedirectResponse(url="/dashboard" if admin else "/login", status_code=302)


@router.get("/dashboard")
def dashboard_page(request: Request, admin: AdminUser | None = Depends(get_optional_admin)):
    if not admin:
        return RedirectResponse(url="/login", status_code=302)
    return templates.TemplateResponse(
        "dashboard.html",
        {"request": request, "admin": admin, "panel_title": settings.PANEL_TITLE},
    )


def _instance_status() -> dict:
    return {
        "udp": {
            "port": settings.OVPN_UDP_PORT,
            "reachable": mgmt.is_reachable(settings.OVPN_UDP_MGMT_PORT),
        },
        "tcp": {
            "port": settings.OVPN_TCP_PORT,
            "reachable": mgmt.is_reachable(settings.OVPN_TCP_MGMT_PORT),
        },
    }


def _relay_status(db: Session) -> list[dict]:
    """Enabled relays with their last health check, and how many users are
    connected through each right now."""
    health = get_health()
    via = relay_names_by_ip()
    users_by_name: dict[str, int] = {}
    for sess in get_online_sessions():
        name = via.get(sess["ip"])
        if name:
            users_by_name[name] = users_by_name.get(name, 0) + 1
    return [
        {
            "id": r.id,
            "name": r.name,
            "ok": health.get(r.id, {}).get("ok"),
            "ms": health.get(r.id, {}).get("ms"),
            "sessions": users_by_name.get(r.name, 0),
        }
        for r in ordered_relays(db, enabled_only=True)
    ]


@router.get("/api/dashboard/stats")
def dashboard_stats(admin: AdminUser = Depends(get_optional_admin), db: Session = Depends(get_db)):
    if not admin:
        return JSONResponse({"detail": "unauthorized"}, status_code=401)

    users = db.query(VpnUser).all()
    status_counts = {"active": 0, "disabled": 0, "expired": 0, "over_quota": 0, "revoked": 0}
    ending_soon = 0
    for u in users:
        status_counts[u.status_label()] += 1
        ending_soon += 1 if u.is_ending_soon() else 0

    sessions = get_online_sessions()
    online_usernames = {s["username"] for s in sessions}
    via = relay_names_by_ip()
    for sess in sessions:
        sess["via"] = via.get(sess["ip"])

    today = datetime.date.today()
    today_bytes = (
        db.query(func.coalesce(func.sum(TrafficSample.bytes_total), 0))
        .filter(TrafficSample.date == today)
        .scalar()
        or 0
    )
    # From the daily history rather than users' quota counters, so resetting
    # someone's usage doesn't make served traffic disappear from the total.
    total_bytes = db.query(func.coalesce(func.sum(TrafficSample.bytes_total), 0)).scalar() or 0

    since = today - datetime.timedelta(days=13)
    rows = (
        db.query(TrafficSample.date, func.sum(TrafficSample.bytes_total))
        .filter(TrafficSample.date >= since)
        .group_by(TrafficSample.date)
        .all()
    )
    by_date = {d.isoformat(): int(total) for d, total in rows}
    chart_dates = [(since + datetime.timedelta(days=i)).isoformat() for i in range(14)]
    chart_values = [by_date.get(d, 0) for d in chart_dates]

    top_rows = (
        db.query(VpnUser.id, VpnUser.username, TrafficSample.bytes_total)
        .join(TrafficSample, TrafficSample.vpn_user_id == VpnUser.id)
        .filter(TrafficSample.date == today)
        .order_by(TrafficSample.bytes_total.desc())
        .limit(5)
        .all()
    )

    cpu_percent = psutil.cpu_percent(interval=0.1)
    mem = psutil.virtual_memory()
    disk = psutil.disk_usage("/")
    load1 = psutil.getloadavg()[0] if hasattr(psutil, "getloadavg") else 0.0

    return {
        "total_users": len(users),
        "active_users": status_counts["active"],
        "status_counts": status_counts,
        "ending_soon": ending_soon,
        "online_count": len(online_usernames),
        "online_usernames": sorted(online_usernames),
        "online_sessions": sessions,
        "today_bytes": int(today_bytes),
        "total_bytes": int(total_bytes),
        "chart_dates": chart_dates,
        "chart_values": chart_values,
        "top_today": [{"id": i, "username": n, "bytes": int(b)} for i, n, b in top_rows],
        "instances": _instance_status(),
        "relays": _relay_status(db),
        "system": {
            "cpu_percent": cpu_percent,
            "cpu_count": psutil.cpu_count() or 1,
            "load1": round(load1, 2),
            "mem_percent": mem.percent,
            "mem_used": mem.used,
            "mem_total": mem.total,
            "disk_percent": disk.percent,
            "disk_used": disk.used,
            "disk_total": disk.total,
            "uptime_seconds": int(time.time() - _BOOT_TIME),
        },
        "server_address": settings.SERVER_ADDRESS,
        "now": int(time.time()),
    }
