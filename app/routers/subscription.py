from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import PlainTextResponse
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models import VpnUser
from app.openvpn import certs
from app.openvpn.templates import build_ovpn
from app.pwa import web_manifest
from app.templating import templates
from app.xray import links as xray_links

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
            "days_left": user.days_left(),
            "auth_mode": user.auth_mode or "cert",
            "xray_links": xray_links.user_links(db, user) if user.xray_enabled else [],
            "xray_sub": f"{settings.public_base_url}/sub/{token}/xray",
        },
    )


@router.get("/sub/{token}/manifest.webmanifest")
def subscription_manifest(token: str, db: Session = Depends(get_db)):
    """The user can add their subscription page to the home screen and
    check their usage like an app."""
    user = _get_user_or_404(token, db)
    return web_manifest(f"{user.username} · {settings.PANEL_TITLE}", user.username, f"/sub/{token}", f"/sub/{token}")


@router.get("/sub/{token}/xray")
def subscription_xray(token: str, db: Session = Depends(get_db)):
    """The Xray subscription apps import and refresh: base64 of the share
    links, with usage and expiry in the headers they understand."""
    user = _get_user_or_404(token, db)
    body, headers = xray_links.subscription(db, user)
    return PlainTextResponse(body, headers=headers)


@router.get("/sub/{token}/{proto}")
def subscription_download(token: str, proto: str, db: Session = Depends(get_db)):
    if proto not in ("udp", "tcp"):
        raise HTTPException(status_code=400, detail="invalid proto")
    user = _get_user_or_404(token, db)

    try:
        content = build_ovpn(db, user, proto)
    except certs.CertError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    filename = f"{user.username}-{proto}.ovpn"
    return PlainTextResponse(
        content,
        media_type="application/x-openvpn-profile",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
