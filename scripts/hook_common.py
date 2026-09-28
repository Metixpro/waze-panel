"""Shared helpers for the OpenVPN client-connect / client-disconnect hook
scripts. Deliberately stdlib-only (no venv / pip deps) since OpenVPN calls
these directly as root/nobody, outside of the panel's virtualenv.

Reads /etc/waze-panel/panel.env for PANEL_PORT and INTERNAL_TOKEN, then
talks to the panel's own localhost-only /internal/* API.
"""
import json
import os
import sys
import urllib.error
import urllib.request

ENV_FILE = "/etc/waze-panel/panel.env"


def load_env() -> dict:
    values = {"PANEL_PORT": "8000", "INTERNAL_TOKEN": ""}
    try:
        with open(ENV_FILE, "r") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if key in values:
                    values[key] = value
    except OSError:
        pass
    return values


def call_internal(path: str, payload: dict, timeout: float = 3.0):
    env = load_env()
    url = f"http://127.0.0.1:{env['PANEL_PORT']}{path}"
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "X-Internal-Token": env["INTERNAL_TOKEN"],
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ValueError) as exc:
        print(f"waze-panel hook: could not reach panel ({exc})", file=sys.stderr)
        return None


def env_var(name: str, default: str = "") -> str:
    return os.environ.get(name, default)
