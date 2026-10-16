"""Resource use and actual endpoint goodput alongside relay latency."""

from __future__ import annotations

import pathlib

import duckdb
from matplotlib import pyplot as plt
from matplotlib.axes import Axes

from .common import PlotRun, _save


def _intervals(axis: Axes, rows: list[tuple], column: int, label: str) -> None:
    if rows:
        axis.stairs([row[column] for row in rows], [rows[0][0], *(row[1] for row in rows)], baseline=None, label=label)


def plot_monitoring(path: pathlib.Path, subtitle: str, connection: duckdb.DuckDBPyConnection) -> None:
    fig, axes = plt.subplots(4, 1, figsize=(13, 12), sharex=True)
    cpu, memory, goodput, objects = axes
    start, end = connection.execute("SELECT start_ns / 1e9, end_ns / 1e9 FROM monitoring.window").fetchone()
    for host, role, pid, error in connection.execute(
        "SELECT host, role, pid, uncertainty_ns FROM monitoring.hosts ORDER BY role, host"
    ).fetchall():
        label = f"{role} ({host}, PID {pid})"
        if error:
            label += f" ±{error / 1e6:.1f} ms"
        rows = connection.execute(
            "SELECT start_ns / 1e9, end_ns / 1e9, cpu_cores FROM monitoring.resource_intervals "
            "WHERE host = ? AND role = ? AND start_ns >= ? AND end_ns <= ? ORDER BY end_ns",
            [host, role, round(start * 1e9), round(end * 1e9)],
        ).fetchall()
        _intervals(cpu, rows, 2, label)
        rows = connection.execute(
            "SELECT elapsed_ns / 1e9, rss_bytes / 1048576.0 FROM monitoring.resources "
            "WHERE host = ? AND role = ? AND elapsed_ns BETWEEN ? AND ? ORDER BY elapsed_ns",
            [host, role, round(start * 1e9), round(end * 1e9)],
        ).fetchall()
        if rows:
            memory.plot(*zip(*rows, strict=True), label=label)
    # Total is always drawn. At large fan-out draw the median and worst by
    # delivered rate as representatives; every connection remains in the DB.
    rates = connection.execute(
        "SELECT connection, bytes_per_second FROM monitoring.delivery_summary ORDER BY bytes_per_second, connection"
    ).fetchall()
    shown = {key for key, _ in rates} if len(rates) <= 2 else {rates[0][0], rates[len(rates) // 2][0]}
    rows = connection.execute(
        "SELECT start_ns / 1e9, end_ns / 1e9, sum(bytes_per_second) * 8 / 1e6, sum(objects_per_second) "
        "FROM monitoring.delivery_intervals WHERE start_ns >= ? AND end_ns <= ? "
        "GROUP BY start_ns, end_ns ORDER BY end_ns",
        [round(start * 1e9), round(end * 1e9)],
    ).fetchall()
    _intervals(goodput, rows, 2, "all subscribers")
    _intervals(objects, rows, 3, "all subscribers")
    for key in sorted(shown):
        rows = connection.execute(
            "SELECT start_ns / 1e9, end_ns / 1e9, bytes_per_second * 8 / 1e6, objects_per_second "
            "FROM monitoring.delivery_intervals WHERE connection = ? AND start_ns >= ? AND end_ns <= ? ORDER BY end_ns",
            [key, round(start * 1e9), round(end * 1e9)],
        ).fetchall()
        _intervals(goodput, rows, 2, f"subscriber {key}")
        _intervals(objects, rows, 3, f"subscriber {key}")
    error = connection.execute("SELECT max(uncertainty_ns) FROM monitoring.hosts WHERE role = 'subscriber'").fetchone()[
        0
    ]
    if error:
        subtitle += f" | delivery clock alignment ±{error / 1e6:.1f} ms"
    for axis, title, unit in zip(
        axes,
        (
            "Process CPU (1 core = 100%)",
            "Resident memory (approximate, sampled)",
            "Delivered application payload goodput",
            "Completed objects at subscribers",
        ),
        ("CPU cores", "MiB", "Mbit/s", "Objects/s"),
        strict=True,
    ):
        axis.set_title(title)
        axis.set_ylabel(unit)
        axis.set_xlim(start, end)
        axis.set_ylim(bottom=0)
        axis.grid(alpha=0.25)
        if axis.get_legend_handles_labels()[0]:
            axis.legend(fontsize=8, loc="upper right")
        else:
            axis.text(0.5, 0.5, "No samples covering this window", transform=axis.transAxes, ha="center", color="gray")
    objects.set_xlabel("Elapsed time on relay clock (s); remote alignment uncertainty in legends")
    _save(fig, path, f"Resources and delivery | {subtitle}")


def plot_monitoring_comparison(path: pathlib.Path, subtitle: str, runs: list[PlotRun]) -> None:
    fig, axes = plt.subplots(1, 3, figsize=(13, 4))
    for index, run in enumerate(runs):
        row = run.connection.execute(
            "SELECT mean_cpu_cores, sampled_max_rss_bytes / 1048576.0 FROM monitoring.resource_summary "
            "WHERE run_id = ? AND role = 'relay'",
            [run.run_id],
        ).fetchone()
        rate = run.connection.execute(
            "SELECT sum(bytes_per_second) * 8 / 1e6 FROM monitoring.delivery_summary WHERE run_id = ?",
            [run.run_id],
        ).fetchone()[0]
        for axis, value in zip(axes, (*row, rate) if row is not None else (None, None, rate), strict=True):
            if value is not None:
                axis.bar(index, value, color=f"C{index % 10}")
            else:
                axis.text(index, 0, "unavailable", rotation=90, ha="center", va="bottom", color="gray", fontsize=8)
    for axis, title in zip(
        axes,
        ("Mean relay CPU (cores)", "Sampled maximum relay RSS (MiB)", "Total delivered payload (Mbit/s)"),
        strict=True,
    ):
        axis.set_title(title)
        axis.set_xticks(range(len(runs)), [run.label for run in runs], rotation=20, ha="right")
        axis.grid(axis="y", alpha=0.25)
    _save(fig, path, f"Resources and delivery comparison | {subtitle}")
