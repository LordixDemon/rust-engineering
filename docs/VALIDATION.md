# Rust knowledge validation

English | [Русский](VALIDATION.ru.md)

This is a historical validation record from the original implementation, before this repository was extracted. Versions and corpus sizes below apply to that date, not to a fresh clone. The standalone repository includes its engine and tests locally; build the current database with `python3 rkb.py update`.

Validated on September 12, 2026, in Europe/Kiev; September 11 in UTC.

## Versions and data

Rust **1.98.1 stable** and Cargo **1.98.1** were active on `aarch64-apple-darwin`. Compiler commit `48a229ceaefd4985c50990b14116b6d856af0985` matched the indexed Rust source.

Books and the standard library were pinned to Rust 1.98.1. Main crate versions in the validated snapshot:

| Crate | Version |
|---|---|
| Tokio | 1.53.1 |
| Axum | 0.8.9 |
| Serde | 1.0.229 |
| SQLx | 0.9.0 |
| Tracing | 0.1.44 |
| Tower | 0.5.3 |
| Thiserror | 2.0.20 |
| Anyhow | 1.0.104 |

These are each source's main crate versions; sibling monorepo packages have independent versions. SQLx 0.9.0's manifest declares MSRV 1.94.0.

All 14 sources were retrieved, updated online and rebuilt offline successfully. Validated snapshot: `20260911T210954-bf534c79`; sources were checked around 21:05 UTC and the graph was built at 21:10 UTC. The offline rebuild preserved the network check time.

| Metric | Value |
|---|---:|
| Documents / files | 2,922 |
| Search chunks | 13,686 |
| Graph nodes | 48,947 |
| Edges with source locations | 102,804 |
| Skipped ambiguous mentions | 64,778 |

The repeated offline build recognized all 2,922 files as unchanged and built a new graph.

## Utility and search

The common engine was checked with `python3 -W error::ResourceWarning -m unittest discover -s tests -q` from the original `flutter-flame-knowledge` directory: **30 tests passed**, comprising 19 existing checks and 11 Rust support checks.

Checks covered Rust declarations and module links with original source lines, nested comments/raw strings/lifetimes, standalone manifests, rustdoc headings, release-pinned books, stable selection excluding prerelease/yanked versions, multiple Cargo.lock versions and sources, and compiler/MSRV/dependency diagnostics. Common checks covered updates, snapshot preservation on failure, writer locking, search and skill installation.

Ruff: **All checks passed**. The system skill-creator validator reported **Skill is valid**.

The installed wrapper retrieved Tokio context with exact commits and source lines. Five source-scoped queries were run after rebuilding:

| Query | Result among the first five chunks |
|---|---|
| `JoinSet` / tokio | Definition in `tokio/src/task/join_set.rs`, first place |
| `CancellationToken` / tokio | `tokio-util/src/sync/cancellation_token.rs`, first place |
| `Serialize` / serde | `serde_core/src/ser/mod.rs`, second place |
| `Router` / axum | Guides on merging, state and nesting routers |
| `PgPool` / sqlx | Official PostgreSQL examples and test-database documentation |

This is a small navigation check for known APIs, not a complete evaluation of search quality or skill responses.

## Rust application check

An isolated edition 2024 probe crate was created in `.data/verification/server_probe` with Axum 0.8.9, Tokio 1.53.1, Tower 0.5.3 and Thiserror 2.0.20. It models match-state ownership inside a task and command handling through a bounded channel.

Results:

- `cargo fmt --check` — passed;
- `cargo clippy --all-targets --locked -- -D warnings` — passed;
- `cargo test` — **4 tests passed**;
- `doctor --project .data/verification/server_probe --strict` — Rust release/commit and indexed dependency versions matched, with no mismatches.

Tests covered ordered and repeated commands without double application, draining accepted commands after senders close, concurrent duplicates, rejection when the queue is full and an HTTP route without opening a socket. `doctor` lists unindexed transitive dependencies and does not claim full dependency-tree compatibility.

The crate is an API application probe, not a complete game server. Validation ran on macOS arm64; Linux deployment, real networking, databases, load, anti-cheat and a complete PvP protocol were not checked here.
