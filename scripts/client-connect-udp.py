#!/usr/bin/env python3
"""OpenVPN --client-connect hook for the UDP instance.
Exit 0 = allow the connection, non-zero = OpenVPN rejects it."""
import sys

from hook_common import call_internal, env_var

common_name = env_var("common_name")
if not common_name:
    sys.exit(1)

result = call_internal("/internal/hooks/connect", {"common_name": common_name, "proto": "udp"})

# Fail closed: if the panel can't be reached we refuse the connection
# rather than silently letting through a user we can no longer check.
if result and result.get("allow"):
    sys.exit(0)
sys.exit(1)
