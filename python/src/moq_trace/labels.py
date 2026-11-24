"""Human-readable labels for workload quantities, shared by the runner and the figures."""

from __future__ import annotations

from collections.abc import Sequence


def cpus(values: Sequence[int]) -> str:
    """Write sorted CPUs as a `taskset -c` list, folding consecutive runs into ranges."""

    runs: list[list[int]] = []
    for cpu in values:
        if runs and cpu == runs[-1][-1] + 1:
            runs[-1].append(cpu)
        else:
            runs.append([cpu])
    return ",".join(str(run[0]) if len(run) == 1 else f"{run[0]}-{run[-1]}" for run in runs)


def parse_cpus(text: str) -> tuple[int, ...]:
    """Read a `taskset -c` style list such as `3-10` or `2,4-6` as sorted, distinct CPUs."""

    values: set[int] = set()
    for part in text.split(","):
        first, _, last = part.strip().partition("-")
        start, end = int(first), int(last or first)
        if start < 0 or end < start:
            raise ValueError(f"invalid CPU range {part.strip()!r}")
        values.update(range(start, end + 1))
    return tuple(sorted(values))


def byte_size(value: int) -> str:
    """Label a byte count using an exact binary unit when possible."""

    for divisor, suffix in ((1024 * 1024, "MiB"), (1024, "KiB")):
        if value % divisor == 0:
            return f"{value // divisor} {suffix}"
    return f"{value} bytes"


def subscribers(count: int) -> str:
    """Label a subscriber count."""

    return f"{count} {'subscriber' if count == 1 else 'subscribers'}"


def objects_per_group(count: int) -> str:
    """Label a group length in objects."""

    return f"{count} {'object' if count == 1 else 'objects'}/group"


def dimension(name: str, value: int) -> str:
    """Label one value of a comparison dimension."""

    if name == "subscribers":
        return subscribers(value)
    if name == "object_size":
        return byte_size(value)
    if name == "objects_per_group":
        return objects_per_group(value)
    raise ValueError(f"unknown comparison dimension: {name}")
