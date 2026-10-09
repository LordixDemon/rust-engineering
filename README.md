# Rust Engineering

[English](#english) | [Русский](#russian)

<a id="english"></a>

## English

A local, updatable knowledge base of official Rust documentation and crate sources, with search, an evidence graph and the `$rust-engineering` Codex skill. The skill selects APIs against actual project versions and verifies results with the compiler, Clippy and tests. Its server guidance covers match-state ownership, command ordering, duplicates, bounded queues, task shutdown and reward transactions.

This repository is standalone. The engine is bundled in `flutter_kb/` under its historical Python package name; a sibling Flutter project is not required. Requirements: Python 3.11+ with its standard library and SQLite FTS5, Git, Linux or macOS, and access to public GitHub, crates.io and static.rust-lang.org. `doctor` also requires an installed Rust toolchain. No API keys, Python runtime dependencies or database server are needed.

### Install from GitHub

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

### Commands

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

### Sources and versions

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

### Search and graph

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

### Updates and diagnostics

Git fetches sources without executing downloaded code. Every file has a SHA-256; updates count additions, modifications and deletions. A complete index and graph are built in a separate directory, SQLite integrity and foreign keys are checked, and the current snapshot is switched atomically. A file lock excludes concurrent writers while the previous snapshot remains readable.

Network failures, missing paths, empty sources and pre-publication build failures preserve the previous database. `rebuild` produces a new snapshot but keeps the actual last network check time. Snapshot history is retained; automatic history cleanup and background scheduling are not configured.

`doctor` reads the active compiler/Cargo, project manifest and lockfile. It compares Rust release/commit and indexed crate versions, distinguishes multiple versions of one crate and flags unverified path/git dependencies. It also considers explicit project MSRV and recorded MSRV of matching indexed dependencies. Workspace-inherited MSRV is flagged for further review. Unindexed packages are listed separately.

A successful `doctor --strict` does not establish compatibility of the entire dependency tree. Feature sets, deployment targets, macros and actual builds require Cargo and tests; diagnostics explicitly report `dependency_compatibility_verified: false`.

### Codex skill

Source: [rust-engineering](skill/rust-engineering/SKILL.md). The installed copy is discovered in the user's Codex skills directory; invoke it explicitly as `$rust-engineering`.

The skill checks sources against `Cargo.lock`, toolchain, MSRV, edition, features and targets, retrieves API evidence, applies ownership and async execution guidance, and verifies behavior. The [server guide](skill/rust-engineering/references/server-engineering.md) covers PvP, task cancellation, overload, transactions and command retries without imposing one stack on every Rust project.

The wrapper locates the application through `installation.json`. Set `RKB_APP_ROOT` after relocating the checkout. For a separate installation, use `python3 rkb.py install-skill --destination /path/to/skills`: the installer protects a copy owned by another application path.

This is a working evidence base and engineering guide, not a guarantee of perfect code. Architecture quality, load behavior and protocol correctness require validation on actual tasks. See the [historical validation results](docs/VALIDATION.md).

---

<a id="russian"></a>

## Русский

Локальная обновляемая база официальной документации и исходников Rust с поиском, графом связей и скиллом `$rust-engineering`. Скилл помогает выбирать API по фактическим версиям проекта и проверять результат компилятором, Clippy и тестами. Для будущего PvP-сервера отдельно описаны владение состоянием матча, порядок команд, повторы, ограниченные очереди, завершение задач и транзакции наград.

Самостоятельный репозиторий: движок включён в `flutter_kb/` под историческим именем Python-пакета. Соседний Flutter-проект не требуется. Требования: Python 3.11+ со стандартной библиотекой и SQLite FTS5, Git, Linux или macOS, доступ к публичным GitHub, crates.io и static.rust-lang.org. Для `doctor` нужен установленный Rust. API-ключи, Python-зависимости и отдельный сервер базы данных для работы утилиты не нужны.

### Установка из GitHub

```bash
git clone https://github.com/LordixDemon/rust-engineering.git
cd rust-engineering
python3 rkb.py install-skill
python3 rkb.py update
```

Исходник скилла находится в `skill/rust-engineering/`. Его wrapper требует
этот репозиторий: установка записывает путь к нему в локальный `installation.json`.
Кеши `.data/`, полученные официальные исходники и локальные пути не публикуются
в Git; `update` создаёт собственную базу. Проверки без сети:

```bash
python3 -W error::ResourceWarning -m unittest discover -s tests -q
```

### Команды

Запускать из папки `rust-engineering`:

```bash
# Проверить актуальные релизы, получить исходники, собрать новую базу и граф
python3 rkb.py update

# Обновить, если последний опрос источников старше суток
python3 rkb.py update --if-older 24

# Полностью перестроить индекс и граф из кеша, без сети
python3 rkb.py rebuild

# Проверить покрытие, версии и соответствие окружению проекта
python3 rkb.py status
python3 rkb.py doctor --project /path/to/rust-project --strict

# Получить небольшой контекст с точными источниками
python3 rkb.py context 'bounded channel backpressure' --source tokio --max-chars 12000
python3 rkb.py search 'with_graceful_shutdown' --source axum
python3 rkb.py related JoinSet --source tokio --limit 20
python3 rkb.py read 'tokio:tokio/src/task/join_set.rs' --start 1 --lines 100

# Показать пути к графу, манифесту и отчёту
python3 rkb.py graph

# Установить или обновить принадлежащую этому приложению копию скилла
python3 rkb.py install-skill
```

Вывод команд — JSON; ход обновления выводится в stderr. Общие параметры `--data-dir` и `--config` ставятся перед подкомандой. Для другого хранилища можно использовать `RKB_DATA_DIR`. По умолчанию данные находятся в `.data/` этой папки и не смешиваются с Flutter-базой.

Для прямого запроса «самые последние версии» нужен обычный `update`: `--if-older` доверяет достаточно свежему кешу. Обновление базы само по себе не запускает `rustup update`, не меняет `Cargo.toml` или `Cargo.lock`. Обновление окружения и зависимостей выполняется отдельно в рамках задачи с проверкой миграций.

### Источники и версии

Точный состав задаёт [sources.json](sources.json); фактические версии и commit сохраняются в манифесте каждого снимка.

| Источник | Что индексируется | Привязка |
|---|---|---|
| Rust | `core`, `alloc`, `std`, Rustc Book | Точный тег актуального stable Rust |
| The Rust Programming Language | Исходники книги | Commit подмодуля из этого релиза Rust |
| Rust Reference | Справочник языка | Commit подмодуля из этого релиза Rust |
| Cargo Book | Руководство Cargo | Commit Cargo, поставляемого с этим Rust |
| Edition Guide | Руководство по редакциям языка | Commit подмодуля из этого релиза Rust |
| Rustonomicon | Владение, unsafe и низкоуровневые инварианты | Commit подмодуля из этого релиза Rust |
| Tokio | Ядро, примеры, `tokio-util` | Релиз основного crate `tokio` |
| Axum | API, встроенная документация и примеры | Релиз `axum` |
| Serde | `serde`, `serde_core`, derive и README | Релиз `serde` |
| SQLx | Основной crate, core, PostgreSQL, macros и примеры | Релиз `sqlx` |
| Tracing | Основной crate, subscriber, attributes и примеры | Релиз `tracing` |
| Tower | `tower`, `tower-service` и README | Релиз `tower` |
| Thiserror | Основной crate, derive и README | Релиз `thiserror` |
| Anyhow | Исходники и README | Релиз `anyhow` |

Версия stable Rust определяется один раз за обновление по официальному манифесту канала. Книги берутся из соответствующих подмодулей релиза, чтобы не смешивать их с более новыми ветками разработки. Для библиотек выбирается последний опубликованный стабильный релиз, не помеченный `yanked`; затем проверяются точный Git tag, имя и версия основного crate в `Cargo.toml`. Если тег отсутствует или манифест расходится с релизом, обновление завершается ошибкой, сохраняя прежний снимок.

У соседних crate в монорепозитории самостоятельные версии. Например, наличие `tokio-util` в снимке Tokio не означает равенство их номеров или отдельную проверку последнего релиза `tokio-util`. Читайте его манифест и сравнивайте с проектом. Аналогично исходники std могут содержать unstable- и target-specific API; версия снимка не делает каждый символ доступным на stable для любой платформы.

Корпус ограничен настроенными путями и текстовыми форматами. Он не включает весь crates.io, все материалы о Rust, полный компилятор, все SQLx-драйверы, обсуждения issues, видео или полный rust-analyzer. Неподдерживаемые форматы, слишком большие файлы и другие пропуски учитываются в отчёте. Максимальный размер файла по умолчанию — 2 МБ. Ссылки на лицензии источников сохранены в манифесте.

### Поиск и граф

SQLite FTS5 с BM25 ищет по документации, rustdoc-комментариям, исходникам и манифестам. Сначала используются все слова запроса, затем более широкий поиск. Лучше работают короткие английские API и термины; небольшой словарь русских ключевых слов не является полноценным многоязычным поиском. `context --max-chars` ограничивает текст извлечённых доказательств; метаданные JSON и URL добавляются сверх него.

Граф содержит документы, лексически найденные объявления Rust и темы. Каждая связь сохраняет файл, строку, выдержку и URL с точным Git commit.

| Связь | Что она означает |
|---|---|
| `declares` | В файле найдено объявление типа, функции, модуля, константы или макроса |
| `module_file` | Простое объявление внешнего модуля связано с файлом по структуре исходников |
| `use_path_candidate` | Для буквального пути импорта найден файл-кандидат |
| `links_to` | Разрешена ссылка на другой индексированный документ |
| `mentions_symbol` | Найдено однозначное по имени лексическое упоминание символа |
| `keyword_tag` | Текст совпал с явно заданным тематическим словарём |

Извлечение учитывает вложенные комментарии, raw strings и lifetimes, чтобы не принимать текст внутри них за объявления. Оно не выполняет макросы, `cfg`, разрешение типов, трейтов, reexports и glob-импортов. Поэтому граф помогает найти исходник, но не доказывает вызов функции или доступность API. Неоднозначные упоминания пропускаются и считаются в отчёте. Для компиляторных свойств нужны rustc, rust-analyzer и контекст проекта.

Полный граф экспортируется в `graph.json`. Для работы агента обычно удобнее небольшие ответы `context`, `related` и `path`, а не загрузка всего графа. Векторная база и визуализатор для этого не обязательны.

### Обновление и диагностика

Git получает исходники без запуска скачанного кода. Каждый файл имеет SHA-256; обновление считает добавления, изменения и удаления. В отдельной папке полностью строятся индекс и граф, проверяются SQLite и внешние ключи, затем атомарно переключается текущий снимок. Два одновременных писателя исключены файловой блокировкой; чтение предыдущего снимка остаётся доступным.

Ошибка сети, отсутствующий путь, пустой источник или ошибка сборки до публикации сохраняют прежнюю базу. `rebuild` строит новый снимок, сохраняя фактическое время последней сетевой проверки. История снимков сохраняется; автоматическое удаление истории и фоновое расписание не настроены.

`doctor` читает активный compiler/Cargo, манифест и lockfile проекта. Он сравнивает Rust release/commit и версии индексируемых crate, различает несколько версий одного crate и отмечает непроверенные path/git-зависимости. Он также учитывает явно заданный MSRV и записанный MSRV совпавших индексируемых зависимостей. Наследование MSRV от workspace помечается для дополнительной проверки. Неиндексируемые пакеты выводятся отдельно.

Успешный `doctor --strict` не подтверждает совместимость всего дерева зависимостей. Наборы features, целевая платформа, макросы и фактическая сборка требуют Cargo и тестов; в результате диагностики это отражено как `dependency_compatibility_verified: false`.

### Скилл

Исходник: [rust-engineering](skill/rust-engineering/SKILL.md#russian). Установленная копия обнаруживается в пользовательской папке навыков Codex; явно использовать её можно через `$rust-engineering`.

Скилл сопоставляет источники с `Cargo.lock`, toolchain, MSRV, edition, features и targets; извлекает подтверждение API; применяет правила владения и асинхронного исполнения; проверяет фактическое поведение. [Серверная памятка](skill/rust-engineering/references/server-engineering.md#russian) описывает решения для PvP, отмены задач, перегрузки, транзакций и повторов команд. Она не навязывает всем Rust-проектам один стек.

Wrapper находит приложение через `installation.json`. После переноса репозитория задайте `RKB_APP_ROOT`. Для параллельной установки используйте `python3 rkb.py install-skill --destination /path/to/skills`: установщик не перезаписывает копию, принадлежащую другому пути приложения.

Это рабочая база доказательств и инженерных правил, а не гарантия идеального кода. Качество архитектуры, нагрузку и корректность игрового протокола нужно подтверждать на конкретных задачах. Фактически выполненные проверки: [VALIDATION.md](docs/VALIDATION.md#russian).
