//! Implementation-independent MoQ peers for cross-implementation trace measurements.
//!
//! Every experiment compares relays this toolkit does not own, which only holds up
//! if the peers are the constant side of the measurement: one pinned protocol
//! version, one implementation, and no relay policy that can vary between runs.
//!
//! The server is a reflexive origin with no auth, cluster, or cache. The client is
//! a synthetic workload driver. Both are built from the same `moq-net` and
//! `moq-native`, so their sessions exercise the toolkit's instrumentation sites the
//! same way and produce comparable traces.
//!
//! These peers are a fixture and a baseline, never a comparison row of their own.
//! See `README.md` for the roles they play.

#![warn(missing_docs)]

pub mod object;
pub mod publish;
pub mod stats;
pub mod subscribe;

use std::str::FromStr;

use moq_net::Versions;

/// The MoQ version every bench peer speaks.
///
/// MoQ-lite and the other IETF drafts are deliberately unreachable. A peer that
/// negotiated a different version would silently produce a trace that cannot be
/// compared with the rest of the matrix.
pub const VERSION: &str = "moq-transport-16";

/// Install the process-wide rustls crypto provider.
///
/// `moq-native` builds rustls with `aws-lc-rs`, and rustls only selects a default
/// provider when exactly one is linked. Installing it explicitly keeps the choice
/// from depending on the rest of the dependency tree.
pub fn install_crypto() {
    rustls::crypto::aws_lc_rs::default_provider()
        .install_default()
        .expect("no other process-wide crypto provider is installed");
}

/// The single MoQ version set both peers offer and accept.
#[must_use]
pub fn versions() -> Versions {
    let version = moq_net::Version::from_str(VERSION).expect("VERSION is a known MoQ version");
    Versions::from(vec![version])
}

/// Raise this process's open-file limit to its hard limit, and check that it
/// leaves room for `sockets` more files.
///
/// Every client session binds its own UDP socket, so a peer with a thousand
/// sessions outgrows the usual soft limit of 1024 and fails with a bind error
/// partway through connecting.
pub fn raise_open_file_limit(sockets: u64) -> anyhow::Result<()> {
    let mut limit = libc::rlimit {
        rlim_cur: 0,
        rlim_max: 0,
    };
    // SAFETY: limit points to a valid, writable rlimit.
    anyhow::ensure!(
        unsafe { libc::getrlimit(libc::RLIMIT_NOFILE, &mut limit) } == 0,
        "getrlimit failed: {}",
        std::io::Error::last_os_error()
    );
    if limit.rlim_cur < limit.rlim_max {
        limit.rlim_cur = limit.rlim_max;
        // SAFETY: limit is a valid rlimit whose soft value does not exceed its hard one.
        anyhow::ensure!(
            unsafe { libc::setrlimit(libc::RLIMIT_NOFILE, &limit) } == 0,
            "setrlimit failed: {}",
            std::io::Error::last_os_error()
        );
    }
    // Room for the runtime, logs, and TLS beside the sessions' sockets.
    let needed = sockets.saturating_add(256);
    anyhow::ensure!(
        limit.rlim_cur >= needed,
        "{sockets} sessions need about {needed} open files, but the hard limit is {}; raise `nofile` for this user",
        limit.rlim_cur
    );
    Ok(())
}

/// The synthetic object shape a publisher emits.
///
/// Every frame in a group is exactly `frame_size` bytes, the keyframe included,
/// so object size stays a clean independent variable across runs.
#[derive(clap::Args, Clone, Copy, Debug)]
#[non_exhaustive]
pub struct Shape {
    /// Frames emitted per second per track. Zero publishes the track but stays idle.
    #[arg(long, env = "MOQ_BENCH_FPS", default_value_t = Shape::default().fps)]
    pub fps: u64,
    /// Bytes per frame. At least the keyframe header and the send-time stamp.
    #[arg(
        long,
        env = "MOQ_BENCH_FRAME_SIZE",
        default_value_t = Shape::default().frame_size,
        value_parser = clap::value_parser!(u64).range(object::MIN_FRAME_LEN as u64..)
    )]
    pub frame_size: u64,
    /// Zeroed frames per group following the keyframe. May be zero.
    #[arg(long, env = "MOQ_BENCH_GROUP_SIZE", default_value_t = Shape::default().group_size)]
    pub group_size: u64,
}

impl Default for Shape {
    fn default() -> Self {
        Self {
            fps: 30,
            frame_size: 16_384,
            group_size: 0,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn open_file_limit_rises_to_the_hard_limit() {
        raise_open_file_limit(1).unwrap();
        let mut limit = libc::rlimit {
            rlim_cur: 0,
            rlim_max: 0,
        };
        // SAFETY: limit points to a valid, writable rlimit.
        assert_eq!(
            unsafe { libc::getrlimit(libc::RLIMIT_NOFILE, &mut limit) },
            0
        );
        assert_eq!(limit.rlim_cur, limit.rlim_max);
    }
}
