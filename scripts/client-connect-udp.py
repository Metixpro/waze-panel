#!/usr/bin/env python3
"""OpenVPN --client-connect hook for the UDP instance (see hook_common)."""
from hook_common import run_connect

run_connect("udp")
