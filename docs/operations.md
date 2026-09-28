# Operations

## Нормальный запуск

Порядок: Postgres/Redis health → migrations/API → engine/notifier/frontend. Engine загружает inputs, получает весь universe и Binance contexts, загружает историю каждого выбранного контракта, восстанавливает checkpoint либо выполняет replay, затем подключает realtime. Docker healthcheck означает живой процесс; `/api/health` отдельно показывает RECOVERING/DEGRADED/HEALTHY расчётов.

Текущий стенд использует TF30 и предварительный фильтр оборота: Settings → «Пул монет — ликвидность», по умолчанию 10 млн USDT за 24 часа. `MAX_SYMBOLS=0` означает отсутствие дополнительного лимита количества после фильтра. Поле `selected` в `/api/health` — выбранные для shard, `initialized` — закончившие bootstrap, `universe` — все доступные. Capacity для сотен инструментов ещё не принята. Не разворачивать в публичный интернет без отдельной аутентификации и ограничения ресурсов: текущая конфигурация предназначена для localhost.

## Диагностика

Продление Redis shard lease выполняется отдельной async-задачей каждые 10 секунд, начиная до инициализации БД. Оно не ждёт записи SQL heartbeat или освобождения thread pool. TTL остаётся 60 секунд, продление проверяет token владельца; потеря владения или ошибка/таймаут Redis останавливает worker. При штатном shutdown lease продлевается до завершения расчётов, транзакций и финального heartbeat, затем освобождается. Это устраняет зависимость владения от задержки SQL telemetry, но не решает общую перегрузку CPU/event loop или недоступность Redis.

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

Для совместимого checkpoint startup запрашивает историю от самого старого committed chart/request timestamp, если все девять request callsites исходника присутствуют, подтверждены и укладываются в загруженный BTC history span. В логах появляется `checkpoint_incremental_backfill` с символом инструмента. Это только сокращение повторной REST-загрузки: накопленные индикаторные истории и persistent/varip state остаются в checkpoint. Неполное состояние, cold start, устаревший checkpoint или смена semantics сохраняют полный backfill/replay; runtime health остаётся RECOVERING до завершения. При смене semantics первоначальный origin обязателен. Reconnect recovery работает по прежнему пути.

Named volumes переживают обычный restart. Изменение Pine source/engine version/parameter hash запрещает прямую загрузку несовместимого checkpoint. Source файл неизменяемый в рамках версии; новый Pine должен иметь отдельный versioned artifact.

Для backup использовать `pg_dump`, для restore — `psql` в отдельную БД и отдельно проверить provenance. Не редактировать persisted FSM вручную. Rule dedupe хранится в PostgreSQL, поэтому Redis restart не создаёт повторную рассылку.

## Capacity и известные эксплуатационные границы

`healthy_engines` пересчитывается на каждом heartbeat: сохранённый HEALTHY перестаёт считаться свежим через 90 секунд с учётом исходного отставания. Общий HEALTHY требует всех выбранных symbol/TF, свежих WS/BTC contexts и отсутствия recovery/errors. Пустой universe не означает готовность. `stale_instruments` считает уникальные символы, а не symbol/TF пары.

`market_data_lag_ms` — максимальное отставание snapshot среди загруженных engines, включая прошедшее после расчёта время; replay также попадает в эту метрику. `checkpoint_export_latency_ms` — средняя длительность последних 1000 экспортов состояния, отдельно от `db_write_latency_ms` (нормализация snapshot и SQL; с 2026-09-27 упаковка engine checkpoint измеряется отдельно). Ожидание потока входит в длительность. `calculation_latency_ms` — p95 последних 1000 расчётов; `calculations_per_second` включает replay. `checkpoint_pack_latency_ms` — средняя длительность JSON/compression последних 1000 engine checkpoints. Все эти метрики публикуются в `/metrics`. Это показатели worker, а не сквозная задержка доставки alert.

Короткий probe работает и во время RECOVERING, не меняет subscriptions/настройки и не ждёт общего HEALTHY:

```bash
python3 -m tools.capacity_probe --samples 3 --interval 15
docker compose run --rm --no-deps -T api python -m tools.checkpoint_benchmark
docker compose run --rm --no-deps -T api python -m tools.checkpoint_benchmark --prepared --repeats 5
```

Первый сохраняет `artifacts/local/capacity-probe.json`: либо `NOT_READY` с причинами, либо `READY_FOR_SOAK`. В обоих случаях `capacity_status=UNVERIFIED`: короткая выборка не заменяет длительный прогон, sizing/WAL и restart acceptance. Проверяются heartbeat, полный набор shards, initialization, свежесть engines/streams/BTC, ошибки и прогресс calculations.

Второй только читает один совместимый checkpoint и сравнивает прежний и оптимизированный обходы одного состояния (по три повтора с чередованием порядка), проверяет точное совпадение сжатых байтов и повторный restore/export. БД не изменяется; запускать отдельным процессом. Время SQL и throughput рынка этот benchmark не измеряет. Экспорт worker выполняется в потоке под прежней блокировкой символа; частота checkpoint, последовательность executions и сохранение intrabar/varip не сокращены.

`--prepared` сравнивает прежний export + повторный `clean` с новым canonical export + подготовленными BWC1-байтами. Каждый realtime execution по-прежнему сохраняется в той же SQL-транзакции вместе со snapshot; частота checkpoint и durable alerts не менялись. Обычные dictionary checkpoints сохраняют прежнюю нормализацию. Reader, формат BWC1 и engine version прежние. После изменения нельзя сравнивать один `db_write_latency_ms` до/после как общее ускорение: теперь нужно учитывать отдельно export + pack + DB.

Для воспроизводимой offline-проверки индекса контекстов:

```bash
python3 -m tools.context_benchmark --fixture artifacts/local/contexts-ethfi/fixture.json --bars 100 --repeats 3 --output artifacts/local/context-index.json
```

Нужен сохранённый native fixture с обычными закрытыми chart bars. Сравниваются все snapshots без wall-clock telemetry и итоговый checkpoint; индекс строится отдельно для каждой пары замеров. В worker он повторно используется до обновления соответствующего потока. Будущие свечи не становятся доступными раньше, developing candle пересчитывается по прежним правилам. Source hash, engine version и формат checkpoint не менялись.

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

## Предварительный фильтр ликвидности

`universe_min_turnover24h_usdt` в `/api/settings` — неотрицательное число USDT (без кавычек в JSON); в Settings поле показано в **миллионах USDT**. Например, `10` в интерфейсе соответствует `10000000` в API. `0` отключает фильтр. Порог включительный: ровно 10 млн проходит. Отсутствующий, отрицательный или нечисловой оборот не проходит включённый фильтр.

Источник — native Bybit `turnover24h`, а не `volume24h` в монетах и не Pine proxy. Фильтр действует до REST backfill и создания engine, затем монеты прогреваются по убыванию оборота. Pine gates и формулы не изменяются. Pin/full-realtime не обходит этот фильтр. Изменение применяется при следующем discovery: на запуске и после завершения прогрева с интервалом `UNIVERSE_REFRESH_SEC` (по умолчанию час). Для немедленного применения сохраните настройку и перезапустите engine.

Исключённые состояния получают `universe_excluded=true`, STALE, WAIT SETUP и пустые signals; checkpoints и история сохраняются. При возвращении монеты в пул штатный прогрев восстанавливает расчёт. `/api/health` показывает исходный `universe`, выбранный для shard `selected`, `liquidity_excluded` и применённый порог. Оборот — предварительный критерий ликвидности, не измерение спреда или глубины стакана.
