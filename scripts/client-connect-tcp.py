#!/usr/bin/env python3
"""OpenVPN --client-connect hook for the TCP instance (see hook_common)."""
from hook_common import run_connect

run_connect("tcp")
