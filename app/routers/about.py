from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app import about
from app.database import get_db
from app.deps import get_optional_admin
from app.models import AdminUser
from app.templating import templates
from app.version import REPO_URL, __version__, build_info

router = APIRouter()


class AboutOptions(BaseModel):
    auto_check: bool


@router.get("/about")
def about_page(request: Request, admin: AdminUser | None = Depends(get_optional_admin)):
    if not admin:
        return RedirectResponse(url="/login", status_code=302)
    return templates.TemplateResponse(
        "about.html",
        {
            "request": request,
            "admin": admin,
            "developer": about.DEVELOPER,
            "credits": about.CREDITS,
            "changelog": about.local_changelog(),
            "build": build_info(),
            "system": about.system_info(),
            "repo_url": REPO_URL,
            "version": __version__,
        },
    )


@router.get("/api/about")
def about_data(admin: AdminUser | None = Depends(get_optional_admin), db: Session = Depends(get_db)):
    if not admin:
        raise HTTPException(status_code=401, detail="unauthorized")
    return about.payload(db)


@router.post("/api/about/check")
def about_check(admin: AdminUser | None = Depends(get_optional_admin), db: Session = Depends(get_db)):
    if not admin:
        raise HTTPException(status_code=401, detail="unauthorized")
    return about.payload(db, force=True)


@router.post("/api/about/options")
def about_options(
    payload: AboutOptions,
    admin: AdminUser | None = Depends(get_optional_admin),
    db: Session = Depends(get_db),
):
    if not admin:
        raise HTTPException(status_code=401, detail="unauthorized")
    about.set_auto_check(db, payload.auto_check)
    return {"ok": True, "auto_check": payload.auto_check}
