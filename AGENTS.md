# MoQ workspace development

This workspace owns local development/testing tools and coordinates pinned
instrumentation, transport, and relay forks. These tools are not an upstream
integration or a product for independent consumers.

## Python tooling

`python/` owns `moq_trace`: decoding, analysis, artifact schemas, plotting,
capture helpers, verification, and the Rust CTF reader in `python/native/`.
`bench/` owns experiment workloads, relay launch profiles, local/SSH host
placement, checkout synchronization, and capture orchestration.

Keep imports flowing from `decode` to `analysis` to `plot` to `run`; lower layers
never import higher ones. Root modules `errors`, `manifest`, and `metadata` are
shared contracts and import none of those layers. The bench runner may consume
all layers. Re-analyzing an existing capture requires no SSH or live hosts.
`python/tests/test_layering.py` enforces the direction.

Correlate providers by process, direction, connection ID, stream ID, and half-open
byte ranges. IDs are process-local; one recording may contain several processes.
Timestamps use the host monotonic clock. Analyze one process's slice and retain
its thread context so work on different threads is not treated as nested work.

Provider schemas and artifact schemas describe the current checkout. Reject
unknown events, fields, or schema versions; re-record or re-analyze older data
instead of adding compatibility layers or migrations. Coordinate provider/facade
changes in `moq-trace/` with the workspace reader and integration checks.

Relay profiles contain launch settings only. Workload settings are shared across
all relays in a bench. Keep relay-specific behavior in profile data rather than
branches on relay names in the runner or analysis.

## Checks

Run commands through `nix develop --command just RECIPE`. `fix-tools` formats the
Python tools and native decoder. `check-tools` tests the
Python tooling, native decoder, and runner. `check-contract` runs the toolkit's
unit checks and compares actual Rust/C++ captures. `check-bench` also checks the
reference peers. `check-all` is the gate for changes to committed submodule pins.

Keep each submodule's changes in that repository; update workspace pins after
its commits exist. Implementation repositories own hook placement. See each
repository's own instructions before changing its source.
