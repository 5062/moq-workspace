# Workspace experiment runner

`moq-workspace-bench` runs the reference peers against the pinned relays, locally
or across SSH hosts. This package owns workload configuration, relay profiles,
sweeps, host placement, checkout synchronization, builds, and capture collection.
It uses the workspace's `python/` tooling for capture primitives, decoding,
analysis, artifact schemas, and plotting.

From the workspace root:

```sh
nix develop --command just bench --relay moq-dev-moq --subscribers 4
nix develop --command just bench --help
nix develop --command just run experiment.toml --output artifacts/example
nix develop --command just check-runner
```

`just bench` and `just run` build the workspace's native decoder before launching
the runner. Build the relays and reference peers first for local experiments.
Inside the workspace shell, the equivalent commands are
`moq-workspace-bench bench ...` and `moq-workspace-bench run CONFIG ...`.
`moq-trace analyze`, `moq-trace verify`, and `moq-trace plot` use the workspace's
Python analysis tooling. The workspace shell loads both source packages and
provides their dependencies. Tests use stand-in peers and SSH connections; they
need no remote hosts or privileged packet capture.

## Workloads and suites

An experiment names the relay's whole command line in a `relay_args` TOML
array, since the runner knows no relay's arguments, and the MoQ version the peers
offer in `protocol`. The `relay_args` entries can use
`{port}`, `{protocol}`, `{output}`, `{certificate}`, and `{key}`; certificate
placeholders trigger generation of a throwaway TLS pair. Relays
without a readiness log marker can set `relay_ready_log = ""` and use
`relay_startup_seconds`. `moq-workspace-bench run CONFIG --output` overrides the configured run
directory without changing the checked-in experiment profile.

`moq-workspace-bench bench` runs one workload against several relay implementations in
turn. It reads which relays, protocol, and peers to use from a `moq-trace.toml`
suite file, in the working directory unless `--suite` names another. Relative
paths in it resolve against the file's directory:

```toml
profiles = "profiles"                 # one launch profile per relay
protocol = "moq-transport-16"         # the version every implementation negotiates
bench_bin = "moq-bench/target/release/moq-bench"
bench_build = "nix develop --command just build-moq-bench"  # in a remote peer's checkout
```

The workspace's `moq-trace.toml` runs its profiles and peers, so from the
workspace root:

```sh
moq-workspace-bench bench --relay moq-dev-moq,cloudflare-moq-rs,google-quiche \
  --subscribers 4 --object-size 16384 --fps 30 --objects-per-group 30 \
  --duration-seconds 20
```

Every group opens a new stream, so `--objects-per-group` decides how many objects
share one stream's setup. One object per group charges that setup to every
object, which is a stress case; the default of 30 is one second of 30 fps video.
`metrics.position_statistics` reports object metrics separately for the first
object of each group and for the rest, so the setup cost stays visible.

Each relay gets its own subdirectory under `--output`, which defaults to
`artifacts/bench-<UTC time>`. The relays come from the launch profiles in the
suite's `profiles` directory. A profile names the relay's checkout, binary,
arguments, readiness marker, and stop behavior, and may not set any workload key,
so every relay in one invocation runs the same workload. The workload flags
default to the experiment defaults. A relative profile checkout resolves against
the suite's directory. `--checkout RELAY=PATH` points one relay at another
checkout, and an unknown relay or a missing binary fails before any run starts.
A failed run does not stop the relays after it. Build each relay with its tracing
hooks first, linked against the LTTng-UST release of the `lttng` tools that run
the capture.

`--sweep DIMENSION=V1,V2,...` runs every relay at each value of `subscribers`,
`object-size`, or `objects-per-group` instead of once, with every other workload
flag fixed:

```sh
moq-workspace-bench bench --relay moq-dev-moq --sweep subscribers=1,4,16
```

Each run then goes to `<relay>/<dimension>-<value>/` under `--output`, and each
relay's values are compared in that relay's directory instead of the relays being
compared with each other. The swept dimension cannot also be given as its own flag.

## Distributed runs

`--hosts hosts.toml` places the relay, the publisher, and the subscriber on
other hosts, reached over ssh with asyncssh, which reads `~/.ssh/config` (aliases,
`User`, `IdentityFile`, `ProxyJump`) and `~/.ssh/known_hosts`. It never prompts,
so each host needs key authentication, an already accepted host key, and SFTP,
which copies files to and from the relay host. One connection per host serves
every run of an invocation. A role without a table runs on the controller. The same
tables go under `[hosts.relay]` and so on in a `run` configuration.

```toml
[relay]
ssh = "me@relay-host"
address = "10.0.0.1"      # what the peers dial; defaults to the ssh host name
lttng = "nix --extra-experimental-features 'nix-command flakes' develop ~/moq-workspace/moq-trace --command lttng"   # when lttng is not on the ssh PATH
checkout = "~/moq-workspace"  # the copy of the suite the relays' checkouts are in

[publisher]
ssh = "me@pub-host"
checkout = "~/moq-workspace"  # a checkout that builds moq-bench

[subscriber]
ssh = "me@sub-host"
checkout = "~/moq-workspace"
```

Every path is a path on that host, and `~/` or a relative path resolves against
the remote home. Before each run, every remote host with a `checkout` builds
its role there, once per host and checkout: the relay with its profile's
`relay_build`, and the peers with the suite's `bench_build`. The workspace's
commands enable flakes themselves, so a host whose Nix leaves them off still
builds.
`build` overrides the command, and an empty `build` uses the existing binary.
A remote relay builds from `--checkout` for that relay when given, and otherwise
from the profile's checkout. A relay host's `checkout` names its copy of the
suite: a relative profile checkout resolves against it, so every relay of one
bench finds its own checkout there, and against the remote home without one. The
profile's relative `relay_bin` resolves against the relay's checkout. A peer's
binary is `moq-bench/target/release/moq-bench` in its host's `checkout`, and
`binary` overrides it; the relay's is always `relay_bin`.

When the suite names its git `repository`, a bench brings every remote host's
`checkout` to the controller's commit before it builds, so the hosts need no
setup beyond ssh, git, and Nix. A missing checkout is cloned, an existing one
fetches and checks out the commit, and only the suite's `submodules` are
initialized, plus a relay's own `submodules` on the relay's host:

```toml
repository = "https://github.com/5062/moq-workspace.git"
submodules = ["moq-trace", "deps/quinn"]   # every host
```

The hosts fetch from the remotes, so commit and push the checkout and its
submodules first; a bench refuses to start while tracked files have uncommitted
changes, and a host's checkout fails rather than overwrite changes made on the
host. A `run` configuration syncs the same way through a `[sync]` table with
`repository`, `revision`, `submodules`, and, for the relay's host,
`relay_root` and `relay_submodules`.

Only the relay's host is traced. The LTTng session, the packet capture, qlog,
and the clock offset the network figures need are all taken there and copied
back, so the analysis and figures are the same as for a local run, and no
clocks need synchronizing. A peer that shares the relay's host is traced too; a
peer on another host is not. A peer on the relay's host dials
`relay_local_host`, and a peer elsewhere dials the relay host's `address`, or
`relay_url` when the relay runs on the controller. Each host keeps its run
directory under `workdir` (`~/moq-trace-runs` by default) for inspection.

`--no-trace` runs the same workload without an LTTng session. Its runs keep only
the peers' logs, which report throughput, frame rates, and mismatches, and
produce no analysis artifact, so they are left out of any comparison.

Every run also measures the network the relay saw, and renders it as
`network.png` when anything was captured. `--capture-packets` (or
`capture_packets = true`) records a full-payload `tcpdump` of the relay's UDP port
on the relay host, which gives throughput per direction. It runs tcpdump through
sudo. Where sudo asks for a password, export `MOQ_TRACE_SUDO_PASSWORD`: the
runner removes it from its environment at startup, so no child process inherits
it, and sends it only to sudo's standard input, over ssh for a remote relay.
Without it, sudo must not need a password. RTT, lost packets, and
congestion window come from the relay's own qlog: `--qlog` (or `qlog = true`)
sets `QLOGDIR` to the run's `qlog/` directory, and a relay that honors it writes
one JSON-SEQ qlog there. qlog serializes an event per packet on the relay, so
measure latency without it and turn it on for network runs. qlog times are relative to a start instant the relay picks, so the relay
must record that instant's `CLOCK_MONOTONIC` nanoseconds as
`monotonic_start_ns=<n>` in the qlog title; the analysis rejects a qlog without
it. RTT is read in milliseconds, as the qlog specification defines it, unless the
title also says `rtt_unit=s`; Quinn 0.11 writes seconds. `network.json` in the run directory records what was captured and the
offset between the capture's wall clock and the trace clock, and `analyze
--network` reads it for a trace analyzed by hand. A capture sees UDP sockets, not
connections, so it splits traffic by direction; the qlog panels show the
publisher and up to two subscriber connections.

## Figures

Traced experiments render figures unless `render = false` or `--no-render` is
set. Re-render a run, bench, or sweep with `moq-trace plot PATH`; see the
[analysis figures documentation](../python/README.md#figures).
