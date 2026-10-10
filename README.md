# MoQ trace workspace

Pins the MoQ trace toolkit, the instrumented relays, and the transport forks they
build on, so that one commit of this repository names a set of revisions that
were checked together.

| Path | Repository | Branch |
|---|---|---|
| `moq-trace/` | [5062/moq-trace](https://github.com/5062/moq-trace) | `main` |
| `relays/moq-dev-moq/` | [5062/moq](https://github.com/5062/moq) | `moq-trace` |
| `relays/cloudflare-moq-rs/` | [5062/moq-rs](https://github.com/5062/moq-rs) | `moq-trace` |
| `relays/google-quiche/` | [5062/quiche](https://github.com/5062/quiche) | `moq-trace` |
| `relays/facebook-moxygen/` | [5062/moxygen](https://github.com/5062/moxygen) | `moq-trace` |
| `deps/quinn/` | [5062/quinn](https://github.com/5062/quinn) | `moq-trace/quinn-0.11` |
| `deps/web-transport/` | [5062/web-transport](https://github.com/5062/web-transport) | `moq-trace/web-transport-quinn-v0.11.11` |

## Setup

```sh
git clone --recurse-submodules https://github.com/5062/moq-workspace
cd moq-workspace
nix develop --command just init
```

`just init` sets `submodule.recurse`, so `git pull` and `git checkout` move the
submodules to the pins they record. Without it, a pull updates only the pins and
leaves the old code checked out.

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
first build also fetches folly, fizz, wangle, mvfst, and proxygen at the
revisions its checkout pins, and needs well over 4 GB of memory.

## Changing the event contract

A contract change spans the toolkit and every relay:

1. Commit the change in `moq-trace/` and in each affected fork, on the branch
   listed above. Submodules check out a detached commit, so switch to the branch
   first, for example `git -C relays/moq-dev-moq switch moq-trace`.
2. Run `nix develop --command just check-all`, which runs the toolkit checks and
   builds every traced relay and the bench peers against the working trees.
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
