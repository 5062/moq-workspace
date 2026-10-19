"""Capture equivalent Rust and C++ lifecycles and compare decoded payloads."""

from __future__ import annotations

import asyncio
import collections
import pathlib
import tempfile

from moq_trace.decode import ctf
from moq_trace.run.verify import record

ROOT = pathlib.Path(__file__).resolve().parents[2] / "moq-trace"


def capture(binary: pathlib.Path, output: pathlib.Path) -> dict:
    """Record a fresh process, retaining every contract field except clocks and contexts."""

    # shortcut: a timed-out fixture process is left running; kill it in record() if hangs recur.
    trace = asyncio.run(asyncio.wait_for(record(output, [[str(binary)]]), 30))
    rows = collections.defaultdict(list)
    for name, batch in ctf.batches(trace):
        for row in batch.to_pylist():
            if row["timestamp_ns"] <= 0:
                raise AssertionError(f"{name} has no monotonic timestamp")
            # Synthetic boundary instants in the fixture are retained verbatim.
            # Automatic timestamps vary between these independent processes.
            if row["timestamp_ns"] > 1000:
                row["timestamp_ns"] = None
            for field in ctf.CONTEXT_FIELDS:
                del row[field.name]
            rows[name].append(row)
    return dict(rows)


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="moq-trace-contract-") as directory:
        root = pathlib.Path(directory)
        rust = capture(ROOT / "target/debug/examples/facade_contract", root / "rust")
        cpp = capture(ROOT / "target/cmake/facade_contract", root / "cpp")
        if set(rust) != set(ctf.SCHEMAS):
            raise AssertionError(f"Rust fixture omitted events: {set(ctf.SCHEMAS) - set(rust)}")
        if set(cpp) != set(ctf.SCHEMAS):
            raise AssertionError(f"C++ fixture omitted events: {set(ctf.SCHEMAS) - set(cpp)}")
        for name in ctf.SCHEMAS:
            if rust[name] != cpp[name]:
                raise AssertionError(f"facade payloads differ for {name}:\nRust: {rust[name]}\nC++: {cpp[name]}")
    print("Rust and C++ native lifecycle payloads match for every event")


if __name__ == "__main__":
    main()
