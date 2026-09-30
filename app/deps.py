from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import AdminUser

SESSION_KEY = "admin_id"
SESSION_VERSION_KEY = "session_version"


def get_current_admin(request: Request, db: Session = Depends(get_db)) -> AdminUser:
    admin_id = request.session.get(SESSION_KEY)
    if not admin_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    admin = db.get(AdminUser, admin_id)
    if not admin:
        request.session.clear()
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    session_version = request.session.get(SESSION_VERSION_KEY)
    if session_version != admin.session_version:
        request.session.clear()
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session expired or invalidated")
    return admin


def get_optional_admin(request: Request, db: Session = Depends(get_db)) -> AdminUser | None:
    admin_id = request.session.get(SESSION_KEY)
    if not admin_id:
        return None
    admin = db.get(AdminUser, admin_id)
    if not admin:
        return None
    session_version = request.session.get(SESSION_VERSION_KEY)
    if session_version != admin.session_version:
        request.session.clear()
        return None
    return admin
