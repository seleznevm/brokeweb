# Database

PostgreSQL 17 используется без TimescaleDB: стандартного образа достаточно для локального развёртывания. SQLAlchemy 2, Alembic. На старте API применяет `alembic upgrade head`; unit/integration tests используют изолированные SQLite-БД; отдельные Docker-проверки используют PostgreSQL, включая миграции, archive/restore и native partitions.

| Таблица | Данные |
|---|---|
| instruments | уникальные exchange/symbol, status, tick size и metadata |
| market_bars | ключ exchange/symbol/timeframe/start; OHLCV, confirmed, turnover |
| setup_current | текущее состояние и checkpoint по exchange/symbol/TF |
| setup_snapshots | typed quality/price/risk columns + полный JSONB payload |
| setup_events | FSM/action/path/zone/generation transitions и сигналы |
| signals | dedupe key, original full snapshot, parameter version |
| parameter_sets | content hash, values, единственная active version |
| alert_rules / alert_rule_versions | текущая версия и история редактирования |
| alert_rule_state | persistent crossing/frequency/cooldown state |
| alert_deliveries | transactional outbox, dedupe, попытки и результат |
| research_samples | frozen source UDT и entry snapshot, completed outcome |
| parity_reference / parity_results | versioned imported comparison artifacts |
| service_health | heartbeats и snapshot interval |
| archive_batches | SHA-256, путь, timestamps/count и статус restore архивов snapshots/events |

Current, snapshot, transition, signal, research и alert enqueue записываются одной транзакцией. PostgreSQL row locks сериализуют rule state; activation параметров использует advisory lock. Основные выборки имеют индексы по symbol/TF/time. Retention-сервис переносит старые snapshots/events в проверенные lossless архивы, сохраняя каталог в PostgreSQL. По умолчанию preview; сроки задаются отдельно. Миграция 0005 переводит snapshots/events в native monthly RANGE partitions; DEFAULT и подготовка будущих месяцев сохраняют непрерывность записи. См. [partitioning.md](partitioning.md). Подробности: [retention.md](retention.md).

`payload.metrics` хранит исходные Pine globals, `source_locals` — callsite intermediates. ResearchSignal/ResearchStats сохраняют исходные поля `signalBar`, `bucket`, `entry`, `atr0`, `t1`, `sl`, `mfe`, `mae`, `t1Bar`, `slBar`. Завершённый sample перехватывается при исходном `array.remove`, а не вычисляется заново ORM. При совпадении T1 и SL в одном баре Pine сохраняет ambiguous outcome, порядок тиков не выдумывается.

Historical replay сохраняет snapshots каждого закрытия. Тяжёлый checkpoint записывается через 50 исторических баров и на realtime updates; промежуток после последнего checkpoint повторно проигрывается. Прежде уже сохранённые исторические снимки пропускаются при восстановлении. Checkpoint содержит source hash, version, parameter hash, series buffers, vars/varip и request contexts. Версионный mismatch требует replay.

Миграция `0003` добавляет `checkpoint_blob` (BYTEA): versioned JSON UTF-8 + lossless zlib level 1. Это уменьшает запись/разбор больших history buffers в PostgreSQL без округления чисел и пропуска executions. `checkpoint` JSONB остаётся для чтения старых записей; следующая успешная запись переносит state в сжатый формат той же транзакцией. Оба поля deferred: списки setups не загружают buffers. Downgrade сначала восстанавливает JSON. Повреждённый blob вызывает ошибку; состояние не обнуляется молча.

Историческая аналитика доступна через `/api/research`, `/history`, SQL по typed columns и JSONB. ML, performance backtest сделок и биржевые исполнения не моделируются. Research MFE/MAE — движение после signal close, а не PnL торгового счёта.
