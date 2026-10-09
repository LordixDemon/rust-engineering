# Rust knowledge validation

[English](#english) | [Русский](#russian)

<a id="english"></a>

## English

This is a historical validation record from the original implementation, before this repository was extracted. Versions and corpus sizes below apply to that date, not to a fresh clone. The standalone repository includes its engine and tests locally; build the current database with `python3 rkb.py update`.

Validated on September 12, 2026, in Europe/Kiev; September 11 in UTC.

### Versions and data

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

### Utility and search

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

### Rust application check

An isolated edition 2024 probe crate was created in `.data/verification/server_probe` with Axum 0.8.9, Tokio 1.53.1, Tower 0.5.3 and Thiserror 2.0.20. It models match-state ownership inside a task and command handling through a bounded channel.

Results:

- `cargo fmt --check` — passed;
- `cargo clippy --all-targets --locked -- -D warnings` — passed;
- `cargo test` — **4 tests passed**;
- `doctor --project .data/verification/server_probe --strict` — Rust release/commit and indexed dependency versions matched, with no mismatches.

Tests covered ordered and repeated commands without double application, draining accepted commands after senders close, concurrent duplicates, rejection when the queue is full and an HTTP route without opening a socket. `doctor` lists unindexed transitive dependencies and does not claim full dependency-tree compatibility.

The crate is an API application probe, not a complete game server. Validation ran on macOS arm64; Linux deployment, real networking, databases, load, anti-cheat and a complete PvP protocol were not checked here.

---

<a id="russian"></a>

## Русский

Историческая проверка первоначальной реализации до выделения этого репозитория.
Версии и размеры корпуса ниже относятся к указанной дате, а не к свежему clone.
В самостоятельном репозитории движок и тесты включены локально; текущую базу
создаёт `python3 rkb.py update`.

Проверено 12 сентября 2026 года по времени Europe/Kiev, 11 сентября по UTC.

### Версии и данные

Активны Rust **1.98.1 stable** и Cargo **1.98.1** на `aarch64-apple-darwin`. Commit компилятора — `48a229ceaefd4985c50990b14116b6d856af0985`; он совпадает с индексированным Rust.

Книги и стандартная библиотека привязаны к Rust 1.98.1. Версии основных библиотек в проверенном снимке:

| Crate | Версия |
|---|---|
| Tokio | 1.53.1 |
| Axum | 0.8.9 |
| Serde | 1.0.229 |
| SQLx | 0.9.0 |
| Tracing | 0.1.44 |
| Tower | 0.5.3 |
| Thiserror | 2.0.20 |
| Anyhow | 1.0.104 |

Это версии основного crate каждого источника; соседние пакеты монорепозиториев имеют собственные версии. В манифесте SQLx 0.9.0 указан MSRV 1.94.0.

Успешно выполнены получение всех 14 источников, онлайн-обновление и офлайн-перестроение. Проверенный снимок: `20260911T210954-bf534c79`; источники опрошены примерно в 21:05 UTC, граф построен в 21:10 UTC. Офлайн-перестроение сохранило время сетевой проверки.

| Метрика | Значение |
|---|---:|
| Документы / файлы | 2 922 |
| Поисковые фрагменты | 13 686 |
| Узлы графа | 48 947 |
| Связи с местом в источнике | 102 804 |
| Пропущенные неоднозначные упоминания | 64 778 |

Повторная офлайн-сборка распознала все 2 922 файла как неизменённые и построила новый граф.

### Утилита и поиск

Общий движок проверен командой `python3 -W error::ResourceWarning -m unittest discover -s tests -q` из `flutter-flame-knowledge`: **30 тестов прошли**. Это 19 существующих проверок и 11 проверок поддержки Rust.

Проверены Rust-объявления и ссылки модулей с исходными строками, вложенные комментарии/raw strings/lifetimes, отдельные файлы манифестов, заголовки rustdoc, привязка книг к релизу, выбор stable без prerelease/yanked, сохранение нескольких версий и источников в Cargo.lock, диагностика compiler/MSRV/зависимостей. Общие проверки покрывают обновления, сохранение предыдущего снимка при сбое, блокировку писателя, поиск и установку скилла.

Ruff: **All checks passed**. Скилл проверен системным валидатором skill-creator: **Skill is valid**.

Установленный wrapper использован для получения контекста Tokio с точным commit и строками исходника. После перестроения выполнены пять запросов с ограничением источником:

| Запрос | Результат в первых пяти фрагментах |
|---|---|
| `JoinSet` / tokio | Файл определения `tokio/src/task/join_set.rs`, первое место |
| `CancellationToken` / tokio | `tokio-util/src/sync/cancellation_token.rs`, первое место |
| `Serialize` / serde | `serde_core/src/ser/mod.rs`, второе место |
| `Router` / axum | Руководства по объединению, состоянию и вложенности роутеров |
| `PgPool` / sqlx | Официальные PostgreSQL-примеры и документация тестовых баз |

Это небольшая проверка навигации по известным API, а не полная оценка качества поиска или ответов скилла.

### Применение в Rust

В `.data/verification/server_probe` создан изолированный проверочный crate на edition 2024 с Axum 0.8.9, Tokio 1.53.1, Tower 0.5.3 и Thiserror 2.0.20. Он моделирует владение состоянием матча внутри задачи и обработку команд через ограниченный канал.

Результаты:

- `cargo fmt --check` — успешно;
- `cargo clippy --all-targets --locked -- -D warnings` — успешно;
- `cargo test` — **4 теста прошли**;
- `doctor --project .data/verification/server_probe --strict` — Rust release/commit и версии индексируемых зависимостей совпали, расхождений нет.

Тесты проверяют последовательность и повтор команд без двойного применения, завершение после закрытия отправителей с обработкой принятых команд, конкурентный повтор, отказ при переполнении очереди и HTTP-маршрут без открытия сокета. Неиндексируемые транзитивные зависимости перечислены в `doctor`, совместимость полного дерева он не объявляет проверенной.

Этот crate служит проверкой применения API, а не готовым игровым сервером. Проверки выполнены на macOS arm64; здесь не проверялись Linux-развёртывание, реальные сетевые соединения, база данных, нагрузка, античит или полноценный протокол PvP.
