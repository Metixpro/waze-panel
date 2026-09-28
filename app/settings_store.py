"""Bridges the DB-backed `settings` table with the in-process Settings
object, and keeps /etc/waze-panel/panel.env in sync so changes made from
the panel survive a service restart."""
import re
from pathlib import Path

from sqlalchemy.orm import Session

from app.config import settings
from app.models import Setting

# Keys editable at runtime from the Settings page.
_RUNTIME_KEYS = ("SERVER_ADDRESS", "PANEL_TITLE", "SUBSCRIPTION_BASE_URL")

_ENV_FILE = Path("/etc/waze-panel/panel.env")


def load_overrides(db: Session) -> None:
    rows = db.query(Setting).filter(Setting.key.in_(_RUNTIME_KEYS)).all()
    for row in rows:
        setattr(settings, row.key, row.value)


def set_value(db: Session, key: str, value: str) -> None:
    if key not in _RUNTIME_KEYS:
        raise ValueError(f"'{key}' is not a runtime-editable setting")
    # Values end up inside KEY="..." lines of panel.env: never let one
    # break out of its line or its quotes.
    value = re.sub(r'["\\\r\n`$]', "", value).strip()

    row = db.get(Setting, key)
    if row is None:
        row = Setting(key=key, value=value)
        db.add(row)
    else:
        row.value = value
    db.commit()

    setattr(settings, key, value)
    _sync_env_file(key, value)


def _sync_env_file(key: str, value: str) -> None:
    if not _ENV_FILE.exists():
        return  # dev environment, nothing to sync
    try:
        text = _ENV_FILE.read_text()
    except OSError:
        return

    pattern = re.compile(rf"^{re.escape(key)}=.*$", re.MULTILINE)
    line = f'{key}="{value}"'
    if pattern.search(text):
        text = pattern.sub(line, text)
    else:
        text = text.rstrip("\n") + f"\n{line}\n"

    try:
        _ENV_FILE.write_text(text)
    except OSError:
        pass
