//! Counters shared by a peer's publisher and subscriber tasks.

use std::sync::Arc;
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::Duration;

use tokio::time::Instant;

/// Frame, byte, and group counters shared across a peer's tasks.
pub struct Stats {
    connections: AtomicU64,
    frames_sent: AtomicU64,
    bytes_sent: AtomicU64,
    groups_recv: AtomicU64,
    groups_short: AtomicU64,
    subscriptions: AtomicU64,
    mismatches: AtomicU64,
    received: Vec<Received>,
}

#[derive(Default)]
struct Received {
    bytes: AtomicU64,
    objects: AtomicU64,
}

impl Default for Stats {
    fn default() -> Self {
        Self::new(1)
    }
}

/// A point-in-time copy of the counters, used to compute interval rates.
#[derive(Clone, Copy, Default)]
struct Counters {
    frames_sent: u64,
    bytes_sent: u64,
    frames_recv: u64,
    bytes_recv: u64,
    groups_recv: u64,
    groups_short: u64,
    mismatches: u64,
}

impl Stats {
    /// Allocate separate payload delivery counters for each client connection.
    pub fn new(connections: usize) -> Self {
        Self {
            connections: AtomicU64::default(),
            frames_sent: AtomicU64::default(),
            bytes_sent: AtomicU64::default(),
            groups_recv: AtomicU64::default(),
            groups_short: AtomicU64::default(),
            subscriptions: AtomicU64::default(),
            mismatches: AtomicU64::default(),
            received: (0..connections).map(|_| Received::default()).collect(),
        }
    }
    /// Count a session as live until the returned guard drops.
    ///
    /// The reporter logs this as `connections`, which is the counter a runner waits
    /// on before it starts measuring: a peer that has not finished connecting would
    /// publish an empty trace.
    pub fn connection(&self) -> Gauge<'_> {
        self.connections.fetch_add(1, Ordering::Relaxed);
        Gauge(&self.connections)
    }

    /// Record one emitted frame of `bytes` bytes.
    pub fn frame_sent(&self, bytes: usize) {
        self.frames_sent.fetch_add(1, Ordering::Relaxed);
        self.bytes_sent.fetch_add(bytes as u64, Ordering::Relaxed);
    }

    /// Record one received frame of `bytes` bytes.
    pub fn frame_recv(&self, connection: usize, bytes: usize) {
        self.received[connection]
            .bytes
            .fetch_add(bytes as u64, Ordering::Relaxed);
        self.received[connection]
            .objects
            .fetch_add(1, Ordering::Relaxed);
    }

    /// Record one received group.
    pub fn group_recv(&self) {
        self.groups_recv.fetch_add(1, Ordering::Relaxed);
    }

    /// Record a received group that arrived without its keyframe or with fewer
    /// frames than its keyframe declared.
    ///
    /// A relay that joins a group partway or drops a late one produces these, and
    /// so does the end of a run, so they are relay policy or teardown rather than a
    /// broken path.
    pub fn group_short(&self) {
        self.groups_short.fetch_add(1, Ordering::Relaxed);
    }

    /// Record a group with a frame whose length disagrees with the size its
    /// keyframe declared, or with more frames than it declared, which means objects
    /// were truncated or re-framed somewhere on the path.
    pub fn mismatch(&self) {
        self.mismatches.fetch_add(1, Ordering::Relaxed);
    }

    /// Count a subscription as live until the returned guard drops.
    pub fn subscription(&self) -> Gauge<'_> {
        self.subscriptions.fetch_add(1, Ordering::Relaxed);
        Gauge(&self.subscriptions)
    }

    /// Log a counter line every `interval` until the task is dropped.
    pub async fn report(self: Arc<Self>, interval: Duration) {
        let delivery = std::env::var_os("MOQ_BENCH_DELIVERY").is_some();
        let mut last_delivery = Instant::now();
        if delivery {
            self.report_delivery();
        }
        // A fixed schedule keeps report times from drifting by the time each report
        // takes. A stalled report skips the ticks it missed rather than firing them in
        // a burst, and every rate divides by the time actually elapsed.
        let mut ticker = tokio::time::interval(interval);
        ticker.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Skip);
        // The first tick completes at once and would report an empty interval.
        ticker.tick().await;

        let mut previous = self.counters();
        let mut last = Instant::now();

        loop {
            ticker.tick().await;

            let elapsed = last.elapsed().as_secs_f64();
            last = Instant::now();
            let current = self.counters();
            let sent = (current.frames_sent - previous.frames_sent) as f64 / elapsed;
            let recv = (current.frames_recv - previous.frames_recv) as f64 / elapsed;
            let send_mbps = (current.bytes_sent - previous.bytes_sent) as f64 * 8.0 / elapsed / 1e6;
            let recv_mbps = (current.bytes_recv - previous.bytes_recv) as f64 * 8.0 / elapsed / 1e6;
            let mismatches = current.mismatches - previous.mismatches;
            previous = current;

            tracing::info!(
                connections = self.connections.load(Ordering::Relaxed),
                subscriptions = self.subscriptions.load(Ordering::Relaxed),
                send_fps = format!("{sent:.1}"),
                recv_fps = format!("{recv:.1}"),
                send_mbps = format!("{send_mbps:.2}"),
                recv_mbps = format!("{recv_mbps:.2}"),
                groups_recv = current.groups_recv,
                groups_short = current.groups_short,
                mismatches,
                "stats"
            );
            if delivery && last_delivery.elapsed() >= Duration::from_secs(1) {
                self.report_delivery();
                last_delivery = Instant::now();
            }
        }
    }

    /// Write cumulative received payload counters on the Linux host clock.
    pub fn report_delivery(&self) {
        let mut instant = libc::timespec {
            tv_sec: 0,
            tv_nsec: 0,
        };
        // SAFETY: instant points to a valid, writable timespec. Linux's monotonic
        // clock is also the clock used by the resource collector and trace hooks.
        let status = unsafe { libc::clock_gettime(libc::CLOCK_MONOTONIC, &mut instant) };
        assert_eq!(status, 0, "CLOCK_MONOTONIC unavailable");
        let ns = instant.tv_sec as u64 * 1_000_000_000 + instant.tv_nsec as u64;
        let pid = std::process::id();
        for (connection, received) in self.received.iter().enumerate() {
            let bytes = received.bytes.load(Ordering::Relaxed);
            let objects = received.objects.load(Ordering::Relaxed);
            println!(
                "MOQ_BENCH_DELIVERY {{\"schema_version\":1,\"monotonic_ns\":{ns},\"pid\":{pid},\"connection\":{connection},\"bytes\":{bytes},\"objects\":{objects}}}"
            );
        }
    }

    fn counters(&self) -> Counters {
        Counters {
            frames_sent: self.frames_sent.load(Ordering::Relaxed),
            bytes_sent: self.bytes_sent.load(Ordering::Relaxed),
            frames_recv: self
                .received
                .iter()
                .map(|r| r.objects.load(Ordering::Relaxed))
                .sum(),
            bytes_recv: self
                .received
                .iter()
                .map(|r| r.bytes.load(Ordering::Relaxed))
                .sum(),
            groups_recv: self.groups_recv.load(Ordering::Relaxed),
            groups_short: self.groups_short.load(Ordering::Relaxed),
            mismatches: self.mismatches.load(Ordering::Relaxed),
        }
    }
}

/// A live count. Drops the counter it was created for when the session or
/// subscription ends, including when its task is aborted, so the reported gauge
/// tracks reality rather than attempts.
pub struct Gauge<'a>(&'a AtomicU64);

impl Drop for Gauge<'_> {
    fn drop(&mut self) {
        self.0.fetch_sub(1, Ordering::Relaxed);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn delivery_is_counted_per_connection_and_aggregated_once() {
        let stats = Stats::new(2);
        stats.frame_recv(0, 100);
        stats.frame_recv(1, 200);
        stats.frame_recv(1, 300);
        assert_eq!(stats.received[0].bytes.load(Ordering::Relaxed), 100);
        assert_eq!(stats.received[1].objects.load(Ordering::Relaxed), 2);
        assert_eq!(stats.counters().bytes_recv, 600);
        assert_eq!(stats.counters().frames_recv, 3);
    }

    /// The connection gauge counts live sessions. A runner gates on `connections=N`
    /// before starting its window, so a session that already left must not hold the
    /// count up.
    #[test]
    fn connection_gauge_tracks_live_sessions() {
        let stats = Stats::default();
        let first = stats.connection();
        let second = stats.connection();
        assert_eq!(stats.connections.load(Ordering::Relaxed), 2);

        drop(first);
        assert_eq!(stats.connections.load(Ordering::Relaxed), 1);

        drop(second);
        assert_eq!(stats.connections.load(Ordering::Relaxed), 0);
    }
}
