# MoQ trace workspace

Pins the MoQ trace toolkit, the instrumented relays, and the transport forks they
build on, so that one commit of this repository names a set of revisions that
were checked together. It also holds what a cross-implementation bench needs
beyond those: the reference peers, a launch profile per relay, and the hosts the
experiments run on.

| Path | Contents |
|---|---|
| `bench/` | Python experiment runner and its tests; see `bench/README.md` |
| `moq-bench/` | The implementation-independent reference peers; see `moq-bench/README.md` |
| `profiles/` | One `just bench` launch profile per relay |
| `moq-trace.toml` | The bench suite: the profiles, the peers, and the protocol every relay negotiates |
| `experiments/` | Host configurations for distributed runs |
| `docs/` | The relay processing taxonomy and cross-implementation plans |
| `draft/` | Notes outside the toolkit's scope |

The submodules:

| Path | Repository | Branch |
|---|---|---|
| `moq-trace/` | [5062/moq-trace](https://github.com/5062/moq-trace) | `main` |
| `relays/moq-dev-moq/` | [5062/moq](https://github.com/5062/moq) | `moq-trace` |
| `relays/cloudflare-moq-rs/` | [5062/moq-rs](https://github.com/5062/moq-rs) | `moq-trace` |
| `relays/google-quiche/` | [5062/quiche](https://github.com/5062/quiche) | `moq-trace` |
| `relays/facebook-moxygen/` | [5062/moxygen](https://github.com/5062/moxygen) | `moq-trace` |
| `deps/quinn/` | [5062/quinn](https://github.com/5062/quinn) | `moq-trace/quinn-0.11` |
| `deps/web-transport/` | [5062/web-transport](https://github.com/5062/web-transport) | `moq-trace/web-transport-quinn-v0.11.11` |
| `deps/mvfst/` | [5062/mvfst](https://github.com/5062/mvfst) | `moq-trace` |

## Setup

```sh
git clone --recurse-submodules https://github.com/5062/moq-workspace
cd moq-workspace
nix develop --command just init
```

`just init` sets `submodule.recurse`, so `git pull` and `git checkout` move the
submodules to the pins they record. Without it, a pull updates only the pins and
leaves the old code checked out.

Optionally, install direnv with nix-direnv and enable direnv's hook in your
shell, then run `direnv allow` from the workspace root. The `.envrc` loads the
workspace's Nix shell, so you can run `just` directly:

```sh
just build-moq-dev-moq build-moq-bench
just bench --relay moq-dev-moq --subscribers 4
```

Each recipe still enters the component's own development shell when needed.

## How builds use the pins

Every fork names its dependencies by git branch or rev so that it builds on its
own. Inside the workspace, the `just` recipes pass `.cargo/patches.toml` to
Cargo, which replaces those sources with the submodules here: one `moq-trace`, the
pinned Quinn and web-transport, and for the bench peers the pinned moq-dev-moq.

A patch rewrites the consumer's `Cargo.lock`, so the recipes restore the
committed lockfile when cargo exits. A plain `cargo build` in a submodule ignores
the workspace and builds that fork exactly as it builds standalone.

QUICHE and moxygen read the toolkit's C++ install from `MOQ_TRACE_PREFIX`, which
`just build-google-quiche` and `just build-facebook-moxygen` point at
`moq-trace/target/install` after installing the pinned toolkit there. moxygen's
first build also fetches folly, fizz, wangle, and proxygen at the revisions its
checkout pins, and needs well over 4 GB of memory. Its transport, mvfst, comes
from `deps/mvfst`, which `just build-facebook-moxygen` passes as
`MVFST_SOURCE_DIR`, the way the cargo patches replace the Rust forks' sources. A
standalone traced moxygen build fetches the same fork at the revision in its
`moq_trace/mvfst-rev.txt`, so commit that file whenever the `deps/mvfst` pin
moves.

A fork built outside the workspace builds from the revisions it names: its
`Cargo.lock`, git revs in its Cargo manifests and configs, and moxygen's
`mvfst-rev.txt`. `just check-pins` fails when one of those is not in the history
of the matching submodule's pin or builds different code from it. Fix it in the
fork, for example with `cargo update -p moq-trace` after the toolkit pin moves,
then commit. A pin may trail by commits that change nothing it builds. moq-bench
lives here and always builds through the patches, so it has no standalone pins.

## Running benches

`just bench` runs the workspace experiment runner from the workspace root with the suite in
`moq-trace.toml`, so each profile's relay is the pinned submodule under
`relays/`:

```sh
nix develop --command just build-moq-dev-moq build-moq-bench
nix develop --command just bench --relay moq-dev-moq --subscribers 4
```

Inside the workspace shell, `moq-workspace-bench bench` does the same. Build
each relay you bench first, and see [the runner documentation](bench/README.md)
for workload flags, sweeps, packet capture, and distributed runs.

For a distributed run, `--hosts experiments/hosts.toml` places the roles on other
hosts, each with a workspace checkout at its `checkout`. The hosts need only ssh,
git, and Nix, and QUICHE's relay host devenv too: before each run, the bench
clones the workspace there if it is missing and checks out this checkout's
commit, with only the submodules each host's roles need, `submodules` in
`moq-trace.toml` and a relay profile's own. It then builds the relay with the
profile's `relay_build`, which runs the relay's `just build-*` recipe, and the
peers with `just build-moq-bench`.

The hosts fetch the commit and its submodules' commits from GitHub, so push
before a distributed run. A bench refuses to start while tracked files here have
uncommitted changes, which the hosts would not see.

## Changing the event contract

A contract change spans the toolkit and every relay:

1. Commit the change in `moq-trace/` and in each affected fork, on the branch
   listed above. Submodules check out a detached commit, so switch to the branch
   first, for example `git -C relays/moq-dev-moq switch moq-trace`.
2. Run `nix develop --command just check-all`, which runs the toolkit checks,
   checks the forks' standalone pins, tests the bench peers and checks the bench
   suite, lints the traced Rust relays, runs every relay's tests with tracing
   compiled in, and builds every traced relay and the bench peers against the
   working trees.
3. Push each submodule's branch, then commit the new pins here and push. Pushing
   the pins first would record commits that nobody else can fetch.

To pick up commits that others pushed to the forks, run `just bump` for all
submodules or `just bump relays/google-quiche` for one, then `just check-all`,
then commit.

Submodule remotes fetch over HTTPS. Set a push URL in a submodule to push over
SSH, for example
`git -C deps/quinn remote set-url --push origin git@github.com:5062/quinn.git`.

## Recipes

Run `nix develop --command just` to list them. `check-all` is the gate for any
commit that changes a pin.
