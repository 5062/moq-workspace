"""Standalone procfs collector, also sent as source to SSH hosts (stdlib only)."""

import json
import os
import pathlib
import signal
import sys
import threading
import time


def sample(role: str, pid: int) -> dict:
    # comm may contain spaces and parentheses. The last ')' ends field 2.
    fields = pathlib.Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
    if fields[0] in ("Z", "X"):
        raise RuntimeError(f"{role} process {pid} stopped during resource collection")
    rss = int(pathlib.Path(f"/proc/{pid}/statm").read_text().split()[1])
    return {
        "schema_version": 1,
        "role": role,
        "pid": pid,
        "start_ticks": int(fields[19]),
        "monotonic_ns": time.clock_gettime_ns(time.CLOCK_MONOTONIC),
        "clock_ticks": os.sysconf("SC_CLK_TCK"),
        "user_ticks": int(fields[11]),
        "system_ticks": int(fields[12]),
        "rss_bytes": rss * os.sysconf("SC_PAGE_SIZE"),
    }


def main() -> None:
    roles = json.loads(sys.argv[1])
    interval = float(sys.argv[2])
    stopped = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: stopped.set())
    identities = {}
    deadline = time.monotonic()
    while True:
        for role, pid in roles.items():
            row = sample(role, pid)
            identity = identities.setdefault(role, row["start_ticks"])
            if row["start_ticks"] != identity:
                raise RuntimeError(f"{role} PID {pid} was reused")
            print(json.dumps(row), flush=True)
        if stopped.is_set():
            break
        # Skip missed ticks, without bursting or assuming the interval elapsed.
        deadline += interval
        now = time.monotonic()
        if deadline < now:
            deadline = now + interval
        stopped.wait(max(0, deadline - now))


if __name__ == "__main__":
    main()
