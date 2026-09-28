"""Minimal client for the OpenVPN management interface (plain TCP, local
only). Used to read live connected-client stats and to force-disconnect a
client whose quota/expiry ran out while a session is still open."""
import socket
from dataclasses import dataclass


class ManagementError(RuntimeError):
    pass


@dataclass
class ClientSession:
    common_name: str
    real_address: str
    virtual_address: str
    bytes_received: int
    bytes_sent: int
    connected_since: str


def _talk(port: int, command: str, stop_prefixes=("END", "SUCCESS:", "ERROR:")) -> list[str]:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=5) as sock:
            sock.settimeout(5)
            f = sock.makefile("r", newline="\n")
            # Drain the initial ">INFO:..." banner line.
            f.readline()

            sock.sendall((command + "\n").encode())

            lines: list[str] = []
            while True:
                line = f.readline()
                if line == "":
                    break
                line = line.rstrip("\r\n")
                if any(line.startswith(p) for p in stop_prefixes):
                    if line != "END":
                        lines.append(line)
                    break
                lines.append(line)
            return lines
    except (OSError, socket.timeout) as exc:
        raise ManagementError(str(exc)) from exc


def get_client_sessions(port: int) -> list[ClientSession]:
    lines = _talk(port, "status 3")
    sessions: list[ClientSession] = []
    for line in lines:
        parts = line.split(",")
        if not parts or parts[0] != "CLIENT_LIST":
            continue
        # CLIENT_LIST,cn,real_addr,virt_addr,virt_ipv6,bytes_recv,bytes_sent,since,...
        try:
            sessions.append(
                ClientSession(
                    common_name=parts[1],
                    real_address=parts[2],
                    virtual_address=parts[3],
                    bytes_received=int(parts[5]),
                    bytes_sent=int(parts[6]),
                    connected_since=parts[7] if len(parts) > 7 else "",
                )
            )
        except (IndexError, ValueError):
            continue
    return sessions


def kill_client(port: int, common_name: str) -> bool:
    lines = _talk(port, f"kill {common_name}")
    return any(l.startswith("SUCCESS") for l in lines)


def is_reachable(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=2):
            return True
    except OSError:
        return False
