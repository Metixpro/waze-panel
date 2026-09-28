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
import time
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


def call_internal(path: str, payload: dict, timeout: float = 3.0, retry_for: float = 8.0):
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
    # The panel may still be starting (reboot, update): a refused connection
    # is retried for a few seconds, otherwise the client would get
    # AUTH_FAILED and -- by default -- give up instead of retrying.
    deadline = time.monotonic() + retry_for
    while True:
        try:
            with _OPENER.open(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            print(f"waze-panel hook: panel answered {exc.code}", file=sys.stderr)
            return None
        except (urllib.error.URLError, TimeoutError, ValueError) as exc:
            refused = isinstance(getattr(exc, "reason", None), ConnectionRefusedError)
            if refused and time.monotonic() < deadline:
                time.sleep(0.5)
                continue
            print(f"waze-panel hook: could not reach panel ({exc})", file=sys.stderr)
            return None


def env_var(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


def _int_env(name: str) -> int:
    try:
        return int(env_var(name, "0") or 0)
    except ValueError:
        return 0


def _session() -> dict:
    # Without a client certificate OpenVPN leaves common_name unset; the
    # username the client logged in with identifies it instead.
    return {
        "common_name": env_var("common_name"),
        "username": env_var("username"),
        "real_address": f"{env_var('trusted_ip')}:{env_var('trusted_port')}",
        "start_t": _int_env("time_unix"),
    }


def _tell_client(result) -> None:
    """OpenVPN 2.6 sends this text to the client with AUTH_FAILED, so the
    app can say *why* (expired, data limit, wrong password...)."""
    path = env_var("auth_failed_reason_file")
    message = (result or {}).get("message")
    if path and message:
        try:
            with open(path, "w") as f:
                f.write(str(message)[:200])
        except OSError:
            pass


def run_auth(proto: str) -> None:
    """--auth-user-pass-verify via-file: argv[1] holds username + password.
    Called for every client (auth-user-pass-optional), with or without a
    certificate and with or without credentials."""
    username = password = ""
    if len(sys.argv) > 1:
        try:
            with open(sys.argv[1], "r", encoding="utf-8", errors="replace") as f:
                lines = f.read().splitlines()
            username = (lines[0] if lines else "").strip()
            password = lines[1] if len(lines) > 1 else ""
        except OSError:
            pass
    result = call_internal(
        "/internal/hooks/auth",
        {
            "proto": proto,
            "common_name": env_var("common_name"),
            "username": username,
            "password": password,
            "ip": env_var("untrusted_ip"),
        },
    )
    # Fail closed: if the panel can't be reached we refuse the connection.
    if result and result.get("allow"):
        sys.exit(0)
    _tell_client(result)
    sys.exit(1)


def run_connect(proto: str) -> None:
    """--client-connect: exit 0 = allow, non-zero = OpenVPN rejects it."""
    payload = _session()
    if not payload["common_name"] and not payload["username"]:
        sys.exit(1)
    result = call_internal("/internal/hooks/connect", dict(payload, proto=proto))
    # Fail closed: if the panel can't be reached we refuse the connection
    # rather than silently letting through a user we can no longer check.
    if result and result.get("allow"):
        sys.exit(0)
    _tell_client(result)
    sys.exit(1)


def run_disconnect(proto: str) -> None:
    """--client-disconnect: reports the session's final byte counters so
    usage is exact, even for sessions shorter than the panel's poll
    interval. Always exits 0 -- a reporting failure must never block a
    client from disconnecting cleanly."""
    payload = _session()
    if payload["common_name"] or payload["username"]:
        payload.update(
            proto=proto,
            bytes_sent=_int_env("bytes_sent"),
            bytes_received=_int_env("bytes_received"),
        )
        call_internal("/internal/hooks/disconnect", payload, retry_for=3.0)
    sys.exit(0)
