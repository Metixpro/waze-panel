"""Shared helpers for the OpenVPN client-connect / client-disconnect hook
scripts. Deliberately stdlib-only (no venv / pip deps) since OpenVPN calls
these directly, outside of the panel's virtualenv.

OpenVPN runs these hooks *after* dropping privileges (`user nobody`,
`group nogroup`), so they cannot read the root-only panel.env. install.sh
therefore writes a separate hook.env holding just PANEL_PORT and
INTERNAL_TOKEN, readable by the nogroup group.
"""
import json
import os
import sys
import urllib.error
import urllib.request

ENV_FILES = ("/etc/waze-panel/hook.env", "/etc/waze-panel/panel.env")

# Never route the localhost call through an HTTP proxy that happens to be
# configured in the environment.
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def load_env() -> dict:
    values = {"PANEL_PORT": "8000", "INTERNAL_TOKEN": ""}
    for path in ENV_FILES:
        try:
            with open(path, "r") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    key, _, value = line.partition("=")
                    key = key.strip()
                    value = value.strip().strip('"').strip("'")
                    if key in values:
                        values[key] = value
            return values
        except OSError:
            continue
    print(
        "waze-panel hook: cannot read /etc/waze-panel/hook.env "
        f"(running as uid {os.getuid()}); re-run install.sh to fix permissions",
        file=sys.stderr,
    )
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
        with _OPENER.open(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ValueError) as exc:
        print(f"waze-panel hook: could not reach panel ({exc})", file=sys.stderr)
        return None


def env_var(name: str, default: str = "") -> str:
    return os.environ.get(name, default)
