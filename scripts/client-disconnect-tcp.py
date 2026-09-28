#!/usr/bin/env python3
"""OpenVPN --client-disconnect hook for the TCP instance (see hook_common)."""
from hook_common import run_disconnect

run_disconnect("tcp")
