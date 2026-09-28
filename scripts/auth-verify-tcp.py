#!/usr/bin/env python3
"""OpenVPN --auth-user-pass-verify hook for the TCP instance (see hook_common)."""
from hook_common import run_auth

run_auth("tcp")
