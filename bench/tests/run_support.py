from __future__ import annotations

import pathlib
import shlex

from moq_trace.metadata import CommandSet, Counts, Processes, RunMetadata, TransportCapabilities, Window, Workload
from moq_trace.run import hosts, local

from moq_workspace_bench import placement, ssh
from moq_workspace_bench.config import ExperimentConfig, HostConfig


def placement_for(config: ExperimentConfig, output: pathlib.Path | None = None) -> placement.Placement:
    return placement.Placement(config, output or config.output, ssh.SshPool())


def peer(*lines: str, exit_status: int | None = None) -> tuple[str, ...]:
    """A stand-in role that prints `lines`, then runs until SIGINT, which it exits 0 on.

    With `exit_status`, it exits with that status right after printing instead.
    """

    body = "".join(f"echo {shlex.quote(line)}; " for line in lines)
    if exit_status is not None:
        return ("sh", "-c", f"{body}exit {exit_status}")
    return ("sh", "-c", f"trap 'exit 0' INT; {body}while :; do sleep 0.02; done")


def command_set(relay=None, publisher=None, subscriber=None, subscribers: int = 1) -> CommandSet:
    return CommandSet(
        relay=relay or peer("listening"),
        publisher=publisher or peer("connections=1 subscriptions=0"),
        subscriber=subscriber or peer(f"connections={subscribers} subscriptions={subscribers}"),
    )


class FakeRemote(local.LocalHost):
    """A remote host whose processes run locally and whose ssh traffic is recorded.

    Its run directory is under the configured workdir, which a test points at a
    temporary directory, so the paths a run uses there are real.
    """

    def __init__(self, config: HostConfig, _pool) -> None:
        self.config = config
        self.name = config.ssh
        self.scripts: list[str] = []
        self.fetched: list[tuple[str, list[str]]] = []

    async def _shell(self, script, timeout):
        self.scripts.append(script)
        if " -c " in script and "clock_gettime_ns" in script:
            return await super()._shell(script, timeout)
        return hosts.Completed(0, "", "")

    async def fetch(self, directory, names, destination):
        self.fetched.append((directory, list(names)))

    def run_directory(self, output, suffix):
        return f"{self.config.workdir}/{output.name}-{suffix}"

    def qualify(self, path):
        return f"{self.name}:{path}"

    def binary(self, value, checkout):
        return value

    def path(self, value, base=None):
        return value


def run_metadata(**overrides) -> RunMetadata:
    """Build a complete run artifact, as the analyzer writes one."""

    fields = {
        "workload": Workload(subscribers=1, object_size=1024),
        "window": Window(warmup_seconds=0.0, cooldown_seconds=0.0),
        "transport_profile": "generic",
        "transport_capabilities": TransportCapabilities(packet_phases=()),
        "counts": Counts(
            groups=1,
            packets=1,
            selected_packets=1,
            correlated_objects=1,
            correlated_object_copies=1,
            copy_outcomes={"success": 1},
        ),
        "processes": Processes(process_id=0, analyzed_pid=0, captured_pids=(0,)),
    }
    fields.update(overrides)
    return RunMetadata(**fields)
