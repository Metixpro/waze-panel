"""Web app manifests: phones can install the panel (and each user's
subscription page) as an app with its own home-screen icon."""
from fastapi.responses import JSONResponse

_ICONS = [
    {"src": "/static/icons/icon-192.png", "sizes": "192x192", "type": "image/png"},
    {"src": "/static/icons/icon-512.png", "sizes": "512x512", "type": "image/png"},
    {"src": "/static/icons/icon-maskable-512.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable"},
]


def web_manifest(name: str, short_name: str, start_url: str, scope: str) -> JSONResponse:
    """Lets phones install the page as an app (home-screen icon, no
    browser bars)."""
    return JSONResponse(
        {
            "name": name,
            "short_name": short_name[:12],
            "start_url": start_url,
            "scope": scope,
            "display": "standalone",
            "orientation": "portrait",
            "dir": "rtl",
            "lang": "fa",
            "background_color": "#0b0e17",
            "theme_color": "#0b0e17",
            "icons": _ICONS,
        },
        media_type="application/manifest+json",
    )
