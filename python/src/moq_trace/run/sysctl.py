"""UDP socket buffer sizes on the run's hosts."""

from __future__ import annotations

import pathlib

from ..capture.hosts import Host
from ..errors import CaptureError
from .tcpdump import SUDO_PASSWORD_ENVIRONMENT, sudo

_BUFFERS = ("rmem_default", "rmem_max", "wmem_default", "wmem_max")
# Large enough that a relay fanning out to many subscribers neither drops
# received datagrams nor stalls on sends while its event loop is busy.
RUN_BUFFERS = dict.fromkeys(_BUFFERS, 8_388_608)


async def socket_buffers(host: Host) -> dict[str, int]:
    """Read `host`'s socket buffers, which a run returns it to afterwards."""

    paths = " ".join(f"/proc/sys/net/core/{name}" for name in _BUFFERS)
    return dict(zip(_BUFFERS, map(int, (await host.run(f"cat {paths}")).split()), strict=True))


def sysctl_command(sizes: dict[str, int], prefix: list[str]) -> list[str]:
    """Set the default and maximum socket buffers to `sizes` under the sudo `prefix`.

    The relays and peers request no buffer size, so their sockets get the
    defaults, and the maxima bound what any of them could request.
    """

    return [*prefix, "sysctl", "-w", *(f"net.core.{name}={size}" for name, size in sizes.items())]


async def set_socket_buffers(host: Host, sizes: dict[str, int], cwd: str, log: pathlib.Path) -> None:
    """Set `host`'s socket buffers to `sizes`, with the sudo password on stdin when one was given."""

    prefix, stdin = sudo()
    process = await host.start("sysctl", sysctl_command(sizes, prefix), cwd, log, stdin=stdin)
    status = await process.wait()
    if status != 0:
        raise CaptureError(
            f"setting socket buffers on {host.name} failed with status {status}; sysctl needs sudo: "
            f"set {SUDO_PASSWORD_ENVIRONMENT} or allow it without a password (see {log})"
        )
