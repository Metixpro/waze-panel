import time
from pathlib import Path

from fastapi.templating import Jinja2Templates

from app.config import settings

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def humanize_bytes(value) -> str:
    try:
        n = float(value or 0)
    except (TypeError, ValueError):
        return "0 B"
    units = ["B", "KB", "MB", "GB", "TB", "PB"]
    idx = 0
    while n >= 1024 and idx < len(units) - 1:
        n /= 1024
        idx += 1
    if idx == 0:
        return f"{int(n)} {units[idx]}"
    return f"{n:.2f} {units[idx]}"


templates.env.filters["humanize_bytes"] = humanize_bytes
# Live settings object (Settings-page edits show up without a restart) and a
# per-process asset version so browsers pick up new CSS/JS after an update.
templates.env.globals["cfg"] = settings
templates.env.globals["asset_v"] = str(int(time.time()))
