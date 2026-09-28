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
    # Login name the client sent ("UNDEF"/"" when it sent none).
    username: str = ""

    @property
    def identity(self) -> str:
        """Who this session belongs to: the certificate CN, or -- for a
        password-only client that presented no certificate, whose CN
        OpenVPN reports as UNDEF -- the username it logged in with."""
        return self.common_name if self.common_name != "UNDEF" else self.username


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
        username = parts[9] if len(parts) > 9 and parts[9] != "UNDEF" else ""
        if parts[1] == "UNDEF" and not username:
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
                username=username,
            )
        )
    return sessions


def kill_client(port: int, real_address: str, client_id: int | None = None) -> bool:
    """Disconnect one session. With a client id we send HALT, which tells
    the OpenVPN client to stop instead of immediately reconnecting (and
    getting rejected again) in a loop. `kill ip:port` is the fallback; it
    also works for certificate-less sessions, which have no CN to kill by."""
    if client_id is not None and client_id >= 0:
        lines = _talk(port, f"client-kill {client_id} HALT")
        if any(l.startswith("SUCCESS") for l in lines):
            return True
    lines = _talk(port, f"kill {real_address}")
    return any(l.startswith("SUCCESS") for l in lines)


def kill_everywhere(ports, identity: str) -> None:
    """Disconnect every session of one user, on every instance."""
    wanted = identity.lower()
    for port in ports:
        try:
            for s in get_client_sessions(port):
                if s.identity.lower() == wanted:
                    kill_client(port, s.real_address, s.client_id)
        except ManagementError:
            pass


def is_ready(port: int) -> bool:
    """The instance finished starting up (keys loaded, port bound)."""
    try:
        return any(",CONNECTED," in line for line in _talk(port, "state"))
    except ManagementError:
        return False


def send_signal(port: int, signal: str) -> bool:
    try:
        return any(l.startswith("SUCCESS") for l in _talk(port, f"signal {signal}"))
    except ManagementError:
        return False


def is_reachable(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=2):
            return True
    except OSError:
        return False
