"""Data behind the About page: who builds the panel, what it is built on,
and whether a newer version is out.

Network access is optional and never blocks anything important: GitHub is
asked at most once an hour (plus a manual "check now"), with short
timeouts, and the last good answer is cached on disk so the page and the
update badge still work after a restart or while GitHub is unreachable.
The update check reads the version and changelog straight from the main
branch, so it needs no GitHub releases and no API token.
"""
import json
import logging
import platform
import re
import subprocess
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache

from sqlalchemy.orm import Session

from app.config import settings
from app.database import SessionLocal
from app.models import Setting
from app.version import RAW_BASE, REPO, REPO_URL, ROOT, __version__, build_info, parse_version, version_from_source

logger = logging.getLogger(__name__)

DEVELOPER = {
    "name": "مهدی طوسی",
    "name_en": "Mahdi Toossi",
    "login": "Metixpro",
    "role": "طراح و توسعه‌دهنده",
    # Shown until GitHub answers with the profile's own bio.
    "bio": "طراح و توسعه‌دهنده‌ی Waze Panel؛ از هسته‌ی اتصال و اسکریپت نصب تا تک‌تک صفحه‌های همین پنل.",
    "avatar": "/static/img/developer.jpg",
    "github": "https://github.com/Metixpro",
    # Optional contact links; empty ones are not shown.
    "telegram": "",
    "website": "",
}

# What the panel is built on, shown with their licenses.
CREDITS = [
    {"name": "OpenVPN", "role": "هسته‌ی VPN", "license": "GPL-2.0", "url": "https://openvpn.net"},
    {"name": "Easy-RSA", "role": "صدور و ابطال گواهی‌ها", "license": "GPL-2.0", "url": "https://github.com/OpenVPN/easy-rsa"},
    {"name": "Xray-core", "role": "VLESS، VMess، Trojan و Shadowsocks", "license": "MPL-2.0", "url": "https://github.com/XTLS/Xray-core"},
    {"name": "FastAPI", "role": "بک‌اند پنل", "license": "MIT", "url": "https://fastapi.tiangolo.com"},
    {"name": "Uvicorn", "role": "وب‌سرور", "license": "BSD-3", "url": "https://www.uvicorn.org"},
    {"name": "SQLAlchemy", "role": "پایگاه‌داده", "license": "MIT", "url": "https://www.sqlalchemy.org"},
    {"name": "APScheduler", "role": "کارهای زمان‌بندی‌شده", "license": "MIT", "url": "https://github.com/agronholm/apscheduler"},
    {"name": "psutil", "role": "آمار سرور", "license": "BSD-3", "url": "https://github.com/giampaolo/psutil"},
    {"name": "Chart.js", "role": "نمودارها", "license": "MIT", "url": "https://www.chartjs.org"},
    {"name": "qrcodejs", "role": "کد QR", "license": "MIT", "url": "https://github.com/davidshimjs/qrcodejs"},
    {"name": "Vazirmatn", "role": "فونت فارسی", "license": "OFL-1.1", "url": "https://github.com/rastikerdar/vazirmatn"},
]

AUTO_CHECK_KEY = "ABOUT_AUTO_CHECK"
CACHE_FILE = settings.DATA_DIR / "about-cache.json"
OWNER = REPO.split("/")[0]

_FRESH_FOR = 3600  # seconds a successful answer is reused
_RETRY_AFTER = 600  # seconds before retrying after GitHub was unreachable
_MANUAL_GAP = 15  # "check now" is ignored when pressed more often than this

_lock = threading.Lock()
_data: dict | None = None  # last successful answer
_loaded = False
_last_attempt = 0.0
_last_error = ""


# ------------------------------------------------------------------ changelog
_HEAD_RE = re.compile(r"^##\s+v?(\d+\.\d+\.\d+)\s*(?:\((\d{4}-\d{2}-\d{2})\))?\s*(?:[—–-]\s*(.+?))?\s*$")


def parse_changelog(text: str) -> list[dict]:
    """`## 1.2.0 (2026-01-31) — Title` headings followed by `- item` lines."""
    entries: list[dict] = []
    current = None
    for line in text.splitlines():
        m = _HEAD_RE.match(line.strip())
        if m:
            current = {"version": m[1], "date": m[2] or "", "title": (m[3] or "")[:80], "items": []}
            entries.append(current)
        elif line.startswith("#"):
            current = None
        elif current is not None and line.lstrip().startswith(("- ", "* ")):
            current["items"].append(line.strip()[2:].strip()[:300])
    return entries[:40]


@lru_cache(maxsize=1)
def local_changelog() -> list[dict]:
    try:
        return parse_changelog((ROOT / "CHANGELOG.md").read_text(encoding="utf-8"))
    except OSError:
        return []


# ---------------------------------------------------------------- this server
@lru_cache(maxsize=1)
def system_info() -> dict:
    info = {"python": platform.python_version(), "openvpn": "", "os": ""}
    try:
        out = subprocess.run(["openvpn", "--version"], capture_output=True, text=True, timeout=3).stdout
        m = re.search(r"OpenVPN\s+(\d+\.\d+(?:\.\d+)?)", out)
        info["openvpn"] = m[1] if m else ""
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        info["os"] = platform.freedesktop_os_release().get("PRETTY_NAME", "")
    except (OSError, AttributeError):
        info["os"] = platform.system()
    return info


# --------------------------------------------------------------------- options
def auto_check_enabled(db: Session) -> bool:
    row = db.get(Setting, AUTO_CHECK_KEY)
    return row is None or row.value != "0"


def set_auto_check(db: Session, enabled: bool) -> None:
    row = db.get(Setting, AUTO_CHECK_KEY)
    value = "1" if enabled else "0"
    if row is None:
        db.add(Setting(key=AUTO_CHECK_KEY, value=value))
    else:
        row.value = value
    db.commit()


# ---------------------------------------------------------------------- GitHub
def _get(url: str, timeout: float = 6.0) -> bytes:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": f"waze-panel/{__version__}", "Accept": "application/vnd.github+json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (fixed https URLs)
        return resp.read(2_000_000)


def _clean_url(value) -> str:
    value = (value or "").strip()
    if value and not value.startswith(("http://", "https://")):
        value = "https://" + value
    return value if re.fullmatch(r"https?://[^\s\"'<>]{1,200}", value) else ""


def _text(value, limit: int = 200) -> str:
    return str(value or "").strip()[:limit]


def _collect() -> dict:
    urls = {
        "repo": f"https://api.github.com/repos/{REPO}",
        "owner": f"https://api.github.com/users/{OWNER}",
        "head": f"https://api.github.com/repos/{REPO}/commits/main",
        "version": f"{RAW_BASE}/app/version.py",
        "changelog": f"{RAW_BASE}/CHANGELOG.md",
    }
    raw: dict[str, bytes] = {}
    with ThreadPoolExecutor(max_workers=len(urls)) as pool:
        futures = {key: pool.submit(_get, url) for key, url in urls.items()}
        for key, future in futures.items():
            try:
                raw[key] = future.result()
            except Exception as exc:  # network, HTTP error, timeout
                logger.info("about: %s unavailable (%s)", key, exc)
    if not raw:
        raise RuntimeError("github unreachable")

    out: dict = {"fetched_at": time.time()}
    try:
        r = json.loads(raw["repo"])
        out["repo"] = {
            "stars": int(r.get("stargazers_count") or 0),
            "forks": int(r.get("forks_count") or 0),
            "watchers": int(r.get("subscribers_count") or 0),
            "issues": int(r.get("open_issues_count") or 0),
            "pushed_at": _text(r.get("pushed_at"), 40),
            "license": _text((r.get("license") or {}).get("spdx_id"), 40),
        }
    except (KeyError, ValueError, TypeError, AttributeError):
        pass
    try:
        u = json.loads(raw["owner"])
        out["owner"] = {
            "name": _text(u.get("name"), 80),
            "bio": _text(u.get("bio"), 240),
            "location": _text(u.get("location"), 80),
            "company": _text(u.get("company"), 80),
            "blog": _clean_url(u.get("blog")),
            "twitter": _text(u.get("twitter_username"), 40),
            "followers": int(u.get("followers") or 0),
            "repos": int(u.get("public_repos") or 0),
        }
    except (KeyError, ValueError, TypeError, AttributeError):
        pass
    try:
        c = json.loads(raw["head"])
        out["head"] = {
            "sha": _text(c["sha"], 40),
            "date": _text(c["commit"]["committer"]["date"], 40),
            "message": _text(c["commit"]["message"].splitlines()[0], 160),
        }
    except (KeyError, ValueError, TypeError, AttributeError, IndexError):
        pass
    if "version" in raw:
        out["latest_version"] = version_from_source(raw["version"].decode("utf-8", "replace")) or ""
    if "changelog" in raw:
        out["changelog"] = parse_changelog(raw["changelog"].decode("utf-8", "replace"))

    # Same version but the server runs an older commit (small fixes that
    # did not bump the version): list what it is missing.
    local = build_info()["commit"]
    head = out.get("head", {}).get("sha", "")
    if local and head and local != head:
        try:
            cmp = json.loads(_get(f"https://api.github.com/repos/{REPO}/compare/{local}...{head}"))
            if cmp.get("status") in ("ahead", "diverged") and int(cmp.get("ahead_by") or 0) > 0:
                commits = []
                for item in reversed(cmp.get("commits") or []):
                    msg = (item.get("commit") or {}).get("message") or ""
                    if msg.startswith("Merge "):
                        continue
                    commits.append({
                        "sha": _text(item.get("sha"), 40),
                        "message": _text(msg.splitlines()[0] if msg else "", 160),
                        "date": _text(((item.get("commit") or {}).get("committer") or {}).get("date"), 40),
                    })
                out["behind"] = {"base": local, "count": int(cmp["ahead_by"]), "commits": commits[:15]}
        except Exception as exc:
            logger.info("about: compare unavailable (%s)", exc)
    return out


def _load_cache() -> None:
    global _data, _loaded
    if _loaded:
        return
    _loaded = True
    try:
        data = json.loads(CACHE_FILE.read_text())
        if isinstance(data, dict) and data.get("fetched_at"):
            _data = data
    except (OSError, ValueError):
        pass


def refresh(force: bool = False) -> None:
    """Ask GitHub again if the cached answer is old (or `force`)."""
    global _data, _last_attempt, _last_error
    with _lock:
        _load_cache()
        now = time.time()
        if force:
            if now - _last_attempt < _MANUAL_GAP:
                return
        else:
            fresh = _data and now - _data.get("fetched_at", 0) < _FRESH_FOR
            if fresh or now - _last_attempt < _RETRY_AFTER:
                return
        _last_attempt = now
        try:
            _data = _collect()
            _last_error = ""
        except Exception as exc:
            _last_error = "unreachable"
            logger.info("about: GitHub check failed (%s)", exc)
            return
        try:
            CACHE_FILE.write_text(json.dumps(_data, ensure_ascii=False))
        except OSError:
            pass


def scheduled_refresh() -> None:
    db = SessionLocal()
    try:
        enabled = auto_check_enabled(db)
    finally:
        db.close()
    if enabled:
        refresh()


# ---------------------------------------------------------------------- output
def _behind_now(data: dict) -> bool:
    """The cached "N commits behind" still describes the commit we run
    (not the one before an update that happened within the cache's hour)."""
    behind = data.get("behind") or {}
    return bool(behind.get("count")) and behind.get("base") == build_info()["commit"]


def update_status(data: dict | None) -> dict:
    status = {
        "current": __version__,
        "latest": "",
        "available": False,
        "kind": "",
        "entries": [],
        "commits": [],
        "behind": 0,
    }
    if not data:
        return status
    local = parse_version(__version__)
    latest = parse_version(data.get("latest_version"))
    status["latest"] = data.get("latest_version") or ""
    if local and latest and latest > local:
        status.update(available=True, kind="release")
        status["entries"] = [
            e for e in data.get("changelog") or [] if (parse_version(e.get("version")) or (0, 0, 0)) > local
        ]
    elif local and latest and latest == local and _behind_now(data):
        status.update(available=True, kind="commits")
        status["behind"] = data["behind"]["count"]
        status["commits"] = data["behind"].get("commits") or []
    return status


def update_available() -> bool:
    """Cheap (memory only) — used for the badge in the navigation."""
    _load_cache()
    return update_status(_data)["available"]


def payload(db: Session, force: bool = False) -> dict:
    auto = auto_check_enabled(db)
    if force or auto:
        refresh(force=force)
    data = _data or {}
    return {
        "version": __version__,
        "build": build_info(),
        "repo": REPO,
        "repo_url": REPO_URL,
        "developer": DEVELOPER,
        "github": {k: data[k] for k in ("repo", "owner", "head") if k in data} or None,
        "update": update_status(_data),
        "checked_at": data.get("fetched_at", 0),
        "error": _last_error,
        "auto_check": auto,
        "system": system_info(),
    }
