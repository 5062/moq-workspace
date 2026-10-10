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

# Build the bench peers against the pinned moq-dev-moq, toolkit, and forks.
build-moq-bench:
    just _cargo moq-trace/moq-bench build --release --features lttng

# Every check and traced build that a pin bump must pass.
check-all: check-trace build-moq-dev-moq build-cloudflare-moq-rs build-google-quiche build-moq-bench

# Run cargo in a component's development shell with the workspace patches. The
# patches rewrite Cargo.lock to path sources, so the committed lockfile is put
# back afterwards and a submodule never shows the rewrite as a change.
[private]
_cargo dir *args:
    #!/usr/bin/env bash
    set -euo pipefail
    cd {{ dir }}
    lock=$(mktemp)
    cp Cargo.lock "$lock"
    trap 'mv "$lock" Cargo.lock' EXIT
    shell=$(git rev-parse --show-toplevel)
    nix develop "$shell" --command cargo --config {{ patches }} {{ args }}
