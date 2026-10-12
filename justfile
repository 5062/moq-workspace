# Run through the workspace shell: `nix develop --command just <recipe>`.
# Each component builds inside its own development shell, so this shell only
# carries git and just.

root := justfile_directory()
patches := root / ".cargo/patches.toml"

# The toolkit's C++ facade and providers, installed for the C++ relays.
install_prefix := root / "moq-trace/target/install"

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

# Build the bench peers against the pinned moq-dev-moq, toolkit, and forks.
build-moq-bench:
    just _cargo moq-trace/moq-bench build --release --features lttng

# Lint the traced Rust relays against the pinned toolkit and forks.
clippy-relays:
    just _cargo relays/moq-dev-moq clippy -p moq-net -p moq-relay --features moq-relay/trace --all-targets -- -D warnings
    just _cargo relays/cloudflare-moq-rs clippy -p moq-relay-ietf --features trace --all-targets -- -D warnings

# Check that the forks' standalone pins build the code the workspace pins.
check-pins:
    scripts/check-pins.sh

# Run every relay's tests with tracing compiled in, including the
# instrumentation's own tests. QUICHE's test_trace.sh captures its transport
# fixtures with LTTng and checks the events.
test-relays: install-trace
    just _cargo relays/moq-dev-moq test -p moq-net --features moq-net/trace
    just _cargo relays/cloudflare-moq-rs test --workspace --features moq-relay-ietf/trace
    cd relays/google-quiche && MOQ_TRACE_PREFIX={{ install_prefix }} devenv shell -- bash -c 'moq_trace/test.sh && moq_trace/test.sh //quiche:quic_connection_test //quiche:quic_framer_test //quiche:quic_udp_socket_test //quiche:quiche_linux_tests && moq_trace/test_trace.sh'
    cd relays/facebook-moxygen && MOQ_TRACE_PREFIX={{ install_prefix }} MVFST_SOURCE_DIR={{ root }}/deps/mvfst nix develop --command moq_trace/test.sh

# Every check, test, and traced build that a pin bump must pass.
check-all: check-trace check-pins clippy-relays test-relays build-moq-dev-moq build-cloudflare-moq-rs build-google-quiche build-facebook-moxygen build-moq-bench

# Run cargo in a component's development shell with the workspace patches. The
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
    nix develop "$shell" --command cargo {{ command }} --config {{ patches }} {{ args }}
