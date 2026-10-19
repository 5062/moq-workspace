"""Ingest saved host counters and delivered payload counters, without live hosts."""

from __future__ import annotations

import json
import pathlib

import duckdb

from ..errors import TraceError
from ..manifest import DeliverySample, MonitoringManifest, ResourceSample
from . import sql
from .artifact import open_artifact


def ingest(
    connection: duckdb.DuckDBPyConnection,
    path: pathlib.Path,
    *,
    origin_ns: int,
    start_ns: int,
    end_ns: int,
    subscribers: int | None = None,
    relay_pid: int | None = None,
) -> None:
    """Validate identities and counters before deriving actual-interval rates."""

    try:
        manifest = MonitoringManifest.model_validate_json(path.read_text())
        if end_ns <= start_ns:
            raise ValueError("monitoring window must have positive duration")
        if subscribers is not None and manifest.subscribers != subscribers:
            raise ValueError("monitoring subscriber count differs from the analyzed workload")
        subscribers = manifest.subscribers
        connection.execute(
            "INSERT INTO monitoring.window VALUES (?, ?, ?)", [origin_ns, start_ns - origin_ns, end_ns - origin_ns]
        )
        roles_seen = set()
        for host in manifest.hosts:
            if not set(host.resource_roles) <= set(host.roles) or (host.resources is None and host.resource_roles):
                raise ValueError("resource file does not match its declared roles")
            if relay_pid is not None and "relay" in host.roles and host.roles["relay"] != relay_pid:
                raise ValueError("monitoring relay PID differs from the analyzed process")
            if roles_seen.intersection(host.roles):
                raise ValueError("a monitored role appears on multiple hosts")
            roles_seen.update(host.roles)
            for role, pid in host.roles.items():
                connection.execute(
                    "INSERT INTO monitoring.hosts VALUES (?, ?, ?, ?, ?, ?)",
                    [host.host, role, pid, host.offset_ns, host.uncertainty_ns, role in host.resource_roles],
                )
            if host.resources is not None:
                previous = {}
                previous_threads = {}
                with (path.parent / host.resources).open() as stream:
                    for line in stream:
                        row = ResourceSample.model_validate_json(line)
                        if row.role not in host.resource_roles or host.roles.get(row.role) != row.pid:
                            raise ValueError("resource sample has an unexpected role or PID")
                        before = previous.get(row.role)
                        if before is not None:
                            if row.start_ticks != before.start_ticks or row.clock_ticks != before.clock_ticks:
                                raise ValueError("resource process identity or clock tick rate changed")
                            if (
                                row.monotonic_ns <= before.monotonic_ns
                                or row.user_ticks < before.user_ticks
                                or row.system_ticks < before.system_ticks
                            ):
                                raise ValueError("resource time/counters are not monotonic")
                        previous[row.role] = row
                        tids = [thread.tid for thread in row.threads]
                        if len(set(tids)) != len(tids) or row.pid not in tids:
                            raise ValueError("resource sample threads are duplicated or omit the main thread")
                        for thread in row.threads:
                            key = (row.role, thread.tid, thread.start_ticks)
                            before = previous_threads.get(key)
                            if before is not None and (
                                thread.user_ticks < before.user_ticks or thread.system_ticks < before.system_ticks
                            ):
                                raise ValueError("thread counters are not monotonic")
                            previous_threads[key] = thread
                            connection.execute(
                                "INSERT INTO monitoring.threads VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                                [
                                    host.host,
                                    row.role,
                                    row.pid,
                                    thread.tid,
                                    thread.start_ticks,
                                    thread.name,
                                    row.monotonic_ns - host.offset_ns - origin_ns,
                                    row.clock_ticks,
                                    thread.user_ticks,
                                    thread.system_ticks,
                                ],
                            )
                        connection.execute(
                            "INSERT INTO monitoring.resources VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                            [
                                host.host,
                                row.role,
                                row.pid,
                                row.start_ticks,
                                row.monotonic_ns,
                                row.monotonic_ns - host.offset_ns - origin_ns,
                                row.clock_ticks,
                                row.user_ticks,
                                row.system_ticks,
                                row.rss_bytes,
                            ],
                        )
                if set(previous) != set(host.resource_roles):
                    raise ValueError("resource collector is missing expected processes")
            if manifest.goodput and "subscriber" in host.roles:
                previous = {}
                with (path.parent / "subscriber.log").open() as stream:
                    for line in stream:
                        if not line.startswith("MOQ_BENCH_DELIVERY "):
                            continue
                        row = DeliverySample.model_validate_json(line.removeprefix("MOQ_BENCH_DELIVERY "))
                        if row.pid != host.roles["subscriber"]:
                            raise ValueError("delivery sample has an unexpected PID")
                        before = previous.get(row.connection)
                        if before is not None and (
                            row.monotonic_ns <= before.monotonic_ns
                            or row.bytes < before.bytes
                            or row.objects < before.objects
                        ):
                            raise ValueError("delivery time/counters are not monotonic")
                        previous[row.connection] = row
                        connection.execute(
                            "INSERT INTO monitoring.deliveries VALUES (?, ?, ?, ?, ?, ?, ?)",
                            [
                                host.host,
                                row.pid,
                                row.connection,
                                row.monotonic_ns,
                                row.monotonic_ns - host.offset_ns - origin_ns,
                                row.bytes,
                                row.objects,
                            ],
                        )
                if not previous or (subscribers is not None and set(previous) != set(range(subscribers))):
                    raise ValueError(
                        "missing delivered payload counters; rebuild reference peers or disable monitor_goodput"
                    )
        if "relay" not in roles_seen or (manifest.goodput and "subscriber" not in roles_seen):
            raise ValueError("monitoring manifest is missing a required role")
    except (OSError, ValueError, duckdb.Error) as error:
        raise TraceError(f"failed to analyze monitoring {path}: {error}") from error


def _records(connection: duckdb.DuckDBPyConnection, table: str) -> list[dict]:
    result = connection.execute(f"SELECT * FROM {table} ORDER BY ALL")
    names = [entry[0] for entry in result.description]
    return [dict(zip(names, row, strict=True)) for row in result.fetchall()]


def materialize(connection: duckdb.DuckDBPyConnection) -> None:
    """Publish concrete tables, keeping artifacts independent of query definitions."""

    for name in (
        "resource_summary",
        "thread_summary",
        "delivery_summary",
        "resource_intervals",
        "thread_intervals",
        "delivery_intervals",
    ):
        connection.execute(f"CREATE TABLE monitoring.{name}_materialized AS SELECT * FROM monitoring.{name}")
        connection.execute(f"DROP VIEW monitoring.{name}")
        connection.execute(f"ALTER TABLE monitoring.{name}_materialized RENAME TO {name}")


def write_summary(path: pathlib.Path, database: pathlib.Path | None = None) -> None:
    """Keep a human-readable summary even for untraced runs."""

    try:
        manifest = MonitoringManifest.model_validate_json(path.read_text())
    except (OSError, ValueError) as error:
        raise TraceError(f"failed to read monitoring {path}: {error}") from error
    if database is not None:
        with open_artifact(database, "run") as artifact:
            _write_summary(artifact.connection, path, manifest)
    else:
        with duckdb.connect(":memory:") as connection:
            connection.execute(sql.read("monitoring/schema"))
            ingest(connection, path, origin_ns=manifest.start_ns, start_ns=manifest.start_ns, end_ns=manifest.end_ns)
            _write_summary(connection, path, manifest)


def _write_summary(connection: duckdb.DuckDBPyConnection, path: pathlib.Path, manifest: MonitoringManifest) -> None:
    deliveries = _records(connection, "monitoring.delivery_summary")
    start_ns, end_ns = connection.execute("SELECT start_ns, end_ns FROM monitoring.window").fetchone()
    summary = {
        "schema_version": 2,
        "window_seconds": (end_ns - start_ns) / 1e9,
        "hosts": [host.model_dump() for host in manifest.hosts],
        "resources": _records(connection, "monitoring.resource_summary"),
        "threads": _records(connection, "monitoring.thread_summary"),
        "deliveries": deliveries,
        "total_goodput_bytes_per_second": (sum(row["bytes_per_second"] for row in deliveries) if deliveries else None),
        "total_objects_per_second": sum(row["objects_per_second"] for row in deliveries) if deliveries else None,
    }
    path.with_name("monitoring-summary.json").write_text(json.dumps(summary, indent=2) + "\n")
