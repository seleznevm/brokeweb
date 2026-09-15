# Месячные разделы PostgreSQL

Alembic `0005` переводит `setup_snapshots` и `setup_events` в native RANGE partitioning по `event_time` (UTC milliseconds). Остальные таблицы не изменяются. По одному разделу на календарный месяц, границы `[начало месяца, начало следующего)`. DEFAULT-раздел принимает историю вне подготовленного диапазона.

## Сохранность и миграция

Миграция копирует SQL-строки без Python-пересериализации, проверяет двусторонний `EXCEPT ALL`, сохраняет исходные IDs, общий sequence, foreign keys к параметрам и обычные индексы. После проверки таблицы меняются именами в той же транзакции. Sequence получает нового владельца до удаления старой таблицы. Ошибка откатывает весь перенос. Downgrade выполняет обратную проверяемую операцию; данные и IDs сохраняются.

Миграция требует окна обслуживания: `ACCESS EXCLUSIVE` на двух таблицах, lock timeout 5 s, statement timeout 120 s на statement. Перед обновлением заполненного стенда сохранить `pg_dump` и остановить engine/retention/notifier. Затем применить миграцию API, проверить counts/checksums и снова поднять сервисы. Для больших production-таблиц это не обещание zero-downtime migration: если копирование не укладывается в окно, нужен отдельный план переноса.

Custom unique indexes, table grants, RLS и пользовательские triggers требуют отдельного рассмотрения: автоматическая конвертация отказывается молча терять их смысл. Зависимости внешних views/FK также не удаляются через CASCADE.

Физический primary key PostgreSQL — `(id,event_time)`, поскольку partition key должен входить в unique/primary constraint. Один общий sequence сохраняет логическую ORM-идентичность `id`; API и archive IDs не меняются. SQLAlchemy metadata описывает логический ключ и обычные SQLite-таблицы для тестов; PostgreSQL physical schema создаётся через Alembic. Прямой внешний SQL с ручным повторением ID между месяцами не поддерживается. Restore дополнительно проверяет существующие IDs и отказывается перезаписывать несовпадающую строку.

## Автоматическое обслуживание

Сервис `retention` обслуживает разделы перед очередным циклом archive/preview. `PARTITION_MONTHS_AHEAD=3` создаёт разделы на текущий месяц, предыдущий и три следующих. Также обрабатываются месяцы, уже попавшие в DEFAULT, не более 12 таких месяцев на цикл. Старые пустые разделы автоматически не удаляются.

Создание отсутствующего раздела использует ограниченную транзакцию и краткую блокировку маршрутизации записей. `PARTITION_MAX_MOVE_ROWS=10000` ограничивает число DEFAULT-строк, переносимых в месяц автоматически. Если строк больше, статус DEFERRED; они остаются доступны через родительскую таблицу. При занятости таблицы/DDL timeout — BUSY, повтор при следующем цикле. DEFAULT продолжает принимать данные.

DEFAULT-строки переносятся через `DELETE … RETURNING` в подготовленную таблицу, после чего она присоединяется как partition. Перенос и ATTACH атомарны. Сбой после DELETE откатывает строки и DDL. Lock timeout 2 s, statement timeout 30 s. Создание заранее сокращает необходимость таких операций на активной границе месяца, но не отменяет проверку нагрузки.

```bash
docker compose exec retention python -m backend.partitions list
docker compose exec retention python -m backend.partitions ensure
curl http://localhost:8080/api/storage
```

Список разделов и границ возвращается в `partitioning` у `/api/storage`, результат последнего обслуживания — в health retention. Логическая история при `RETENTION_MODE=preview` сохраняется целиком: разделение не является очисткой данных. Для большого DEFAULT backlog можно в согласованное окно выполнить `ensure --max-move-rows <лимит>`; обычный автоматический предел не повышается незаметно.

## Archive/restore и запросы

Архивирование работает через родительские таблицы и сохраняет те же поля/IDs; каталог архивов не зависит от текущего физического раздела. Restore вставляет через родителя, поэтому PostgreSQL направляет строку в соответствующий месяц или DEFAULT. Partition maintenance и archive/restore используют общий advisory lock.

Фильтры `/history` и `/events` по `event_time` позволяют PostgreSQL исключать не относящиеся к периоду разделы. Это проверено через `EXPLAIN (FORMAT JSON)`. Partition pruning и ограничение primary key описаны в [PostgreSQL 17 Table Partitioning](https://www.postgresql.org/docs/17/ddl-partitioning.html). Само наличие разделов не подтверждает full-universe throughput или числовое совпадение с Pine.

## Проверки

`tests/unit/test_partitions.py` проверяет UTC-календарь, високосный февраль, переход года и ограничения. `tools.partition_smoke` работает в отдельной временной PostgreSQL-схеме:

- upgrade → downgrade → upgrade с точным сравнением строк/IDs;
- события на обеих сторонах границы месяца;
- сохранность FK и sequence, partition pruning;
- DEFAULT fallback, bounded transfer, повторное обслуживание;
- откат после ошибки ATTACH и занятой таблицы;
- archive/restore через родителя, неизменность checkpoint.

```bash
docker compose run --rm --no-deps retention python -m tools.partition_smoke
```

Evidence: `artifacts/local/partition-smoke.json`. Проверка временная: рабочая история не удаляется. Full-universe нагрузка, sizing WAL/IO и внешняя TradingView parity остаются отдельными acceptance gates.
