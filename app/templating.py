import re
import time
from pathlib import Path

from fastapi.templating import Jinja2Templates
from markupsafe import Markup, escape

from app import about
from app.config import settings
from app.version import __version__

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


def inline_md(value) -> Markup:
    """`code` and **bold** inside one escaped line (changelog items)."""
    text = str(escape(value or ""))
    text = re.sub(r"`([^`]+)`", r"<code>\1</code>", text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", text)
    return Markup(text)


def fa_digits(value) -> str:
    """Persian digits, for numbers inside Persian sentences."""
    return str(value).translate(str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹"))


templates.env.filters["humanize_bytes"] = humanize_bytes
templates.env.filters["fa"] = fa_digits
templates.env.filters["inline_md"] = inline_md
# Live settings object (Settings-page edits show up without a restart) and a
# per-process asset version so browsers pick up new CSS/JS after an update.
templates.env.globals["cfg"] = settings
templates.env.globals["asset_v"] = str(int(time.time()))
templates.env.globals["app_version"] = __version__
templates.env.globals["update_available"] = about.update_available
templates.env.globals["developer"] = about.DEVELOPER
