from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import SESSION_KEY, get_optional_admin
from app.models import AdminUser
from app.security import verify_password
from app.templating import templates
from app.config import settings
import datetime

router = APIRouter()


@router.get("/login")
def login_page(request: Request, admin: AdminUser | None = Depends(get_optional_admin)):
    if admin:
        return RedirectResponse(url="/dashboard", status_code=302)
    return templates.TemplateResponse(
        "login.html", {"request": request, "error": None, "panel_title": settings.PANEL_TITLE}
    )


@router.post("/login")
def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    admin = db.query(AdminUser).filter(AdminUser.username == username).first()
    if not admin or not verify_password(password, admin.password_hash):
        return templates.TemplateResponse(
            "login.html",
            {
                "request": request,
                "error": "نام کاربری یا رمز عبور اشتباه است.",
                "panel_title": settings.PANEL_TITLE,
            },
            status_code=401,
        )

    admin.last_login_at = datetime.datetime.now(datetime.timezone.utc)
    db.commit()

    request.session[SESSION_KEY] = admin.id
    return RedirectResponse(url="/dashboard", status_code=302)


@router.get("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/login", status_code=302)
