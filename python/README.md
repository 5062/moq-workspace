# Workspace trace analysis tools

This directory owns decoding, analysis, artifact schemas, plots, capture helpers,
verification, and the Rust CTF decoder for local MoQ development and testing.
The `moq_trace` import name and `moq-trace` command are unchanged. Providers and
Rust/C++ instrumentation remain in `moq-trace/`; experiment orchestration lives
in `bench/`.

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

## Capture and analysis

From the workspace root, build the native CTF reader before analyzing captures:

```sh
nix develop --command just decoder
nix develop --command moq-trace --help
```

The workspace shell loads `python/src` and `bench/src` directly. It provides Rust,
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
`monitoring.resources` and `monitoring.deliveries` retain raw samples;
`monitoring.resource_intervals` and `monitoring.delivery_intervals` store rates
from actual counter deltas and elapsed time. Their `*_summary` tables use only
complete intervals within `model.window`, with explicit `covered_seconds`.
CPU cores are total process CPU seconds divided by wall seconds. RSS is sampled
resident memory, not heap size. Delivered bytes exclude QUIC overhead and count
each subscriber's received copy; UDP throughput includes transport traffic.
The comparison snapshot copies the monitoring summaries so comparison figures
need no source run directories. Artifact schema version 10 requires re-analysis
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
it ends when the data could go out. Google QUICHE and mvfst emit it; the Quinn
fork does not yet.

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
