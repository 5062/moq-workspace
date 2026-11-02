"""UDP socket buffer sizes on the run's hosts."""

from __future__ import annotations

import pathlib

from ..capture.hosts import Host
from ..errors import CaptureError
from .tcpdump import SUDO_PASSWORD_ENVIRONMENT, take_sudo_password

_BUFFERS = ("rmem_default", "rmem_max", "wmem_default", "wmem_max")
# Large enough that a relay fanning out to many subscribers neither drops
# received datagrams nor stalls on sends while its event loop is busy.
RUN_BUFFERS = dict.fromkeys(_BUFFERS, 8_388_608)


async def socket_buffers(host: Host) -> dict[str, int]:
    """Read `host`'s socket buffers, which a run returns it to afterwards."""

    paths = " ".join(f"/proc/sys/net/core/{name}" for name in _BUFFERS)
    return dict(zip(_BUFFERS, map(int, (await host.run(f"cat {paths}")).split()), strict=True))


def sysctl_command(sizes: dict[str, int], password: bool = False) -> list[str]:
    """Set the default and maximum socket buffers to `sizes` under sudo.

    The relays and peers request no buffer size, so their sockets get the
    defaults, and the maxima bound what any of them could request.
    """

    sudo = ["sudo", "-S", "-p", ""] if password else ["sudo", "-n"]
    return [*sudo, "sysctl", "-w", *(f"net.core.{name}={size}" for name, size in sizes.items())]


async def set_socket_buffers(host: Host, sizes: dict[str, int], cwd: str, log: pathlib.Path) -> None:
    """Set `host`'s socket buffers to `sizes`, with the sudo password on stdin when one was given."""

    password = take_sudo_password()
    stdin = None if password is None else f"{password}\n".encode()
    process = await host.start("sysctl", sysctl_command(sizes, password is not None), cwd, log, stdin=stdin)
    status = await process.wait()
    if status != 0:
        raise CaptureError(
            f"setting socket buffers on {host.name} failed with status {status}; sysctl needs sudo: "
            f"set {SUDO_PASSWORD_ENVIRONMENT} or allow it without a password (see {log})"
        )
