import re
import time
from pathlib import Path

from fastapi.templating import Jinja2Templates
from markupsafe import Markup, escape

from app import about
from app.config import settings
from app.version import __version__

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"


class CompatibleJinja2Templates(Jinja2Templates):
    def TemplateResponse(self, *args, **kwargs):
        # Support both legacy (name, context, status_code=...) and modern (request, name, context=...)
        if args and isinstance(args[0], str):
            name = args[0]
            context = args[1] if len(args) > 1 else kwargs.pop("context", {})
            request = kwargs.pop("request", None) or (context.get("request") if isinstance(context, dict) else None)
            status_code = args[2] if len(args) > 2 else kwargs.pop("status_code", 200)
            return super().TemplateResponse(
                request=request,
                name=name,
                context=context,
                status_code=status_code,
                **kwargs,
            )
        return super().TemplateResponse(*args, **kwargs)


templates = CompatibleJinja2Templates(directory=str(TEMPLATES_DIR))


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
