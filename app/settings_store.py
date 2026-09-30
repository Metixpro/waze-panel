import os
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
        text = _ENV_FILE.read_text(encoding="utf-8")
    except OSError:
        return

    pattern = re.compile(rf"^{re.escape(key)}=.*$", re.MULTILINE)
    line = f'{key}="{value}"'
    if pattern.search(text):
        text = pattern.sub(line, text)
    else:
        text = text.rstrip("\n") + f"\n{line}\n"

    tmp_file = _ENV_FILE.with_name(f"{_ENV_FILE.name}.tmp.{os.getpid()}")
    try:
        try:
            mode = _ENV_FILE.stat().st_mode & 0o777
        except OSError:
            mode = 0o600
        fd = os.open(str(tmp_file), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_file, _ENV_FILE)
    except OSError:
        if tmp_file.exists():
            try:
                tmp_file.unlink()
            except OSError:
                pass
