"""Check the workspace's moq-trace.toml against the profiles and peers it names.

Every relay profile must load and build an experiment, every submodule the
suite and the profiles list must be one, and the protocol the suite pins must
be the one the peers pin, or a bench either fails its handshake or changes the
wire under the trace. Runs in the workspace's development shell.
"""

import pathlib
import re
import subprocess
import sys

from moq_trace.run import bench, suite

root = pathlib.Path(__file__).resolve().parents[1]
loaded = suite.load(root / suite.FILE)
names = bench.profiles(loaded)
if not names:
    sys.exit(f"no relay profiles in {loaded.profiles}")
# Remote hosts initialize the listed submodules, so a typo would fail only there.
listed = subprocess.run(
    [
        "git",
        "-C",
        str(root),
        "config",
        "--file",
        ".gitmodules",
        "--get-regexp",
        r"\.path$",
    ],
    check=True,
    capture_output=True,
    text=True,
).stdout
known = {line.split()[1] for line in listed.splitlines()}
unknown = sorted(set(loaded.submodules) - known)
for name in names:
    profile = bench.load_profile(loaded, name)
    bench.experiment_config(profile, pathlib.Path("run"), {"protocol": loaded.protocol})
    unknown += [f"{path} ({name})" for path in profile.submodules if path not in known]
if unknown:
    sys.exit(f"not submodules of the workspace: {', '.join(unknown)}")

pinned = re.findall(r'VERSION: &str = "([^"]+)"', (root / "moq-bench/src/lib.rs").read_text())
if pinned != [loaded.protocol]:
    sys.exit(f"{suite.FILE} pins protocol {loaded.protocol}, but moq-bench pins {', '.join(pinned) or 'none'}")
print(f"{len(names)} relay profiles load, and the peers pin {loaded.protocol}")
