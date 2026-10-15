# Run through the workspace shell: `nix develop --command just <recipe>`.
# The workspace shell runs experiments; each component builds in its own shell.

root := justfile_directory()
patches := root / ".cargo/patches.toml"

# The toolkit's C++ facade and providers, installed for the C++ relays.
install_prefix := root / "moq-trace/target/install"

# The toolkit's development shell carries the Rust toolchain used by moq-bench.
trace_shell := root / "moq-trace"

default:
    @just --list

# Check out every pinned submodule, and make later pulls and checkouts update them.
init:
    git config submodule.recurse true
    git submodule update --init --recursive

# Show each submodule's pin and the state of its working tree.
status:
    git submodule status
    git submodule foreach --quiet 'echo "== $sm_path"; git status --short --branch'

# Advance submodules to their .gitmodules branch tips; commit after check-all passes.
bump *paths:
    git submodule update --remote {{ paths }}
    git diff --submodule=log

# Run the toolkit's own checks.
check-trace:
    cd moq-trace && nix develop --command just check

# Build the local Python decoder with optimization for real capture ingestion.
decoder:
    nix develop {{ root }} --command cargo build --locked --manifest-path python/native/Cargo.toml --target-dir target/ctf --release --features extension-module
    cp target/ctf/release/lib_native_ctf.so python/src/moq_trace/_native_ctf.abi3.so

# Check the native decoder, Python analysis/plots, runner, and provider contract.
check-tools: decoder check-runner
    nix develop {{ root }} --command moq-trace --help
    nix develop {{ root }} --command cargo fmt --manifest-path python/native/Cargo.toml --check
    nix develop {{ root }} --command cargo test --locked --manifest-path python/native/Cargo.toml
    nix develop {{ root }} --command cargo clippy --locked --manifest-path python/native/Cargo.toml --all-targets -- -D warnings
    nix develop {{ root }} --command python -m unittest discover -s python/tests -v
    nix develop {{ root }} --command ruff check python
    nix develop {{ root }} --command ruff format --check python

# Format the local Python tools and native decoder.
fix-tools:
    nix develop {{ root }} --command cargo fmt --manifest-path python/native/Cargo.toml
    nix develop {{ root }} --command ruff check --fix python
    nix develop {{ root }} --command ruff format python
    nix develop {{ root }} --command ruff check --fix --config bench/pyproject.toml bench scripts/check-suite.py
    nix develop {{ root }} --command ruff format --config bench/pyproject.toml bench scripts/check-suite.py

# Compare actual Rust and C++ captures through the workspace decoder.
check-contract: check-trace decoder
    nix develop {{ trace_shell }} --command cargo build --manifest-path moq-trace/Cargo.toml --example facade_contract -p moq-trace --all-features
    nix develop {{ root }} --command python python/checks/facade_contract.py

# Install the toolkit's C++ facade and providers into moq-trace/target/install.
install-trace:
    cd moq-trace && nix develop --command just install

# Build the traced moq-dev-moq relay against the pinned toolkit and forks.
build-moq-dev-moq:
    just _cargo relays/moq-dev-moq build --release -p moq-relay --features moq-relay/trace

# Build the traced Cloudflare moq-rs relay against the pinned toolkit and forks.
build-cloudflare-moq-rs:
    just _cargo relays/cloudflare-moq-rs build --release -p moq-relay-ietf --features trace

# Build the traced QUICHE relay against the toolkit installed from the pin.
build-google-quiche: install-trace
    cd relays/google-quiche && MOQ_TRACE_PREFIX={{ install_prefix }} devenv shell -- moq_trace/build.sh

# Build the traced moxygen relay against the toolkit installed from the pin and
# the pinned mvfst fork.
build-facebook-moxygen: install-trace
    cd relays/facebook-moxygen && MOQ_TRACE_PREFIX={{ install_prefix }} MVFST_SOURCE_DIR={{ root }}/deps/mvfst nix develop --command moq_trace/build.sh

# Release, so a measurement carries no debug-built peer's overhead.
# Build the bench peers against the pinned moq-dev-moq, toolkit, and forks.
build-moq-bench:
    just _cargo moq-bench build --release --features lttng

# Also checks that every relay profile loads and that moq-trace.toml pins the
# protocol the peers pin.
# Test and lint the bench peers, and check the bench suite.
check-bench: check-runner
    just _cargo moq-bench test --all-features
    just _cargo moq-bench clippy --all-targets --all-features -- -D warnings
    cd moq-bench && nix develop {{ trace_shell }} --command cargo fmt --all --check

# Check experiment orchestration and the suite against the workspace's peers.
check-runner:
    nix develop {{ root }} --command python -m unittest discover -s bench/tests -v
    nix develop {{ root }} --command python scripts/check-suite.py
    nix develop {{ root }} --command ruff check --config bench/pyproject.toml bench scripts/check-suite.py
    nix develop {{ root }} --command ruff format --check --config bench/pyproject.toml bench scripts/check-suite.py

# Format the bench peers.
fix-bench:
    cd moq-bench && nix develop {{ trace_shell }} --command cargo fmt --all

# For example `just bench --relay moq-dev-moq,google-quiche --hosts
# experiments/hosts.toml`. Relative paths resolve against the workspace root.
# Run the workspace experiment runner with this workspace's suite.
[positional-arguments]
bench *args: decoder
    nix develop {{ root }} --command moq-workspace-bench bench "$@"

# Run one experiment from TOML configuration, with optional --output.
[positional-arguments]
run *args: decoder
    nix develop {{ root }} --command moq-workspace-bench run "$@"

# Lint the traced Rust relays against the pinned toolkit and forks.
clippy-relays:
    just _cargo relays/moq-dev-moq clippy -p moq-net -p moq-relay --features moq-relay/trace --all-targets -- -D warnings
    just _cargo relays/cloudflare-moq-rs clippy -p moq-relay-ietf --features trace --all-targets -- -D warnings

# Check that the forks' standalone pins build the code the workspace pins.
check-pins:
    scripts/check-pins.sh

# Run every relay's tests with tracing compiled in, including the
# instrumentation's own tests.
test-relays: install-trace
    just _cargo relays/moq-dev-moq test -p moq-net --features moq-net/trace
    just _cargo relays/cloudflare-moq-rs test --workspace --features moq-relay-ietf/trace
    cd relays/google-quiche && MOQ_TRACE_PREFIX={{ install_prefix }} devenv shell -- bash -c 'moq_trace/test.sh && moq_trace/test.sh //quiche:quic_connection_test //quiche:quic_framer_test //quiche:quic_udp_socket_test //quiche:quiche_linux_tests'
    cd relays/facebook-moxygen && MOQ_TRACE_PREFIX={{ install_prefix }} MVFST_SOURCE_DIR={{ root }}/deps/mvfst nix develop --command moq_trace/test.sh

# Capture each QUIC stack's own fixtures under LTTng and check the events with
# the workspace's `moq-trace verify`: Quinn's blocked transfers, QUICHE's packet,
# batch, and stream fixtures, and moxygen's relay session for mvfst. Each script
# runs in its stack's shell, entered from the workspace's, which puts moq-trace on
# PATH. Quinn builds against the pinned toolkit, so its lockfile is put back.
test-trace: install-trace decoder
    #!/usr/bin/env bash
    set -euo pipefail
    lock=$(mktemp)
    cp deps/quinn/Cargo.lock "$lock"
    trap 'mv "$lock" deps/quinn/Cargo.lock' EXIT
    nix develop {{ root }} --command deps/quinn/moq_trace/test_trace.sh --config {{ patches }}
    nix develop {{ root }} --command bash -c 'cd relays/google-quiche && MOQ_TRACE_PREFIX={{ install_prefix }} devenv shell -- moq_trace/test_trace.sh'
    nix develop {{ root }} --command bash -c 'cd relays/facebook-moxygen && MOQ_TRACE_PREFIX={{ install_prefix }} MVFST_SOURCE_DIR={{ root }}/deps/mvfst nix develop --command moq_trace/test_trace.sh'

# Every check, test, and traced build that a pin bump must pass.
check-all: check-contract check-tools check-pins check-bench clippy-relays test-relays test-trace build-moq-dev-moq build-cloudflare-moq-rs build-google-quiche build-facebook-moxygen build-moq-bench

# Run cargo in a component's development shell with the workspace patches. A
# submodule builds in its own shell, and the workspace's own code in the
# toolkit's, which supplies moq-bench's Rust toolchain. The
# patches rewrite Cargo.lock to path sources, so the committed lockfile is put
# back afterwards and a submodule never shows the rewrite as a change. The
# config follows the subcommand: cargo does not forward a --config given before
# it to an external subcommand such as clippy, which would then build the
# upstream crates.
[private]
_cargo dir command *args:
    #!/usr/bin/env bash
    set -euo pipefail
    cd {{ dir }}
    lock=$(mktemp)
    cp Cargo.lock "$lock"
    trap 'mv "$lock" Cargo.lock' EXIT
    shell=$(git rev-parse --show-toplevel)
    if [ "$shell" = {{ quote(root) }} ]; then shell={{ quote(trace_shell) }}; fi
    nix develop "$shell" --command cargo {{ command }} --config {{ patches }} {{ args }}
