#!/usr/bin/env python3
"""OpenVPN --client-disconnect hook for the UDP instance. Reports the
final byte counters for the session so usage is accounted precisely, even
for sessions shorter than the panel's periodic poll interval. Always
exits 0 -- a reporting failure must never block a client from
disconnecting cleanly."""
from hook_common import call_internal, env_var

common_name = env_var("common_name")
if common_name:
    call_internal(
        "/internal/hooks/disconnect",
        {
            "common_name": common_name,
            "proto": "udp",
            "bytes_sent": int(env_var("bytes_sent", "0") or 0),
            "bytes_received": int(env_var("bytes_received", "0") or 0),
        },
    )
