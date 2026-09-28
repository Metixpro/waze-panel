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
    # Unix time the session started: together with the CN this uniquely
    # identifies one connection, even across quick reconnects.
    connected_since_t: int
    client_id: int | None


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
                if line.startswith(">"):
                    # asynchronous real-time notification, not part of the reply
                    continue
                if any(line.startswith(p) for p in stop_prefixes):
                    if line != "END":
                        lines.append(line)
                    break
                lines.append(line)
            return lines
    except (OSError, socket.timeout) as exc:
        raise ManagementError(str(exc)) from exc


def _to_int(value: str, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def get_client_sessions(port: int) -> list[ClientSession]:
    # `status 3` is the same layout as `status 2` but TAB-separated, which
    # is safer to parse than commas.
    lines = _talk(port, "status 3")
    sessions: list[ClientSession] = []
    for line in lines:
        parts = line.split("\t") if "\t" in line else line.split(",")
        if not parts or parts[0] != "CLIENT_LIST" or len(parts) < 9:
            continue
        # CLIENT_LIST, CN, real addr, virt addr, virt ipv6, bytes recv,
        # bytes sent, connected since, connected since (time_t), username,
        # client id, peer id, data channel cipher
        if parts[1] == "UNDEF":
            continue  # handshake not finished yet
        sessions.append(
            ClientSession(
                common_name=parts[1],
                real_address=parts[2],
                virtual_address=parts[3],
                bytes_received=_to_int(parts[5]),
                bytes_sent=_to_int(parts[6]),
                connected_since=parts[7],
                connected_since_t=_to_int(parts[8]),
                client_id=_to_int(parts[10], -1) if len(parts) > 10 else None,
            )
        )
    return sessions


def kill_client(port: int, common_name: str, client_id: int | None = None) -> bool:
    """Disconnect a client. With a client id we send HALT, which tells the
    OpenVPN client to stop instead of immediately reconnecting (and getting
    rejected again) in a loop."""
    if client_id is not None and client_id >= 0:
        lines = _talk(port, f"client-kill {client_id} HALT")
        if any(l.startswith("SUCCESS") for l in lines):
            return True
    lines = _talk(port, f"kill {common_name}")
    return any(l.startswith("SUCCESS") for l in lines)


def kill_everywhere(ports, common_name: str) -> None:
    for port in ports:
        try:
            for s in get_client_sessions(port):
                if s.common_name == common_name:
                    kill_client(port, common_name, s.client_id)
        except ManagementError:
            pass


def is_reachable(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=2):
            return True
    except OSError:
        return False
