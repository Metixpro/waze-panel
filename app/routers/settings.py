from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.deps import get_optional_admin
from app.models import AdminUser
from app.security import hash_password, verify_password
from app.settings_store import set_value
from app.templating import templates

router = APIRouter()


class UpdatePanelSettings(BaseModel):
    server_address: str
    panel_title: str
    subscription_base_url: str = ""


class ChangePassword(BaseModel):
    current_password: str
    new_password: str


@router.get("/settings")
def settings_page(request: Request, admin: AdminUser | None = Depends(get_optional_admin)):
    if not admin:
        return RedirectResponse(url="/login", status_code=302)
    return templates.TemplateResponse(
        "settings.html",
        {
            "request": request,
            "admin": admin,
            "panel_title": settings.PANEL_TITLE,
            "server_address": settings.SERVER_ADDRESS,
            "subscription_base_url": settings.SUBSCRIPTION_BASE_URL,
            "udp_port": settings.OVPN_UDP_PORT,
            "tcp_port": settings.OVPN_TCP_PORT,
            "panel_port": settings.PANEL_PORT,
        },
    )


@router.post("/api/settings")
def update_settings(
    payload: UpdatePanelSettings,
    admin: AdminUser | None = Depends(get_optional_admin),
    db: Session = Depends(get_db),
):
    if not admin:
        raise HTTPException(status_code=401, detail="unauthorized")

    set_value(db, "SERVER_ADDRESS", payload.server_address.strip())
    set_value(db, "PANEL_TITLE", payload.panel_title.strip() or "Waze Panel")
    set_value(db, "SUBSCRIPTION_BASE_URL", payload.subscription_base_url.strip())
    return {"ok": True}


@router.post("/api/settings/password")
def change_password(
    payload: ChangePassword,
    admin: AdminUser | None = Depends(get_optional_admin),
    db: Session = Depends(get_db),
):
    if not admin:
        raise HTTPException(status_code=401, detail="unauthorized")

    if not verify_password(payload.current_password, admin.password_hash):
        raise HTTPException(status_code=400, detail="رمز عبور فعلی اشتباه است.")
    if len(payload.new_password) < 6:
        raise HTTPException(status_code=400, detail="رمز عبور جدید باید حداقل ۶ کاراکتر باشد.")

    admin.password_hash = hash_password(payload.new_password)
    db.commit()
    return {"ok": True}
