# Rust Engineering

English | [Русский](README.ru.md)

A local, updatable knowledge base of official Rust documentation and crate sources, with search, an evidence graph and the `$rust-engineering` Codex skill. The skill selects APIs against actual project versions and verifies results with the compiler, Clippy and tests. Its server guidance covers match-state ownership, command ordering, duplicates, bounded queues, task shutdown and reward transactions.

This repository is standalone. The engine is bundled in `flutter_kb/` under its historical Python package name; a sibling Flutter project is not required. Requirements: Python 3.11+ with its standard library and SQLite FTS5, Git, Linux or macOS, and access to public GitHub, crates.io and static.rust-lang.org. `doctor` also requires an installed Rust toolchain. No API keys, Python runtime dependencies or database server are needed.

## Install from GitHub

```bash
git clone https://github.com/LordixDemon/rust-engineering.git
cd rust-engineering
python3 rkb.py install-skill
python3 rkb.py update
```

The skill source is in `skill/rust-engineering/`. Its wrapper requires this companion checkout: installation records its path in a local `installation.json`. The `.data/` cache, downloaded upstream sources and local paths are excluded from Git; `update` builds your own database. Run checks without network access:

```bash
python3 -W error::ResourceWarning -m unittest discover -s tests -q
```

## Commands

Run from the `rust-engineering` directory:

```bash
# Discover current releases, fetch sources and build a new database and graph
python3 rkb.py update

# Refresh when the last source check is older than one day
python3 rkb.py update --if-older 24

# Completely rebuild the index and graph from cache without network access
python3 rkb.py rebuild

# Inspect coverage, versions and project environment compatibility
python3 rkb.py status
python3 rkb.py doctor --project /path/to/rust-project --strict

# Retrieve bounded context with precise sources
python3 rkb.py context 'bounded channel backpressure' --source tokio --max-chars 12000
python3 rkb.py search 'with_graceful_shutdown' --source axum
python3 rkb.py related JoinSet --source tokio --limit 20
python3 rkb.py read 'tokio:tokio/src/task/join_set.rs' --start 1 --lines 100

# Locate the graph, manifest and report
python3 rkb.py graph

# Install or update the skill copy owned by this application
python3 rkb.py install-skill
```

Commands return JSON; update progress goes to stderr. Global options `--data-dir` and `--config` go before the subcommand. Use `RKB_DATA_DIR` for another storage location. By default, data lives in this checkout's `.data/` and is separate from the Flutter database.

Use plain `update` for an explicit latest-version request: `--if-older` trusts a sufficiently fresh cache. Updating the database does not run `rustup update` or modify `Cargo.toml` or `Cargo.lock`. Toolchain and dependency upgrades are separate project work and require migration checks.

## Sources and versions

[sources.json](sources.json) defines coverage; every snapshot manifest records the actual versions and commits.

| Source | Indexed content | Version policy |
|---|---|---|
| Rust | `core`, `alloc`, `std`, Rustc Book | Exact current stable Rust tag |
| The Rust Programming Language | Book sources | Submodule commit shipped with that Rust release |
| Rust Reference | Language reference | Submodule commit shipped with that Rust release |
| Cargo Book | Cargo guide | Cargo commit shipped with that Rust release |
| Edition Guide | Language edition guide | Submodule commit shipped with that Rust release |
| Rustonomicon | Ownership, unsafe and low-level invariants | Submodule commit shipped with that Rust release |
| Tokio | Core, examples, `tokio-util` | Main `tokio` crate release |
| Axum | API, inline documentation and examples | `axum` release |
| Serde | `serde`, `serde_core`, derive and README | `serde` release |
| SQLx | Main crate, core, PostgreSQL, macros and examples | `sqlx` release |
| Tracing | Main crate, subscriber, attributes and examples | `tracing` release |
| Tower | `tower`, `tower-service` and README | `tower` release |
| Thiserror | Main crate, derive and README | `thiserror` release |
| Anyhow | Source and README | `anyhow` release |

Stable Rust is resolved once per update from the official channel manifest. Books use that release's submodule commits to avoid mixing them with newer development branches. Libraries use the latest published stable release not marked `yanked`; the exact Git tag, main crate name and `Cargo.toml` version are then checked. Missing tags or mismatched manifests fail the update while preserving the previous snapshot.

Sibling crates in a monorepo have independent versions. Including `tokio-util` in a Tokio snapshot does not mean its version matches Tokio or that its own latest release was checked. Read its manifest and compare it with the project. Standard-library sources can also contain unstable and target-specific APIs; a snapshot version does not make every symbol available on stable for every platform.

Coverage is limited to configured paths and text formats. It does not include all of crates.io, every Rust resource, the complete compiler, all SQLx drivers, issue discussions, videos or the full rust-analyzer implementation. Unsupported formats, oversized files and other omissions are recorded in the report. The default maximum file size is 2 MB. Source license links are retained in the manifest.

## Search and graph

SQLite FTS5 with BM25 searches documentation, rustdoc comments, source and manifests, first matching all query terms and then broadening the search. Short English API names and terms work best; the small Russian keyword dictionary is not general multilingual search. `context --max-chars` bounds the evidence text; JSON metadata and URLs are additional.

The graph contains documents, lexically identified Rust declarations and topics. Every edge records its file, line, excerpt and exact Git commit URL.

| Relation | Meaning |
|---|---|
| `declares` | A type, function, module, constant or macro declaration was found |
| `module_file` | A simple external module declaration was linked to a file by source layout |
| `use_path_candidate` | A candidate file was found for a literal import path |
| `links_to` | A link to another indexed document was resolved |
| `mentions_symbol` | An unambiguous lexical mention of a symbol name was found |
| `keyword_tag` | Text matched an explicitly defined topic dictionary |

Extraction accounts for nested comments, raw strings and lifetimes so their contents are not mistaken for declarations. It does not execute macros or resolve `cfg`, types, traits, reexports or glob imports. The graph helps locate sources but does not prove runtime calls or API availability. Ambiguous mentions are skipped and counted in the report. Compiler properties require rustc, rust-analyzer and project context.

The full graph is exported to `graph.json`. Bounded `context`, `related` and `path` responses are usually more useful to an agent than loading the whole graph. A vector database or visualizer is not required.

## Updates and diagnostics

Git fetches sources without executing downloaded code. Every file has a SHA-256; updates count additions, modifications and deletions. A complete index and graph are built in a separate directory, SQLite integrity and foreign keys are checked, and the current snapshot is switched atomically. A file lock excludes concurrent writers while the previous snapshot remains readable.

Network failures, missing paths, empty sources and pre-publication build failures preserve the previous database. `rebuild` produces a new snapshot but keeps the actual last network check time. Snapshot history is retained; automatic history cleanup and background scheduling are not configured.

`doctor` reads the active compiler/Cargo, project manifest and lockfile. It compares Rust release/commit and indexed crate versions, distinguishes multiple versions of one crate and flags unverified path/git dependencies. It also considers explicit project MSRV and recorded MSRV of matching indexed dependencies. Workspace-inherited MSRV is flagged for further review. Unindexed packages are listed separately.

A successful `doctor --strict` does not establish compatibility of the entire dependency tree. Feature sets, deployment targets, macros and actual builds require Cargo and tests; diagnostics explicitly report `dependency_compatibility_verified: false`.

## Codex skill

Source: [rust-engineering](skill/rust-engineering/SKILL.md). The installed copy is discovered in the user's Codex skills directory; invoke it explicitly as `$rust-engineering`.

The skill checks sources against `Cargo.lock`, toolchain, MSRV, edition, features and targets, retrieves API evidence, applies ownership and async execution guidance, and verifies behavior. The [server guide](skill/rust-engineering/references/server-engineering.md) covers PvP, task cancellation, overload, transactions and command retries without imposing one stack on every Rust project.

The wrapper locates the application through `installation.json`. Set `RKB_APP_ROOT` after relocating the checkout. For a separate installation, use `python3 rkb.py install-skill --destination /path/to/skills`: the installer protects a copy owned by another application path.

This is a working evidence base and engineering guide, not a guarantee of perfect code. Architecture quality, load behavior and protocol correctness require validation on actual tasks. See the [historical validation results](docs/VALIDATION.md).
