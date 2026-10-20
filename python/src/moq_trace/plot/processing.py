"""How much processing each inbound object costs the relay, stacked by layer and category."""

from __future__ import annotations

import pathlib
from collections.abc import Sequence

from matplotlib import pyplot as plt

from ..analysis import phases
from .common import PlotRun, format_us, save_figure

# Bottom to top: each layer's work phases grouped by category. Waits and
# overlapping phases have no category, so every nanosecond is stacked once.
_SEGMENTS = (
    ("packet", "crypto", "QUIC crypto"),
    ("packet", "framing", "QUIC framing"),
    ("object", "framing", "MoQ framing"),
    ("object", "routing", "MoQ routing & fan-out"),
)
_COLORS = [plt.get_cmap("tab20c")(index) for index in (0, 1, 4, 5)]

# A block shorter than this share of the tallest bar has no room for its value inside.
_LABEL_SHARE = 0.03

_CATEGORY = {(phase.subject, phase.direction, phase.name): phase.category for phase in phases.PHASES}


def _per_object_us(run: PlotRun) -> dict[tuple[str, str], float] | None:
    """Each segment's total work divided by the inbound objects, or None without any.

    A mean rather than a quantile, because only means add up into a stack. The
    copies and packets an object caused are charged to it, so the bar grows with
    fan-out the way the relay's CPU does.
    """

    scope, parameters = ("", []) if run.run_id is None else (" AND run_id = ?", [run.run_id])
    connection = run.connection
    totals = connection.execute(
        f"SELECT subject, direction, phase, sum(total_ns) FROM metrics.phase_totals WHERE true{scope} GROUP BY ALL",
        parameters,
    ).fetchall()
    (objects,) = connection.execute(
        "SELECT count(DISTINCT trace_id) FROM metrics.phase_totals "
        f"WHERE subject = 'object' AND direction = 'rx'{scope}",
        parameters,
    ).fetchone()
    if not objects:
        return None
    sums: dict[tuple[str, str], float] = {}
    for subject, direction, phase, total_ns in totals:
        # A phase the table lacks has no category and is left out, like a wait.
        if category := _CATEGORY.get((subject, direction, phase)):
            key = (subject, category)
            sums[key] = sums.get(key, 0.0) + total_ns / 1000.0 / objects
    return sums


def plot_processing(
    path: pathlib.Path,
    title: str,
    subtitle: str,
    runs: Sequence[PlotRun],
    *,
    axis_title: str | None = None,
    ticks: Sequence[str] | None = None,
) -> bool:
    """Render one stacked bar per run when any run has object phases to normalize by.

    `ticks` label the bars in place of the run labels, such as a sweep's bare values under `axis_title`.
    """

    if not runs:
        raise ValueError("a processing figure requires at least one run")
    bars = [_per_object_us(run) for run in runs]
    if all(bar is None for bar in bars):
        return False
    fig, axis = plt.subplots(figsize=(max(10.0, 1.2 * len(runs) + 3), 6))
    positions = range(len(runs))
    bottoms = [0.0] * len(runs)
    blocks = []
    for (subject, category, label), color in zip(_SEGMENTS, _COLORS, strict=True):
        heights = [0.0 if bar is None else bar.get((subject, category), 0.0) for bar in bars]
        if not any(heights):
            continue
        blocks.append((axis.bar(positions, heights, 0.6, bottom=bottoms, label=label, color=color), heights))
        bottoms = [bottom + height for bottom, height in zip(bottoms, heights, strict=True)]
    tallest = max(bottoms)
    gap = _LABEL_SHARE * tallest
    # The height of each bar's last label beside it, so the next one clears it.
    beside = [-gap] * len(runs)
    for container, heights in blocks:
        for index, (patch, height) in enumerate(zip(container.patches, heights, strict=True)):
            if not height:
                continue
            # A short block's value goes beside the bar rather than over its neighbors.
            inside = height >= gap
            y = patch.get_y() + height / 2
            if not inside:
                y = beside[index] = max(y, beside[index] + gap)
            axis.annotate(
                format_us(height),
                (patch.get_x() + patch.get_width() * (0.5 if inside else 1), y),
                xytext=(0 if inside else 3, 0),
                textcoords="offset points",
                ha="center" if inside else "left",
                va="center",
                fontsize=7,
            )
    for position, total, bar in zip(positions, bottoms, bars, strict=True):
        axis.annotate(
            "no object phases" if bar is None else format_us(total),
            (position, total),
            xytext=(0, 3),
            textcoords="offset points",
            ha="center",
            fontsize=8,
        )
    axis.set_xticks(positions, ticks if ticks is not None else [run.label for run in runs])
    if axis_title is not None:
        axis.set_xlabel(axis_title)
    axis.set_ylabel("Mean processing latency (µs)")
    axis.grid(axis="y", alpha=0.25)
    # Reversed so the legend reads top to bottom like the stack.
    handles, labels = axis.get_legend_handles_labels()
    axis.legend(handles[::-1], labels[::-1], loc="upper left", fontsize=8)
    save_figure(fig, path, f"{title} | {subtitle}")
    return True
