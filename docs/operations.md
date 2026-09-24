# Operations

## Нормальный запуск

Порядок: Postgres/Redis health → migrations/API → engine/notifier/frontend. Engine загружает inputs, получает весь universe и Binance contexts, загружает историю каждого выбранного контракта, восстанавливает checkpoint либо выполняет replay, затем подключает realtime. Docker healthcheck означает живой процесс; `/api/health` отдельно показывает RECOVERING/DEGRADED/HEALTHY расчётов.

Текущий стенд настроен на полный universe (`MAX_SYMBOLS=0`, TF30). Поле `selected` в `/api/health` — выбранные для shard, `initialized` — закончившие bootstrap, `universe` — все доступные. Capacity для сотен инструментов ещё не принята. Не разворачивать в публичный интернет без отдельной аутентификации и ограничения ресурсов: текущая конфигурация предназначена для localhost.

## Диагностика

```bash
docker compose ps
docker compose logs --tail=100 engine
curl http://localhost:8080/api/health
curl http://localhost:8080/metrics
docker stats --no-stream
docker compose exec postgres psql -U brokeweb -d brokeweb -c "SELECT pg_size_pretty(pg_database_size('brokeweb'));"
```

После частичного пересоздания API без frontend nginx может сохранять прежний IP upstream и отдавать 502. Обновите его DNS-разрешение командой `docker compose exec -T frontend nginx -s reload`, затем проверьте `/api/health` через порт 8080. При полном обновлении frontend пересоздаётся вместе с остальными сервисами.

- Пустой `/setups`: проверить «показать WAIT SETUP», health и In-Play gates. Пустой список не является поводом менять baseline thresholds.
- RECOVERING: смотреть initialized/selected, ошибки, часы backfill. History depth зависит от всех параметров и requested TF.
- BTC context error: проверить `btc_stream`, `btc_recovering` и `btc_timeframe_events` в `/api/health`. Native Binance USD-M kline WebSocket использует routed `/market/stream`; REST восстанавливает историю после reconnect. При недоступном/устаревшем WS snapshot получает `BINANCE_REST_FALLBACK` и DEGRADED, даже если REST работает. Baseline не заменяется Bybit BTC.
- 1m/30S DEGRADED: исторические micro candles нельзя получить стандартным kline REST; нужно дождаться достаточного полного trade feed. Trade subscription capacity показана отдельно.
- Пересоединение WS: stable subscriptions обновляются без разрыва существующих соединений; после настоящего disconnect выполняется backfill и replay, trade completeness сбрасывается.
- Поздняя сделка: неполное chart coverage больше не помечается FULL_REALTIME.
- Исправленная confirmed kline: engine замораживается в RECOVERING и запускает replay из прежнего origin. Replay по OHLCV не может восстановить пропущенные intrabar samples.
- `uncertain` Telegram: проверить чат перед ручным повтором; automatic retry может дублировать уже доставленное сообщение.

## Restore и параметры

При SIGTERM worker прекращает приём новых событий, дожидается выполняющихся расчётов и транзакций, закрывает feeds и освобождает Redis lease. Docker предоставляет 75 секунд на graceful stop. При аварийном SIGKILL или падении хоста lease может удерживаться до 60 секунд; автоматический restart повторит запуск.

Named volumes переживают обычный restart. Изменение Pine source/engine version/parameter hash запрещает прямую загрузку несовместимого checkpoint. Source файл неизменяемый в рамках версии; новый Pine должен иметь отдельный versioned artifact.

Для backup использовать `pg_dump`, для restore — `psql` в отдельную БД и отдельно проверить provenance. Не редактировать persisted FSM вручную. Rule dedupe хранится в PostgreSQL, поэтому Redis restart не создаёт повторную рассылку.

## Capacity и известные эксплуатационные границы

`healthy_engines` пересчитывается на каждом heartbeat: сохранённый HEALTHY перестаёт считаться свежим через 90 секунд с учётом исходного отставания. Общий HEALTHY требует всех выбранных symbol/TF, свежих WS/BTC contexts и отсутствия recovery/errors. Пустой universe не означает готовность. `stale_instruments` считает уникальные символы, а не symbol/TF пары.

`market_data_lag_ms` — максимальное отставание snapshot среди загруженных engines, включая прошедшее после расчёта время; replay также попадает в эту метрику. `checkpoint_export_latency_ms` — средняя длительность последних 1000 экспортов состояния, отдельно от `db_write_latency_ms` (clean/JSON/compression/SQL). Ожидание потока входит в длительность. `calculation_latency_ms` — p95 последних 1000 расчётов; `calculations_per_second` включает replay. Все четыре метрики публикуются в `/metrics`. Это показатели worker, а не сквозная задержка доставки alert.

Короткий probe работает и во время RECOVERING, не меняет subscriptions/настройки и не ждёт общего HEALTHY:

```bash
python3 -m tools.capacity_probe --samples 3 --interval 15
docker compose run --rm --no-deps -T api python -m tools.checkpoint_benchmark
```

Первый сохраняет `artifacts/local/capacity-probe.json`: либо `NOT_READY` с причинами, либо `READY_FOR_SOAK`. В обоих случаях `capacity_status=UNVERIFIED`: короткая выборка не заменяет длительный прогон, sizing/WAL и restart acceptance. Проверяются heartbeat, полный набор shards, initialization, свежесть engines/streams/BTC, ошибки и прогресс calculations.

Второй только читает один совместимый checkpoint и сравнивает прежний и оптимизированный обходы одного состояния (по три повтора с чередованием порядка), проверяет точное совпадение сжатых байтов и повторный restore/export. БД не изменяется; запускать отдельным процессом. Время SQL и throughput рынка этот benchmark не измеряет. Экспорт worker выполняется в потоке под прежней блокировкой символа; частота checkpoint, последовательность executions и сохранение intrabar/varip не сокращены.

Движок интерпретирует source, а не pandas-пересчёт всей истории. EMA/RMA обновляются от предыдущего результата; history ограничена буферами. Однако snapshots/checkpoints сейчас объёмны, а main Python worker не оптимизирован для сотен FULL_REALTIME подписок. Уменьшение частоты обязательных Pine execution ради скорости не применяется незаметно.

Не хватает production acceptance: полный нагрузочный прогон universe, sizing/WAL/archive-backup acceptance, external TradingView golden/reference наборы, проверка последовательности Binance/TradingView updates. Эти пункты не являются декоративными optional enhancements. UI и API предоставляют исследовательский локальный стенд до их закрытия.

## Retention

Сервис `retention` по умолчанию работает в preview. `/api/storage` показывает каталог и состояние, `/api/health` — heartbeat/ошибки. Перенос старых snapshots/events, recovery и backup: [retention.md](retention.md). Current/checkpoints, 30S bars, signals/dedupe и Research не очищаются.

Месячные разделы snapshots/events внедрены в миграции 0005. Она требует окна обслуживания и backup существующей БД. После применения будущие месяцы готовит retention; DEFAULT продолжает принимать даты вне диапазонов. Инструкция: [partitioning.md](partitioning.md).

## Временная зона интерфейса

В **Settings → Временная зона** выберите UTC-смещение и нажмите «Сохранить временную зону». По умолчанию **UTC+07:00**. Доступны смещения от UTC−12:00 до UTC+14:00 с шагом 15 минут, без сезонного перехода на летнее время.

Настройка общая и сохраняется на сервере. Свечной график, графики атрибутов, подсказки свечей/событий, Timeline и время обновлений используют выбранное смещение. Поля «С / По» пользовательского периода истории также читаются в этой зоне, независимо от зоны браузера. Другие открытые вкладки получают настройку при обновлении settings (до 30 секунд).

API: `GET /api/settings` возвращает `timezone_offset_minutes` (default `420`). `PUT /api/settings` с `{"timezone_offset_minutes": 420}` меняет только зону; прежний snapshot interval сохраняется. Запрос с одним `snapshot_interval_sec` также сохраняет выбранную зону. Уже существующие настройки без этого поля получают UTC+07:00 автоматически. Timestamp в базе/API, границы свечей, условия alerts и расчёты Pine остаются в UTC; эта настройка меняет только представление.

На свечном графике подписи близких зон и SL/T1 разведены по вертикали и соединены с точным уровнем линиями. События одной свечи и соседние тесно расположенные маркеры объединяются в нумерованные группы над графиком: `номер · количество`. Наведение показывает все события группы, клик или Enter открывает полный список «События на графике». Группы пересчитываются при масштабировании; события не удаляются. На узком экране график можно прокручивать горизонтально.
