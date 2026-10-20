"""UDP socket buffer sizes on the run's hosts."""

from __future__ import annotations

import pathlib

from ..capture.hosts import Host
from ..errors import CaptureError
from .tcpdump import SUDO_PASSWORD_ENVIRONMENT, take_sudo_password

# Large enough that a relay fanning out to many subscribers neither drops
# received datagrams nor stalls on sends while its event loop is busy.
RUN_BUFFER_BYTES = 8_388_608
# The kernel's default, which every host is returned to after a run.
DEFAULT_BUFFER_BYTES = 212_992


def sysctl_command(size: int, password: bool = False) -> list[str]:
    """Set the default and maximum socket buffers to `size` under sudo.

    The relays and peers request no buffer size, so their sockets get the
    defaults, and the maxima bound what any of them could request.
    """

    sudo = ["sudo", "-S", "-p", ""] if password else ["sudo", "-n"]
    settings = [f"net.core.{name}={size}" for name in ("rmem_default", "rmem_max", "wmem_default", "wmem_max")]
    return [*sudo, "sysctl", "-w", *settings]


async def set_socket_buffers(host: Host, size: int, cwd: str, log: pathlib.Path) -> None:
    """Set `host`'s socket buffers to `size`, with the sudo password on stdin when one was given."""

    password = take_sudo_password()
    stdin = None if password is None else f"{password}\n".encode()
    process = await host.start("sysctl", sysctl_command(size, password is not None), cwd, log, stdin=stdin)
    status = await process.wait()
    if status != 0:
        raise CaptureError(
            f"setting socket buffers on {host.name} failed with status {status}; sysctl needs sudo: "
            f"set {SUDO_PASSWORD_ENVIRONMENT} or allow it without a password (see {log})"
        )
