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
from app.openvpn.scheduler import get_online_usernames
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


@router.get("/api/dashboard/stats")
def dashboard_stats(admin: AdminUser = Depends(get_optional_admin), db: Session = Depends(get_db)):
    if not admin:
        return JSONResponse({"detail": "unauthorized"}, status_code=401)

    total_users = db.query(func.count(VpnUser.id)).scalar() or 0
    active_users = (
        db.query(func.count(VpnUser.id))
        .filter(VpnUser.enabled == True, VpnUser.revoked == False)  # noqa: E712
        .scalar()
        or 0
    )
    online_usernames = get_online_usernames()

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

    # last 14 days chart data
    since = today - datetime.timedelta(days=13)
    rows = (
        db.query(TrafficSample.date, func.sum(TrafficSample.bytes_total))
        .filter(TrafficSample.date >= since)
        .group_by(TrafficSample.date)
        .all()
    )
    by_date = {d.isoformat(): int(total) for d, total in rows}
    chart_labels = []
    chart_values = []
    for i in range(14):
        d = since + datetime.timedelta(days=i)
        chart_labels.append(d.strftime("%m-%d"))
        chart_values.append(by_date.get(d.isoformat(), 0))

    # system info
    cpu_percent = psutil.cpu_percent(interval=0.1)
    mem = psutil.virtual_memory()
    disk = psutil.disk_usage("/")
    uptime_seconds = int(time.time() - _BOOT_TIME)

    return {
        "total_users": total_users,
        "active_users": active_users,
        "online_count": len(online_usernames),
        "online_usernames": sorted(online_usernames),
        "today_bytes": int(today_bytes),
        "total_bytes": int(total_bytes),
        "chart_labels": chart_labels,
        "chart_values": chart_values,
        "instances": _instance_status(),
        "system": {
            "cpu_percent": cpu_percent,
            "mem_percent": mem.percent,
            "mem_used": mem.used,
            "mem_total": mem.total,
            "disk_percent": disk.percent,
            "disk_used": disk.used,
            "disk_total": disk.total,
            "uptime_seconds": uptime_seconds,
        },
        "server_address": settings.SERVER_ADDRESS,
    }
