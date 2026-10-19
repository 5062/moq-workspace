"""Persistent per-host resource collectors and portable clock provenance."""

from __future__ import annotations

import json
import pathlib
import shlex
import time
from importlib.resources import files

from ..capture.hosts import Host, Process
from ..manifest import MONITORING_MANIFEST, MonitoringHost, MonitoringManifest
from .config import ExperimentConfig
from .placement import Placement


async def _clock(host: Host) -> tuple[int, int, int]:
    """Bracket a host clock reading on the controller; no wall-clock synchronization."""

    probe = "import time; print(time.clock_gettime_ns(time.CLOCK_MONOTONIC))"
    before = time.clock_gettime_ns(time.CLOCK_MONOTONIC)
    instant = int(await host.run(f"{shlex.quote(host.python)} -c {shlex.quote(probe)}"))
    after = time.clock_gettime_ns(time.CLOCK_MONOTONIC)
    return instant, instant - (before + after) // 2, (after - before + 1) // 2


class Monitoring:
    def __init__(self, config: ExperimentConfig, placement: Placement, roles: dict[str, Process]) -> None:
        self.config = config
        self.placement = placement
        self.roles = roles
        self.collectors: list[Process] = []
        self.hosts: list[MonitoringHost] = []

    async def start(self) -> None:
        groups: dict[Host, dict[str, int]] = {}
        for role, process in self.roles.items():
            groups.setdefault(self.placement.host(role), {})[role] = process.pid
        clocks = {host: await _clock(host) for host in groups}
        _, relay_offset, relay_error = clocks[self.placement.relay]
        source = files("moq_trace.capture").joinpath("resources.py").read_text()
        for index, (host, roles) in enumerate(groups.items()):
            measured = {role: pid for role, pid in roles.items() if role == "relay" or self.config.monitor_peers}
            path = None
            if self.config.monitor_resources and measured:
                path = f"resources-{index}.jsonl"
                process = await host.start(
                    "resource collector",
                    [host.python, "-u", "-c", source, json.dumps(measured), str(self.config.monitor_interval_seconds)],
                    self.placement.directory(host),
                    self.placement.output / path,
                )
                self.collectors.append(process)
                await process.wait_for_line(lambda line: line.startswith('{"schema_version":'), "resource sample", 10)
            _, offset, error = clocks[host]
            self.hosts.append(
                MonitoringHost(
                    host=host.name,
                    roles=roles,
                    resources=path,
                    resource_roles=tuple(measured) if path is not None else (),
                    offset_ns=0 if host is self.placement.relay else offset - relay_offset,
                    uncertainty_ns=0 if host is self.placement.relay else error + relay_error,
                )
            )
        self.start_ns = (await _clock(self.placement.relay))[0]

    async def finish(self) -> pathlib.Path:
        end_ns = (await _clock(self.placement.relay))[0]
        for process in self.collectors:
            await process.stop(True)
        manifest = MonitoringManifest(
            hosts=tuple(self.hosts),
            start_ns=self.start_ns + round(self.config.warmup_seconds * 1e9),
            end_ns=end_ns - round(self.config.cooldown_seconds * 1e9),
            interval_seconds=self.config.monitor_interval_seconds,
            subscribers=self.config.subscribers,
            goodput=self.config.monitor_goodput,
        )
        path = self.placement.output / MONITORING_MANIFEST
        path.write_text(manifest.model_dump_json(indent=2) + "\n")
        return path

    async def close(self) -> None:
        for process in self.collectors:
            await process.close()
