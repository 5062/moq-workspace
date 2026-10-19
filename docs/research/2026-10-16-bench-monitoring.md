# Traced bench resource and network monitoring

Research date: 2026-10-16. Recommendation: add lightweight process CPU and memory sampling; retain packet capture and full qlog as explicit diagnostic modes. A 1 s default (optionally 500 ms) is an engineering starting point, not a measured overhead guarantee.

## Existing behavior

[`python/README.md`](../../python/README.md#running-experiments) documents relay-host packet capture for directional UDP throughput and qlog for RTT, loss, and congestion window. Both require opt-in flags. It warns that per-packet qlog serialization affects latency measurements. Capture wall time is mapped to the trace clock; qlog must provide its monotonic start instant. Only the relay host is currently traced.

## Process baseline

Read relay `/proc/<pid>/stat` once per second, retaining raw user/system CPU ticks, process start time, and actual sample timestamp. CPU utilization in cores is `delta(utime + stime) / SC_CLK_TCK / delta_seconds`; 1 core means 100%, so a multithreaded relay can exceed 100%. Keep children separate rather than adding waited-for child counters. Start time plus PID helps reject PID reuse. These counters and units are defined by [Linux proc_pid_stat(5)](https://man7.org/linux/man-pages/man5/proc_pid_stat.5.html).

Read RSS from `statm`, converting pages to bytes using the host page size. Treat it as approximate resident memory, not heap allocation. Linux documents RSS inaccuracies and says `smaps`/`smaps_rollup` are more accurate but much slower; reserve those for focused investigations. Sampled maximum RSS can miss short peaks. [Linux proc_pid_statm(5)](https://man7.org/linux/man-pages/man5/proc_pid_statm.5.html).

Run one persistent collector per measured host rather than launching commands or SSH sessions per sample. Timestamp readings with explicit `CLOCK_MONOTONIC` nanoseconds and retain host/process identity. Python exposes `clock_gettime_ns(CLOCK_MONOTONIC)`; monotonic time avoids wall-clock jumps. Use actual elapsed intervals because sampling wakeups can run late. Align same-host samples directly with traces; remote hosts have independent clock origins. [Python time documentation](https://docs.python.org/3/library/time.html#time.clock_gettime_ns).

## Network interpretation

Report application delivered payload goodput separately from UDP throughput: the former measures useful delivery; the latter includes QUIC overhead and repeated transmissions. Keep direction, connection, and workload window explicit.

Use transport RTT estimates per connection/path. QUIC samples measure send-to-ACK elapsed time; smoothed RTT adjusts for reported ACK delay. ICMP ping measures a different exchange. Label recovery events **QUIC-declared lost packets**, not measured path loss: QUIC infers loss from acknowledgement and time thresholds, and reordering can produce spurious declarations. [RFC 9002 Sections 5–6](https://www.rfc-editor.org/rfc/rfc9002.html#section-5).

Keep capture drops separate. libpcap distinguishes capture-buffer drops and interface drops, warns counters vary by platform, and does not store them in ordinary capture savefiles. Persist capture statistics alongside recordings. [libpcap pcap_stats(3pcap)](https://man7.org/linux/man-pages/man3/pcap_stats.3pcap.html).

## Overhead controls

Compare matched runs: untraced, trace plus baseline sampling, and trace plus full network diagnostics. Keep warmup and measurement windows identical. Optionally sample peer CPU/RSS to identify generator/receiver bottlenecks. Save raw sidecars with capture artifacts so re-analysis remains offline; avoid per-packet logging in the default latency run.
