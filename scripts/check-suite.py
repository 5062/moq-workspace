"""Check the workspace's moq-trace.toml against the profiles and peers it names.

Every relay profile must load and build an experiment, and the protocol the
suite pins must be the one the peers pin, or a bench either fails its handshake
or changes the wire under the trace. Runs in the toolkit's development shell.
"""

import pathlib
import re
import sys

from moq_trace.run import bench, suite

root = pathlib.Path(__file__).resolve().parents[1]
loaded = suite.load(root / suite.FILE)
names = bench.profiles(loaded)
if not names:
    sys.exit(f"no relay profiles in {loaded.profiles}")
for name in names:
    bench.experiment_config(bench.load_profile(loaded, name), pathlib.Path("run"), {"protocol": loaded.protocol})

pinned = re.findall(r'VERSION: &str = "([^"]+)"', (root / "moq-bench/src/lib.rs").read_text())
if pinned != [loaded.protocol]:
    sys.exit(f"{suite.FILE} pins protocol {loaded.protocol}, but moq-bench pins {', '.join(pinned) or 'none'}")
print(f"{len(names)} relay profiles load, and the peers pin {loaded.protocol}")
