# Rust server decisions

[English](#english) | [Русский](#russian)

<a id="english"></a>

## English

Use the sections relevant to the requested change. Verify API details against the resolved crate versions and feature gates.

### Ownership, tasks and shutdown

- Give long-lived state and resources a clear owner. Choose shared state, message passing or per-match tasks according to access patterns, contention, ordering and recovery requirements.
- Scope ordinary mutex guards around short synchronous operations. If serialization must span an await, justify the choice and use a suitable async design; changing mutex types alone does not solve contention or deadlocks.
- Blocking I/O and CPU-heavy work can stall the async runtime. Use appropriate bounded offloading where needed; unrestricted task creation or unbounded queues can merely move the overload elsewhere.
- Define queue capacity, backpressure and overload behavior. Check whether cancellation of `send`, `select!`, a database call or a partially completed request loses work or duplicates it.
- Own task handles, cancellation and shutdown explicitly. A dropped handle can detach work; a closed channel is not automatically a graceful drain. Verify the requested drain/abort behavior and bound shutdown time.

### Authoritative PvP state

- Model legal moves and match progression separately from transport and persistence. For grid games, use explicit board coordinates/rules and a defined clock/randomness model when prediction or replay requires determinism.
- Treat client commands as proposals. Validate ordering, player ownership and rule preconditions before mutating authoritative state. Define duplicate, stale and out-of-order command behavior.
- Reconnect should resume from a known sequence/state. Only expose random information that the rules intend the client to know; a shared seed is not itself a complete synchronization protocol.
- Award rating, inventory and rewards through durable, idempotent operations. A repeated result message must not award twice. Keep cosmetic client animation independent of committed game state.

### Persistence and boundaries

- Choose transaction boundaries from invariants such as atomic reward grants, balances or match finalization. Check isolation/concurrent updates and retries rather than assuming a transaction eliminates every race.
- With SQLx, understand when query macros need a live database or prepared offline metadata. A host-only compile does not verify a production schema or migration.
- Distinguish domain errors, retryable infrastructure failures and unexpected faults. Preserve useful error chains internally; expose appropriate client errors without leaking secrets. Use the project's established `thiserror`/`anyhow` or other conventions rather than imposing a library.
- Apply timeouts and request/body limits where the protocol calls for them. Record useful tracing spans and metrics for queueing, retries and match operations without dumping credentials or unnecessary player data.

### Version and validation traps

- `Cargo.lock` may contain several versions of the same crate. Identify the actual direct/workspace dependency and its source before upgrading. Path/git dependencies are not established by a matching crates.io version number.
- MSRV, edition, features and targets are independent compatibility dimensions. Preserve the intended feature matrix and minimum compiler contract.
- For service changes, useful tests cover invalid/duplicate commands, channel closure and overload, cancellation, shutdown, transaction rollback and retry behavior. Use controlled clocks/inputs instead of timing-sensitive sleeps where possible.
- Linux deployment, networking behavior and database integration need their own validation when the task reaches those boundaries. Report host-only checks as host-only checks.

### Starting queries

```text
ownership borrowing lifetimes
Send Sync Pin
JoinSet shutdown
bounded channel backpressure
select cancellation safety
spawn_blocking
with_graceful_shutdown
Router State WebSocketUpgrade
PgPool transaction
tracing instrument
rust-version resolver features
unsafe invariants
```

---

<a id="russian"></a>

## Русский

Читайте разделы, относящиеся к изменению. Проверяйте API по разрешённым версиям crate и feature gates.

### Владение, задачи и завершение

- Долгоживущее состояние и ресурсы должны иметь ясного владельца. Выбирайте общее состояние, обмен сообщениями или задачи на матч по характеру доступа, конкуренции, порядку операций и требованиям восстановления.
- Ограничивайте обычные mutex guards короткими синхронными операциями. Если сериализация должна охватывать await, обоснуйте это и выберите подходящую async-схему; замена типа mutex сама по себе не устраняет конкуренцию или deadlock.
- Блокирующий I/O и тяжёлые вычисления могут остановить async runtime. При необходимости используйте подходящий ограниченный перенос работы; неограниченные задачи или очереди могут лишь перенести перегрузку в другое место.
- Определите ёмкость очередей, backpressure и поведение при перегрузке. Проверяйте, приводит ли отмена `send`, `select!`, запроса БД или частично выполненного запроса к потере или повтору работы.
- Явно управляйте handles задач, отменой и завершением. Удаление handle может оставить задачу без владельца; закрытие канала не гарантирует корректное завершение принятой работы. Проверяйте требуемое поведение drain/abort и ограничивайте время завершения.

### Авторитетное состояние PvP

- Отделяйте допустимые ходы и развитие матча от транспорта и хранения. Для игр на поле задавайте явные координаты/правила и модель времени/случайности, если предсказание или повтор требуют детерминизма.
- Рассматривайте команды клиента как предложения. До изменения авторитетного состояния проверяйте порядок, принадлежность игроку и условия правил. Определите обработку дубликатов, устаревших и пришедших не по порядку команд.
- При переподключении восстанавливайтесь от известной последовательности/состояния. Раскрывайте только случайную информацию, которую клиент вправе знать по правилам; общий seed сам по себе не является полным протоколом синхронизации.
- Начисляйте рейтинг, предметы и награды через сохраняемые идемпотентные операции. Повтор результата не должен начислять награду второй раз. Косметическая анимация клиента должна быть независима от зафиксированного состояния игры.

### Хранение и границы

- Выбирайте границы транзакций по инвариантам: атомарность награды, баланс, завершение матча. Проверяйте изоляцию, конкурентные обновления и повторы; наличие транзакции не устраняет автоматически каждую гонку.
- Для SQLx выясните, когда query macros требуют живую БД или подготовленные offline metadata. Сборка только на машине разработчика не проверяет production-схему или миграцию.
- Различайте доменные ошибки, временные сбои инфраструктуры и неожиданные неисправности. Сохраняйте полезные цепочки ошибок внутри, а клиенту показывайте подходящую ошибку без раскрытия секретов. Используйте принятые `thiserror`/`anyhow` или другие соглашения проекта вместо навязывания библиотеки.
- Применяйте таймауты и ограничения запросов/тел там, где это требует протокол. Записывайте полезные tracing spans и метрики очередей, повторов и матчей без вывода credentials или ненужных данных игроков.

### Ловушки версий и проверок

- `Cargo.lock` может содержать несколько версий одного crate. До обновления определите фактическую прямую/workspace-зависимость и её источник. Совпадение номера версии crates.io не подтверждает path/git-зависимость.
- MSRV, edition, features и targets — независимые измерения совместимости. Сохраняйте нужную матрицу features и контракт минимального компилятора.
- Для серверных изменений полезны тесты неверных/повторных команд, закрытия каналов и перегрузки, отмены, завершения, отката транзакций и повторов. По возможности используйте управляемое время/входные данные вместо нестабильных задержек.
- Развёртывание Linux, сеть и интеграция БД требуют собственных проверок, когда задача затрагивает эти границы. Проверки только на машине разработчика так и обозначайте.

### Начальные запросы

```text
ownership borrowing lifetimes
Send Sync Pin
JoinSet shutdown
bounded channel backpressure
select cancellation safety
spawn_blocking
with_graceful_shutdown
Router State WebSocketUpgrade
PgPool transaction
tracing instrument
rust-version resolver features
unsafe invariants
```
