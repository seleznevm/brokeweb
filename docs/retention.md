# Архивирование истории

Сервис `retention` обслуживает месячные разделы и ограничивает объём рабочих таблиц `setup_snapshots` и `setup_events` переносом старых строк в проверенные gzip JSONL-архивы. Исходный analytic payload и typed columns сохраняются полностью. По умолчанию включён **preview**: подсчёт без записи архивов и удаления строк. Период хранения остаётся выбором владельца данных.

Не затрагиваются current state, checkpoints, market bars (в том числе невосстановимые из REST 30S), signals/dedupe, Research samples, параметры, правила/доставки и TradingView reference. Поэтому это не автоматический предел размера всей БД и не ограничение суммарного объёма архива на диске.

## Настройки

| Переменная | Default | Назначение |
|---|---|---|
| `RETENTION_MODE` | `preview` | `preview` считает; `archive` архивирует и освобождает рабочие таблицы |
| `SNAPSHOT_RETENTION_DAYS` | `30` | Возраст snapshots; `0` отключает эту таблицу |
| `EVENT_RETENTION_DAYS` | `90` | Возраст событий; `0` отключает эту таблицу |
| `RETENTION_BATCH_ROWS` | `250` | Строк в транзакции, максимум 2000 |
| `RETENTION_MAX_BATCHES` | `8` | Максимум batch на таблицу за цикл, максимум 100 |
| `RETENTION_INTERVAL_SEC` | `3600` | Интервал между циклами, минимум 60 секунд |
| `ARCHIVE_DIR` | `data/archives` | Путь для запуска Python на хосте; Docker использует `/archives` |

В Compose `/archives` примонтирован к `C:\dev\brokeweb\data\archives`. Архивы исключены из Git и Docker build context. Их нужно включить в резервное копирование. Параметры retention относятся к эксплуатации БД и не входят в 323 Pine inputs или parameter hash.

## Preview и запуск

```bash
docker compose exec retention python -m backend.retention preview
curl http://localhost:8080/api/storage
docker compose logs --tail=100 retention
```

Preview показывает UTC cutoffs, количество подходящих строк и диапазоны timestamp. Условие строгое: `event_time < cutoff`; строка ровно на границе остаётся в БД. Все timestamp — milliseconds UTC. В `/api/health` есть отдельный service heartbeat, mode, policy, running, error и last_result. `/api/storage?limit=50` возвращает последние записи каталога архивов; максимум 500.

После выбора сроков хранения установить `RETENTION_MODE=archive` и нужные days в `.env`, затем:

```bash
docker compose up -d retention
```

Цикл ограничен числом batch, поэтому большой backlog освобождается постепенно. Отключение одного срока значением `0` сохраняет соответствующую таблицу. Для возврата в preview изменить `.env` и пересоздать только retention. В локальной проверке реальные рабочие данные не архивировались: полный archive/restore прогон выполнен в отдельной временной схеме.

## Гарантии и сбои

1. Worker выбирает ограниченную группу старых строк в порядке `(event_time, id)`. PostgreSQL advisory lock исключает параллельное архивирование/restore; `FOR UPDATE SKIP LOCKED` не ждёт занятые строки. Это пакетная обработка очереди, а не согласованный снимок всей БД. Семантика: [PostgreSQL locking clause](https://www.postgresql.org/docs/17/sql-select.html#SQL-FOR-UPDATE-SHARE).
2. Полные строки и header записываются во временный gzip-файл. Header содержит format/version, table/columns, batch ID, cutoff, count и creation time. Выполняются fsync, SHA-256, повторное чтение и сравнение значений.
3. Файл получает окончательное имя через atomic rename; metadata каталогов синхронизируется. Если filesystem не поддерживает нужные durable operations, цикл завершается ошибкой до удаления строк.
4. Запись в `archive_batches` и удаление выбранных строк выполняются одной транзакцией PostgreSQL после успешной проверки файла. При сбое БД строки остаются; окончательный файл может стать orphan. Он не удаляется автоматически. Повторный цикл безопасно создаёт новый batch.

Сбой после commit оставляет и файл, и запись каталога. Потеря диска после commit требует backup: локальная проверка checksum не заменяет резервную копию. Архивы автоматически не удаляются. Сигналы/dedupe остаются в БД, поэтому очистка snapshots/events не открывает повторную доставку старых сигналов.

## Восстановление

Сначала перевести сервис в preview, чтобы следующая автоматическая итерация не архивировала восстановленные старые строки снова. Найти `id` в `/api/storage` или `archive_batches`, затем:

```bash
docker compose exec retention python -m backend.retention restore --archive-id <UUID>
```

Restore проверяет checksum, header/catalogue identity, схему и количество строк. Уже имеющиеся идентичные строки пропускаются. Любое несовпадение по тому же ID отменяет весь batch; перезаписи чужих данных нет. Восстанавливаются прежние IDs; sequence PostgreSQL корректируется под краткой блокировкой insert/delete таблицы, с lock timeout 2 секунды. Это ручная ограниченная операция, которую при занятой таблице можно повторить.

Restore работает с каталогом этой БД; для переноса на другой сервер требуется восстановить PostgreSQL backup с каталогом/parameter sets и соответствующую папку архивов. Самостоятельный импорт произвольного внешнего JSONL не выполняется. При изменении схемы header validation требует явного адаптера формата вместо молчаливой потери полей.

После archive обычные `/history` и `/events` показывают только строки, оставшиеся в рабочей БД; архив не подмешивается в выдачу незаметно. Для старого интервала выполнить restore либо читать lossless JSONL для офлайн-анализа. Research и detector signals доступны независимо от архивирования.

## Миграция и backup

Alembic `0004` создаёт `archive_batches` и индексы `(event_time,id)` для обеих таблиц. На PostgreSQL существующие таблицы индексируются `CONCURRENTLY`, без длительной блокировки writers; это требует отдельного autocommit block. См. [PostgreSQL concurrent index builds](https://www.postgresql.org/docs/17/sql-createindex.html#SQL-CREATEINDEX-CONCURRENTLY). Миграция 0005 далее переводит таблицы в native monthly partitions; детали и окно обслуживания: [partitioning.md](partitioning.md).

Downgrade 0004 останавливается при непустом каталоге, чтобы не потерять указатели на архивную историю. Это проверка сохранности, а не удаление файлов. Не очищать каталог для обхода проверки без отдельного сохранённого экспорта/backup.

Backup должен сохранять БД вместе с `archive_batches` и все referenced `.jsonl.gz`. Так как завершённые архивы неизменяемы и не удаляются автоматически, можно сначала получить согласованный `pg_dump`, затем скопировать папку архивов. Не считать `.partial` завершёнными файлами; orphan-файлы после rollback могут присутствовать сверх каталога.

Удаление строк освобождает место для повторного использования PostgreSQL после vacuum, но не обещает немедленного уменьшения размера файлов ОС. Автоматический `VACUUM FULL` не запускается: он блокирует таблицу. Для длительной эксплуатации остаются обязательными измерение backlog/IO/WAL, sizing архивного диска, backup/restore acceptance и full-universe capacity.

## Проверки

`tests/integration/test_retention.py`: cutoff, preview, bounded batches, точный restore, idempotence, disk/DB failure, corruption/conflicts/path validation, сохранность checkpoints/signals/parameters.

```bash
python3 -m pytest tests/integration/test_retention.py -q
docker compose run --rm --no-deps retention python -m tools.retention_smoke
```

PostgreSQL smoke использует временную схему и временный каталог внутри archive mount, проверяет upgrade со старой схемы, valid concurrent indexes, конкурирующую блокировку, archive/restore, sequence, corruption и защиту catalogue при downgrade. В конце временные записи/файлы удаляются; рабочая история не затрагивается. Evidence: `artifacts/local/retention-smoke.json`.

## Проверка резервной копии с непустым архивом (2026-09-27)

Добавлен воспроизводимый isolated DR smoke:

```bash
python3 -m tools.disaster_recovery_smoke --output artifacts/local/disaster-recovery.json
```

Запускать с хоста в корне проекта при работающих Compose `api` и `postgres`. Используется локальная роль `brokeweb` с правом создания временных БД. Команда создаёт две базы с уникальными именами `brokeweb_dr_*`; рабочая БД, правила и archive mount не изменяются. Подготавливает тестовую историю и непустой архив, делает настоящий `pg_dump -Fc`, восстанавливает через `pg_restore --exit-on-error`, переносит архивные файлы через отдельный backup-каталог и удаляет исходную тестовую базу/архивы перед проверкой целевой. Временные базы и файлы убираются после прогона.

Результат: **16 таблиц** совпали по количеству строк и SHA-256 канонических значений; **8 archive batches / 16 строк** восстановлены в точности. Checkpoint, idempotence и sequence после restore проверены. [Отчёт](../reports/disaster-recovery-validation.json). Это функциональная проверка всего пути БД + archive disk на маленьком fixture. Production-sized RTO/RPO, скорость backup, sizing/WAL и доступное место остаются отдельными измерениями.
