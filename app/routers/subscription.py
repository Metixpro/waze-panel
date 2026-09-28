from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import PlainTextResponse
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models import VpnUser
from app.openvpn import certs
from app.openvpn.templates import build_ovpn
from app.templating import templates

router = APIRouter()


def _get_user_or_404(token: str, db: Session) -> VpnUser:
    user = db.query(VpnUser).filter(VpnUser.token == token).first()
    if not user:
        raise HTTPException(status_code=404, detail="لینک نامعتبر است.")
    return user


@router.get("/sub/{token}")
def subscription_page(token: str, request: Request, db: Session = Depends(get_db)):
    user = _get_user_or_404(token, db)

    status_label = user.status_label()

    usage_percent = None
    if user.data_limit_bytes:
        usage_percent = min(100, round((user.data_used_bytes / user.data_limit_bytes) * 100, 1))

    return templates.TemplateResponse(
        "subscription.html",
        {
            "request": request,
            "user": user,
            "status_label": status_label,
            "usage_percent": usage_percent,
            "panel_title": settings.PANEL_TITLE,
            "udp_port": settings.OVPN_UDP_PORT,
            "tcp_port": settings.OVPN_TCP_PORT,
            "days_left": user.days_left(),
        },
    )


@router.get("/sub/{token}/{proto}")
def subscription_download(token: str, proto: str, db: Session = Depends(get_db)):
    if proto not in ("udp", "tcp"):
        raise HTTPException(status_code=400, detail="invalid proto")
    user = _get_user_or_404(token, db)

    try:
        content = build_ovpn(user.username, proto)
    except certs.CertError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    filename = f"{user.username}-{proto}.ovpn"
    return PlainTextResponse(
        content,
        media_type="application/x-openvpn-profile",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
