"""Version and build information of this install.

`__version__` is bumped together with a new entry in CHANGELOG.md. The
commit the server runs is recorded by install.sh in the BUILD file; a git
checkout (development) is read directly as a fallback.
"""
import json
import re
import subprocess
from functools import lru_cache
from pathlib import Path

__version__ = "1.7.0"

REPO = "Metixpro/waze-panel"
REPO_URL = f"https://github.com/{REPO}"
RAW_BASE = f"https://raw.githubusercontent.com/{REPO}/main"

ROOT = Path(__file__).resolve().parent.parent

_VERSION_RE = re.compile(r"v?(\d+)\.(\d+)\.(\d+)")


def parse_version(value: str | None) -> tuple[int, int, int] | None:
    m = _VERSION_RE.fullmatch((value or "").strip())
    return (int(m[1]), int(m[2]), int(m[3])) if m else None


def version_from_source(text: str) -> str | None:
    """`__version__` out of the text of this very file (as fetched from
    GitHub for the update check)."""
    m = re.search(r'^__version__\s*=\s*["\']([^"\']+)["\']', text, re.MULTILINE)
    return m[1] if m and parse_version(m[1]) else None


@lru_cache(maxsize=1)
def build_info() -> dict:
    """{"commit": sha or "", "date": ISO commit date or "", "updated_at": ...}"""
    info = {"commit": "", "date": "", "updated_at": ""}
    try:
        data = json.loads((ROOT / "BUILD").read_text())
        for key in info:
            if isinstance(data.get(key), str):
                info[key] = data[key].strip()
    except (OSError, ValueError):
        pass
    if not info["commit"] and (ROOT / ".git").exists():
        try:
            out = subprocess.run(
                ["git", "-C", str(ROOT), "log", "-1", "--format=%H %cI"],
                capture_output=True, text=True, timeout=3,
            ).stdout.split()
            if len(out) == 2:
                info["commit"], info["date"] = out
        except (OSError, subprocess.SubprocessError):
            pass
    if not re.fullmatch(r"[0-9a-f]{40}", info["commit"]):
        info["commit"] = ""
    return info
