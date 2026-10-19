"""The descriptions of network and monitoring captures that the runner writes and the analyzer reads.

The runner and the analyzer share these contracts and nothing else about a
capture, so they live outside both: the run layer must not import the ingesters.
The monitoring summary the analyzer writes beside a run is described here too,
next to the manifest it summarizes.
"""

from __future__ import annotations

import pathlib
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .errors import TraceError

# File the runner writes into a run directory to describe its network capture.
MANIFEST = "network.json"

# Environment variable that tells a relay where to write qlog. Cloudflare quiche
# and several other QUIC stacks read the same name.
QLOG_ENVIRONMENT = "QLOGDIR"

MONITORING_MANIFEST = "monitoring.json"

# Human-readable summary the analyzer writes beside the monitoring manifest.
MONITORING_SUMMARY = "monitoring-summary.json"

Role = Literal["relay", "publisher", "subscriber"]


class MonitoringHost(BaseModel):
    """Host-local clocks mapped to the relay clock with an explicit error bound."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    host: str
    roles: dict[Role, int]
    # Host monotonic minus relay monotonic; zero on the relay host.
    offset_ns: int
    uncertainty_ns: int = Field(ge=0)
    resources: str | None = None
    resource_roles: tuple[Role, ...] = ()


class MonitoringManifest(BaseModel):
    """Portable sidecars, including endpoint logs, usable without a live host."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    schema_version: Literal[1] = 1
    hosts: tuple[MonitoringHost, ...]
    start_ns: int = Field(ge=0)
    end_ns: int = Field(ge=0)
    interval_seconds: float = Field(gt=0)
    subscribers: int = Field(gt=0)
    goodput: bool


class ThreadSample(BaseModel):
    """Raw procfs counters of one thread. TID plus start ticks identifies one thread lifetime."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    tid: int = Field(gt=0)
    name: str
    start_ticks: int = Field(ge=0)
    user_ticks: int = Field(ge=0)
    system_ticks: int = Field(ge=0)


class ResourceSample(BaseModel):
    """Raw procfs counters. PID plus start ticks identifies one process lifetime."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    schema_version: Literal[2]
    role: Role
    pid: int = Field(gt=0)
    start_ticks: int = Field(ge=0)
    monotonic_ns: int = Field(ge=0)
    clock_ticks: int = Field(gt=0)
    user_ticks: int = Field(ge=0)
    system_ticks: int = Field(ge=0)
    rss_bytes: int = Field(ge=0)
    # Threads alive at the sample; exited threads' CPU stays in the process counters.
    threads: tuple[ThreadSample, ...] = Field(min_length=1)


class DeliverySample(BaseModel):
    """Cumulative payload counters from one reference peer connection."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    schema_version: Literal[1]
    monotonic_ns: int = Field(ge=0)
    pid: int = Field(gt=0)
    connection: int = Field(ge=0)
    bytes: int = Field(ge=0)
    objects: int = Field(ge=0)


class ResourceSummary(BaseModel):
    """One process's resources over the window; CPU fields are absent without a complete interval."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    host: str
    role: Role
    pid: int = Field(gt=0)
    samples: int = Field(gt=0)
    mean_rss_bytes: float = Field(ge=0)
    sampled_max_rss_bytes: int = Field(ge=0)
    rss_growth_bytes: int
    covered_seconds: float | None = Field(ge=0)
    user_cpu_seconds: float | None = Field(ge=0)
    system_cpu_seconds: float | None = Field(ge=0)
    mean_cpu_cores: float | None = Field(ge=0)
    max_cpu_cores: float | None = Field(ge=0)
    sampled_threads: int | None = Field(ge=0)
    busiest_thread_mean_cpu_cores: float | None = Field(ge=0)
    busiest_thread_max_cpu_cores: float | None = Field(ge=0)


class ThreadSummary(BaseModel):
    """One thread lifetime's CPU over the complete intervals it was seen for."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    host: str
    role: Role
    pid: int = Field(gt=0)
    tid: int = Field(gt=0)
    start_ticks: int = Field(ge=0)
    name: str
    covered_seconds: float = Field(ge=0)
    user_cpu_seconds: float = Field(ge=0)
    system_cpu_seconds: float = Field(ge=0)
    mean_cpu_cores: float = Field(ge=0)
    max_cpu_cores: float = Field(ge=0)


class DeliverySummary(BaseModel):
    """One subscriber connection's delivered payload over the window."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    host: str
    pid: int = Field(gt=0)
    connection: int = Field(ge=0)
    covered_seconds: float = Field(ge=0)
    bytes: int = Field(ge=0)
    objects: int = Field(ge=0)
    bytes_per_second: float = Field(ge=0)
    objects_per_second: float = Field(ge=0)


class MonitoringSummary(BaseModel):
    """The window's monitoring summaries, kept even for untraced runs."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    schema_version: Literal[2] = 2
    window_seconds: float = Field(gt=0)
    hosts: tuple[MonitoringHost, ...]
    resources: tuple[ResourceSummary, ...]
    threads: tuple[ThreadSummary, ...]
    deliveries: tuple[DeliverySummary, ...]
    # Absent when no delivery counters were collected, rather than zero.
    total_goodput_bytes_per_second: float | None = Field(ge=0)
    total_objects_per_second: float | None = Field(ge=0)


class NetworkManifest(BaseModel):
    """What the runner captured beside a trace, and how to place it on the trace clock.

    Paths are relative to the manifest's directory, so a run directory can move.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    relay_port: int = Field(gt=0, le=65_535)
    # CLOCK_REALTIME minus CLOCK_MONOTONIC, sampled when the capture started and
    # again when it stopped. Linux slews both clocks together, so the two differ
    # only when the realtime clock was stepped during the run.
    realtime_offset_ns: int
    realtime_offset_end_ns: int
    # The largest error of either offset sample.
    realtime_offset_uncertainty_ns: int = Field(ge=0)
    # Interfaces whose packets a capture on `any` records twice, once leaving and
    # once arriving, so only the arriving copy is kept.
    loopback_ifindexes: tuple[int, ...] = ()
    pcap: str | None = None
    # tcpdump's own report, which counts the datagrams the kernel dropped.
    capture_log: str | None = None
    # The TLS key logs of the peers, which decrypt the capture.
    key_logs: tuple[str, ...] = ()
    qlog_dir: str | None = None


def read_manifest(path: pathlib.Path) -> NetworkManifest:
    """Read and validate a network manifest."""

    try:
        return NetworkManifest.model_validate_json(path.read_text())
    except (OSError, ValueError) as error:
        raise TraceError(f"failed to read network manifest {path}: {error}") from error
