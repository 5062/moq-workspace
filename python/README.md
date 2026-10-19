# Workspace experiment and trace analysis tools

This directory owns experiment orchestration, decoding, analysis, artifact
schemas, plots, capture helpers, verification, and the Rust CTF decoder for local
MoQ development and testing. Providers and Rust/C++ instrumentation remain in
`moq-trace/`.

## Checking a transport stack

`moq-trace verify` checks the `quic_trace:*` events of a stack's own fixtures,
without a relay or a MoQ workload. It records the commands it is given under
LTTng, or reads an existing CTF trace, and fails unless every packet and socket
lifecycle and every phase is balanced, no packet ends abandoned, each successful
packet's `read_queue` or `send_queue` sits on its socket boundary, every STREAM
range is non-empty, every send-blocked interval is balanced, and the capture
holds a connection path and a `frame_process` phase. A fixture adds what its
scenario must produce:

```sh
moq-trace verify artifacts/check \
  --run "target/debug/deps/quic_trace-0123" \
  --rx-outcome dropped \
  --send-blocked stream_flow_control/stream \
  --socket success,1,2,1100=1
```

`--run` is repeatable and runs its commands in turn, `--send-blocked` takes a
reason, with `/stream` when the interval must name a stream, and `--socket`
requires exact (outcome, buffers, datagrams, bytes) counts once given. The Quinn
and Google QUICHE forks, and the moxygen fork for mvfst, each run their fixtures
this way from `moq_trace/test_trace.sh`.

## Running experiments

`moq-trace run` and `moq-trace bench` run the reference peers against the pinned
relays, locally or across SSH hosts. The runner in `moq_trace.run` owns workload
configuration, relay profiles, sweeps, host placement, checkout synchronization,
builds, and capture collection, and drives the capture primitives, analysis, and
plots below.

From the workspace root:

```sh
nix develop --command just bench --relay moq-dev-moq --subscribers 4
nix develop --command just bench --help
nix develop --command just run experiment.toml --output artifacts/example
```

`just bench` and `just run` build the native decoder before launching the
runner. Build the relays and reference peers first for local experiments.
Inside the workspace shell, the equivalent commands are `moq-trace bench ...`
and `moq-trace run CONFIG ...`. Tests use stand-in peers and SSH connections;
they need no remote hosts or privileged packet capture. Traced experiments
render figures unless `render = false` or `--no-render` is set; see
[Figures](#figures).

### Workloads and suites

An experiment names the relay's whole command line in a `relay_args` TOML
array, since the runner knows no relay's arguments, and the MoQ version the peers
offer in `protocol`. The `relay_args` entries can use
`{port}`, `{protocol}`, `{output}`, `{certificate}`, and `{key}`; certificate
placeholders trigger generation of a throwaway TLS pair. Relays
without a readiness log marker can set `relay_ready_log = ""` and use
`relay_startup_seconds`. `moq-trace run CONFIG --output` overrides the configured run
directory without changing the checked-in experiment profile.

`moq-trace bench` runs one workload against several relay implementations in
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
moq-trace bench --relay moq-dev-moq,cloudflare-moq-rs,google-quiche \
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
moq-trace bench --relay moq-dev-moq --sweep subscribers=1,4,16
```

Each run then goes to `<relay>/<dimension>-<value>/` under `--output`, and each
relay's values are compared in that relay's directory instead of the relays being
compared with each other. The swept dimension cannot also be given as its own flag.

### Distributed runs

`--hosts hosts.toml` places the relay, the publisher, and the subscriber on
other hosts, reached over ssh with asyncssh, which reads `~/.ssh/config` (aliases,
`User`, `IdentityFile`, `ProxyJump`) and `~/.ssh/known_hosts`. It never prompts,
so each host needs key authentication, an already accepted host key, and SFTP,
which copies files to and from the relay host. One connection per host serves
every run of an invocation. A role without a table runs on the controller. The same
tables go under `[hosts.relay]` and so on in a `run` configuration.

Subscriber sessions start over a two-second ramp to avoid a burst of simultaneous
handshakes. A single session starts immediately. The publisher starts only once
all subscribers are connected, so the ramp precedes the measured workload.

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

`--no-trace` runs the same workload without an LTTng session. Its runs keep
the peers' logs and monitoring sidecars and summary, but produce no latency
analysis artifact, so they are left out of any comparison.

Every run samples the relay process's user/system CPU counters and approximate
resident memory once per second. `--monitor-peers` also samples the publisher and
subscriber processes, including on SSH hosts. One persistent stdlib Python
collector runs on each measured host; sampling requires no sudo. Set
`--monitor-interval-seconds 0.5` to change the cadence. CPU is reported in cores
(1 core = 100%; a multithreaded relay may exceed it), with user/system CPU seconds,
mean and sampled maximum RSS, and RSS growth. Each sample also records every live
thread's CPU counters from `/proc/<pid>/task`, so the summary names the busiest
thread and shows whether one thread is saturated while the process total looks
low. Apart from that per-thread split, these are whole-process measurements;
the trace's elapsed phases are not CPU time or per-object CPU attribution.

The reference peers also record cumulative received payload bytes and completed
objects per subscriber connection, once per second, in `subscriber.log` as
`MOQ_BENCH_DELIVERY` JSON records. Goodput counts payload actually received at the
subscriber, including each delivered copy in fan-out; it excludes transport
overhead and retransmission traffic. Rebuild the reference peers after updating
the runner. Alternate peer binaries must emit this schema or run with
`--no-monitor-goodput`.

`monitoring.json` names the raw `resources-*.jsonl` files and the hosts and PIDs of
all roles. Host-local monotonic timestamps and process start ticks retain process
identity. Remote clocks are mapped to the relay's monotonic axis with a bracketed
clock probe; the manifest and legends retain the alignment uncertainty. This is
approximate alignment, not synchronized clocks or a source of one-way latency.
All collection stays local to its host and the files are retained on the controller,
so later analysis needs no SSH connection.

`monitoring-summary.json` reports resource usage, delivery rates per subscriber,
and total delivery rates. Traced runs use the analysis's selected steady-state
window; untraced runs use the runner's workload window after trimming warmup and
cooldown. Rates use actual elapsed time and only complete observed intervals
inside that window. Each summary reports `covered_seconds`; boundary intervals
are omitted rather than interpolating completed objects. Very short runs may
have memory samples but no CPU or delivery interval. RSS is approximate, and its
sampled maximum can miss brief peaks. Traced runs also render `monitoring.png` and
resource/goodput comparisons. Every subscriber remains queryable even when a plot
shows only representative connections.

For matched monitoring-overhead controls, use
`--no-monitor-resources --no-monitor-goodput` with the same workload. Monitoring
defaults apply independently of `--trace`; packet capture and qlog remain opt-in.
Measure perturbation at the workload and fan-out being compared before treating
monitored results as equivalent to the control.

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

`capture-statistics.json` and the artifact's `network.capture_statistics` retain
tcpdump's captured, filter-received, kernel-drop, and interface-drop counters.
An unavailable interface counter is unknown, never zero. These are capture
statistics, separate from the QUIC-declared losses in qlog. Wire validation still
rejects captures with reported drops. QUIC-declared loss is the sender's recovery
decision and can be spurious; it is not a direct measurement of path loss.

## Capture and analysis

From the workspace root, build the native CTF reader before analyzing captures:

```sh
nix develop --command just decoder
nix develop --command moq-trace --help
```

The workspace shell loads `python/src` directly. It provides Rust,
libclang, pkg-config, Babeltrace 2.1, and the Python dependencies. The native
extension reads CTF into Arrow batches; DuckDB performs correlation and analysis.
Babeltrace and its standard plugins are required at runtime. The Python `bt2`
bindings are used only by the synthetic trace-IR tests.

Use `just bench` or `just run CONFIG` for workload capture. `moq-trace analyze`,
`moq-trace verify`, and `moq-trace plot` are workspace commands for inspecting
local development and testing artifacts.

After the steady-state window opens, incomplete fanout produces a warning rather
than failing analysis. The artifact keeps every selected inbound object and the
outcomes of its recorded copies; an absent copy is not labeled as dropped.
Latency statistics use successful copies only. Run and comparison figures show
the successful copy count against the expected fanout when it is incomplete.
Excess fanout and invalid trace boundaries still fail validation.

### Figures

`moq-trace plot` renders figures from an existing artifact, for example after changing the plotting code:

```sh
moq-trace plot artifacts/bench-<UTC time>/moq-dev-moq/analysis.duckdb
moq-trace plot artifacts/bench-<UTC time>
moq-trace plot artifacts/bench-<UTC time>/comparison.duckdb
```

A run's figures go to `plots/` beside its `analysis.duckdb`. Given a bench
output directory, or one relay's directory of a sweep, `plot` compares the runs
it holds and writes to that directory's `plots/`. The runs' recorded workloads
decide what is compared: relays when every workload is the same, or else the one
swept dimension, which must be the only difference. A comparison
artifact renders the runs it indexes. Rendering overwrites figures of the same
name and leaves any others in place.

| Figure | Shows |
|---|---|
| `latency_cdf.png` | Object latency per copy for the wire span (with a packet capture), the QUIC+MoQ span, and the MoQ span, beside one CDF per processing phase |
| `segments.png` | The widest span split into the segments between each layer's hand-off, as stacked means beside each segment's distribution |
| `breakdown.png` | Each processing phase as a box of p25 to p75 with p1 to p99 whiskers, on a log time axis |
| `moq_work.png` | MoQ RX and TX work per copy and the first write start after receipt; only when the provider emits object phases |
| `stability.png` | Per-second p50 and p99 of object copies and of QUIC packets, on one time axis |
| `object_timeline.png` | QUIC and MoQ phases of objects nearest the mean, median, and p99 MoQ latency, with actual latency and active time, from each object's first RX packet |
| `network.png` | Throughput, RTT, lost packets, and congestion window; only when a capture or qlog was taken |
| `monitoring.png` | Process CPU cores, approximate RSS, delivered payload goodput and completed objects; when monitoring sidecars were supplied |
| `relays_monitoring.png`, `comparison_monitoring.png` | Mean relay CPU, sampled maximum RSS and total subscriber goodput across runs |
| `relays_cdf.png`, `relays_segments.png`, `relays_breakdown.png`, `relays_moq_work.png` | The relays of one bench, overlaid |
| `comparison_cdf.png`, `comparison_segments.png`, `comparison_breakdown.png`, `comparison_moq_work.png` | One relay's runs of a `--sweep`, overlaid |

Each object timeline heading names the selection statistic (mean, median, or
p99) and shows that object's actual MoQ latency, measured from RX start to its
latest successful TX completion. Active time is the union of successful non-wait
MoQ phase intervals across RX and all successful TX copies, including phases
hidden from the drawing. Overlapping and nested intervals count once. These are
traced wall-clock durations, including any transport calls inside MoQ phases,
rather than CPU time. Active time is unavailable when no work phases were
recorded. The drawing shows the first and last copies; its bar labels sum each
phase's intervals separately, so overlapping intervals count separately there.

One recording can hold the relay and the peers it serves. Every row keeps the
`vpid` it came from, and the analysis tables are one process's slice of that
recording, so process-local trace and span IDs from different processes never
meet. `analyze` picks the only process by default and needs `--pid` when the
recording holds more than one. Every row also keeps the `vtid` of the thread
that emitted it, which tells work one call ran inside another from the same work
run in parallel on another thread. A recording without either context is
rejected.

Pass `--monitoring RUN/monitoring.json` to `moq-trace analyze` to ingest saved
resource counters and endpoint delivery counters. `monitoring.hosts` retains
host/role/PID identity and remote clock alignment uncertainty;
`monitoring.resources`, `monitoring.threads`, and `monitoring.deliveries` retain
raw samples; `monitoring.resource_intervals`, `monitoring.thread_intervals`, and
`monitoring.delivery_intervals` store rates from actual counter deltas and
elapsed time. Their `*_summary` tables use only complete intervals within
`model.window`, with explicit `covered_seconds`. CPU cores are total process CPU
seconds divided by wall seconds. `monitoring.thread_summary` keeps each thread
lifetime (TID plus start ticks) separately, and `resource_summary` reports the
busiest thread's mean and interval maximum, which expose a single-thread ceiling
that the process total hides. Threads that start or exit between samples count
only over the intervals they were seen for, so thread totals need not add up to
the process total. RSS is sampled
resident memory, not heap size. Delivered bytes exclude QUIC overhead and count
each subscriber's received copy; UDP throughput includes transport traffic.
The comparison snapshot copies the monitoring summaries so comparison figures
need no source run directories. Artifact schema version 11 requires re-analysis
of older captures; monitoring sidecars are optional, with no synthetic zeros for
missing measurements.

The MoQ `full_span` starts where a stack first hands an object to MoQ and ends
where MoQ finishes writing the copy, and stacks place that boundary differently,
so it does not compare relays. Three per-copy metrics describe the MoQ layer
itself:

- `moq_rx_work` and `moq_tx_work` sum the object's MoQ phases, which record
  processing and not waiting. A stack that builds and sends packets inside its
  write call runs transport work there, so `moq_tx_work` leaves out every TX
  packet the writing thread started during a write, until the packet ended or
  the write returned.
- `moq_write_after_receive` is the copy's first write minus the end of the
  inbound object's last payload read: negative when the relay forwards bytes
  before the whole object arrives, positive when it waits for the object.

The breakdown's TX payload write row subtracts the same transport work, so it
too charges MoQ only for its own.

Four more per-copy metrics split the end-to-end spans where the layers hand
off. `read_to_moq` runs from the first socket read carrying the inbound object
to the start of its MoQ lifecycle, and `moq_to_send` from the end of the copy's
MoQ lifecycle to the send that completed it, so with `full_span` they sum to
`quic_full_span` for every copy. With a packet capture, `wire_to_read` and
`send_to_wire` add the kernel's share at each end, and the five sum to
`wire_full_span`. Moving the MoQ boundary moves time between the middle
segments without changing the total, which is why `segments.png` explains a
difference in `full_span` between stacks. A segment is negative when its end
comes first: `send_to_wire` usually is, because the capture point sits inside
the send system call.

Some transports call the application while they process an inbound packet;
Google QUICHE, for one, parses and forwards MoQ objects before its packet handler
returns. Such a provider records that time as the `application` packet phase,
which may only appear on RX packets and must not overlap the packet's other
phases. A transport that hands data over after packet processing returns never
records it. `rx_packet_transport_span` subtracts the phase from the packet span,
so it measures the transport's own share of a packet the same way on both kinds
of stack, and `rx_packet_span` keeps the full interval.

Packet lifecycles are bounded by the socket. An RX packet starts when the
receive system call that returned its datagram completes, and a TX packet ends
when the send system call that accepted its datagram completes. Every successful
packet records the matching wait as a phase: `read_queue` from the packet start
until its processing begins, and `send_queue` from the end of encryption until
the packet end. A provider reads the clock immediately after the system call
returns, ends the `udp_socket` operation and every packet the call carried at
that instant with `finish_at`, and records the call's own result, before any
error policy of its stack, as the socket outcome. A packet whose send fails ends
`dropped`. An RX `quic_stream_frame` is stamped when the stream's receive buffer
accepts its bytes, before the application is told, and a frame the stack
discards without buffering is `dropped`; `stream_frame_at` records it once the
outcome is known. Object coverage completes in that order: at send completion
for TX and at buffer acceptance for RX. The analysis rejects a successful packet
without its queue phase.

`quic_connection_path` records the local and peer addresses a connection sends
between, when the connection is created and again whenever its path changes. It
lets analysis join a connection's packets to a packet capture. Addresses are
IPv6, with IPv4 as IPv4-mapped IPv6.

`quic_send_blocked` records an interval in which a connection could not send
data it had, with its reason: the congestion window, pacing, the
anti-amplification limit, connection or stream flow control, or a full send
buffer. A stack keeps one interval open per reason while the condition holds,
per connection for the transmit reasons and per stream for a blocked write, and
it ends when the data could go out. Google QUICHE, mvfst, and the Quinn fork
emit it.

Analysis accepts implementation-independent transport traces by default. Use
`--transport-profile quinn` when the capture must contain the Quinn `routing`
and `scheduling` phases used by the packet processing metric. Cloudflare quiche
and Google QUICHE integrations can use the default profile and report whichever
canonical packet phases they expose.

Every artifact carries one metadata row describing the measurement it holds.
`python/src/moq_trace/metadata.py` is the schema that row must satisfy: a reader
accepts keys a newer writer added, and rejects metadata that does not match the
kind of the artifact it was found in.

## Verification

```sh
nix develop --command just check-tools check-contract
```

The checks also validate provider declarations against the analyzer and compare
real Rust and C++ captures of equivalent lifecycles, including optional metadata,
explicit timestamps, enum values, and abandonment.

For repeatable analysis timings, memory use, and artifact sizes from retained
captures, see the [analysis benchmark](benchmarks/README.md).
