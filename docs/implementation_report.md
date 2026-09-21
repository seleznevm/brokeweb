# Отчёт реализации Brokeweb — 2026-09-15

В `C:\dev\brokeweb` работает локальный стенд с реальными публичными данными Bybit и Binance. **Полный Definition of Done исходного ТЗ ещё не принят:** числовое/stateful совпадение с TradingView не измерено, испытание всего universe и эксплуатационная приёмка объёма/WAL/архивного backup ещё не завершены. Тесты приложения не заменяют эти проверки.

Исходный Pine сохранён без изменений. SHA-256: `782ff6575c9e6e997dea386d429264ea277de22f170e29c0886c62a63c76881e`. Engine version: `1.15.2-interpreter.2`. Получены 7 TradingView CSV: 2993 закрытые свечи, доступные уровни/SL/T1 и 124 положительные метки совпали после исправления float comparisons. Полный reference внутренних оценок и тиков пока отсутствует. Формулы исполняются из AST исходника, а не из независимо подобранного похожего индикатора; это всё ещё требует проверки совместимости интерпретатора с Pine.

## Что реализовано

- Полный парсинг исходника, каталог 323 inputs, 1 594 строки mapping переменных и локальных вычислений. Исходный порядок вычислений, persistent arrays/UDT, отдельные `var`/`varip`, rollback и confirmed commit, независимые contexts `request.security` / lower TF.
- Динамический discovery Bybit USDT perpetual, фильтрация статусов, backfill, native kline WebSocket, incremental subscriptions/shards, public trades, построение 30S с признаками полноты покрытия. Native turnover хранится отдельно от Pine volume proxy.
- Binance BTCUSDT perpetual history и native kline WebSocket для requested TF. REST восстанавливает пропуски; старый ответ не затирает свежий WS sample. Недоступный/устаревший streaming context явно помечается DEGRADED.
- PostgreSQL: бары, current state/checkpoints, snapshots, transitions, detector signals, Research samples, версии параметров/правил, durable alert outbox. Redis: realtime fanout и исключительное владение worker shard.
- API, dense setup table, detail с историей и Decision Panel, редактор всех inputs, visual rule builder, журнал доставок, health и parity pages.
- Правила AND/OR/NOT, comparisons/crossings, частоты/cooldown, confirmed/realtime modes, подавление replay/stale/recovering, версии и deduplication после restart. Telegram transport проверен mock-сервером; реальная отправка в чат в ходе проверки не выполнялась.
- Checkpoint recovery, lossless compressed checkpoint storage с чтением прежнего JSON, backfill после обрыва, historical rebuild при исправленной confirmed kline, graceful stop с ожиданием текущих расчётов/транзакций до освобождения lease.
- Docker Compose, миграции Alembic, healthchecks, localhost binding, тесты/CI, exporter/replay/comparator для внешней Pine-проверки.

## Сервисы и запуск

| Сервис | Назначение |
|---|---|
| frontend | React/TypeScript, nginx, `127.0.0.1:8080` |
| api | FastAPI, HTTP API и WebSocket fanout |
| engine | feeds, source interpreter, state, snapshots/checkpoints |
| notifier | durable outbox и Telegram transport |
| postgres | PostgreSQL 17, постоянные данные |
| redis | pub/sub и shard lease |
| retention | archive/restore snapshots/events, preview по умолчанию |

```powershell
cd C:\dev\brokeweb
# .env уже создан для локального стенда: MAX_SYMBOLS=4, ACTIVE_TIMEFRAMES=15
docker compose up -d --build
docker compose ps
docker compose logs --tail=100 engine
```

Открыть [локальный стенд](http://localhost:8080/setups). Первичная установка, workaround WSL credential helper, настройки Telegram и TF: [README](../README.md). Docker volumes сохраняются при обычном restart/down.

Тестовый лимит четырёх контрактов задан явно. Список выбирается автоматически по времени листинга; текущие BCHUSDT, LINKUSDT, LTCUSDT, XTZUSDT не зашиты в engine. `MAX_SYMBOLS=0` включает весь обнаруженный universe, но его capacity acceptance не выполнен.

## HTTP/API и UI

| Интерфейс | Возможности |
|---|---|
| `GET /api/setups` | Active-only, поиск, диапазоны метрик, sorting/pagination |
| `GET /api/setups/{symbol}/{tf}` и `/history`, `/bars`, `/events` | Current, исторические метрики, свечи с текущей открытой, события по периоду |
| `GET /api/signals`, `/api/instruments`, `/api/research` | Сигналы, universe, Research |
| `GET/PUT /api/parameters`, `/api/settings` | Source-derived inputs, версии, snapshot interval |
| `GET/POST /api/alerts/rules`, `PUT/DELETE /api/alerts/rules/{id}` | Правила и версии |
| `POST /api/alerts/rules/test` | Preview совпадений без отправки |
| `GET /api/alerts/deliveries`, `POST /api/alerts/test-telegram` | Журнал и явная тестовая отправка при настроенных credentials |
| `GET /api/health`, `/api/parity`, `/api/storage`, `/metrics` | Health, честный статус parity, catalogue архивов, telemetry |
| `WS /ws/setups` | Обновления setup из Redis |

Страницы: `/setups`, `/setups/BYBIT/{symbol}/{tf}`, `/alerts`, `/settings`, `/health`, `/parity`. По умолчанию setup table показывает `ACTION != WAIT SETUP`; переключатель позволяет увидеть все состояния. Пустой active-only список при неисполненных исходных gates допустим.

## Метрики и сигналы

Исполняются исходные ATR/EMA, hourly activity/NATR/volume24h proxy, structural zones и lifecycle, locked setup generation, direction, approach/acceptance, momentum/volume/micro context, Formation/Execution/Geometry/Context/Level, Exhaustion/MAE, BTC regime/shock, candidate и active paths, hard gates, FSM/ACTION, Active Plan Health, ADD-ON, AVG SETUP, CONTINUATION, frozen SL/T1/RR, TP tracker, Research outcomes. Полные raw globals и source locals доступны в analytic payload. Decision Panel получает 42 строки из исходного `f_dashRow`.

Сигналы: LONG/SHORT WATCH, WATCH ENTRY, MATURED PRE-BREAK ENTRY, ARMED, PINE READY, BREAKOUT/BREAKDOWN, BRONZE/STRONG, reversal risk, ACTIVE PLAN DEGRADED/EXIT, ADD-ON, TP HIT, ARMED LOST, BOUNCE WATCH, realtime AVG70/Execution65. Точные переменные/формулы/timing приведены в [signals.md](signals.md), полный field mapping — в [pine_mapping.md](pine_mapping.md).

«Исполняется» означает покрытие исходного расчёта приложением. Это не заявление о доказанном 1:1 совпадении результата с TradingView.

## Проверки

| Проверка | Результат / evidence |
|---|---|
| Python unit/integration/parity regressions | 173 passed; `artifacts/local/comparator-tests.txt` |
| Python compileall | PASS для `backend` и `tools` |
| Frontend lint/typecheck/Vitest/build | PASS, 4 Vitest tests; Docker build выполняет эти команды |
| PostgreSQL/Redis/API/durable dedupe/Telegram mock | PASS: PostgreSQL/Redis smoke в отдельной временной схеме; `artifacts/local/integration-smoke.json` |
| Browser | 6 страниц, desktop/mobile, без JS errors; `artifacts/local/browser.json` и PNG |
| Binance WS | Реальные native 5m/15m updates получены; `artifacts/local/binance-websocket.json` |
| Checkpoint recovery | PASS: 4/4 checkpoints restored; restart command 6,43 s, HEALTHY через 34,54 s, без lease conflict; `artifacts/local/restart.json` |
| Миграция checkpoint storage | PASS: PostgreSQL upgrade 0003 → downgrade 0002 → upgrade 0003 с сохранением state; `artifacts/local/checkpoint-migration.json` |
| Обновление семантики engine .1 → .2 в Docker | PASS: 4/4 прежних history origins сохранены, 7 контейнеров healthy; `reports/deployment.json` |
| TradingView CSV reference | PASS доступных plots на 2993 свечах / 7 инструментах, 124 положительные метки; полный parity UNVERIFIED. Debug Pine не компилировался в Pine Editor |

Один warning локального Python suite относится к deprecation `starlette.testclient`/`httpx`, без падения тестов. Проверки включают rollback, generation state, frozen levels, history indexing, persistence/dedupe, реальные SQL/Redis соединения с mock Telegram, источник BTC и порядок обновлений. Синтетические regression cases не названы TradingView golden datasets.

## Parity

В обычных CSV TradingView проверены 2993 закрытые свечи TF30 на ARB, DASH, DOT, ETHFI, OP, PENGU и VVV. R1/R2/S1/S2, Forecast T1 и Estimated Structural SL совпали точно (median/P95/max = 0); все 124 положительные визуальные метки совпали по свечам. Первоначальное расхождение ETHFI T1 0.7 вместо 0.8 устранено добавлением Pine-семантики сравнения float с округлением до девяти знаков. Исходник и допуски не изменены.

`reports/parity.json`: `observed_status=PASS`, полный `status=UNVERIFIED`. EMA скрыты, внутренние оценки/FSM и intrabar не экспортированы; принадлежность сторонней колонки `direction` не установлена. Применены исходные defaults без отдельного подтверждения настроек пользователя. Полная методика, исходный FAIL, повторный PASS, различия объёма источников и недостающие acceptance gates: [parity.md](parity.md).

## Известные ограничения и причины

| Ограничение | Причина / последствие |
|---|---|
| Числовые расхождения пока не измерены | Нет независимого TradingView oracle; нельзя назвать median/P95 или agreement |
| Pine compatibility subset | Парсер/интерпретатор собственного изготовления; tie cases, barmerge и рекурсивные seeds требуют внешней проверки |
| Историческое начало влияет на зоны/EMA | Persistent состояние может зависеть от данных за пределами конечного warmup; origin сохраняется, reference должен его учитывать |
| Intrabar sampling отличается от возможного TradingView feed | Native exchange WS не доказывает тот же порядок/частоту executions у TradingView; источник BTC теперь streaming, но tick parity остаётся gate |
| Пропущенные 30S/trades и ticks при аварии | OHLCV backfill не восстанавливает unseen intrabar samples; полнота явно снижается, данные не фабрикуются |
| External `input.source` / BTC symbol / TF | Произвольный внешний plot не предоставлен; поддерживаются native series. BTC adapter обслуживает BINANCE:BTCUSDT.P; изменение symbol или выбор ненативного TF требует отдельного adapter. Отсутствующий context не подменяется |
| Масштаб всего universe | Испытано четыре symbol/TF; большие checkpoints и синхронное сохранение ограничивают throughput |
| Retention и native partitions реализованы; sizing ещё не принят | Bounded archive/restore и месячные разделы snapshots/events проверены; по умолчанию preview. Остальные таблицы и archive disk требуют отдельной политики/backup; full-universe capacity не измерен |
| Telegram live delivery не проверена | Credentials не заданы; transport/dedupe проверены без внешних сообщений |

Ни один из этих обязательных acceptance gates не отнесён к optional enhancements. Дополнительные биржи, enhanced OI/CVD и косметические улучшения можно рассматривать отдельно после baseline parity.

## Производительность

Приведённые ниже замеры относятся к предыдущему запуску шести сервисов (до добавления retention). Это сохранённый baseline, а не новый замер семи сервисов.

Замеры выполняются на локальном Docker с четырьмя symbol/TF; они не являются full-universe нагрузочным тестом. Health/resource/database evidence и оценка роста приведены ниже. p95 из worker относится к последним 1000 calculations и может включать восстановление; средняя DB write latency — к последним 1000 сохранениям. Эти значения нельзя трактовать как гарантированную сквозную задержку сигнала.


Измерено **2026-09-15 00:20:04–00:21:14 UTC**, три пробы после checkpoint restart, 4 инструмента × TF 15m, universe 762, KLINE_REALTIME. Все 4 engines HEALTHY, ошибки/reconciliation errors отсутствовали. Счётчик увеличился на 52 расчёта за 69.9 s (0.74 calculations/s в этом окне). Worker p95 calculation **40,8–42,5 ms**, среднее сохранение **427,8–493,7 ms**. Это наблюдаемая интенсивность входящего рынка, не максимальная пропускная способность. REST/backfill и фоновые службы работали; выборка короткая и включает период после восстановления.

| Сервис | CPU в трёх пробах | RAM в трёх пробах |
|---|---|---|
| engine | 10,99–101,60% | 251,2–257,0 MiB |
| api | 0,12–0,22% | 81,61 MiB |
| postgres | 0,06–9,97% | 265,6–266,5 MiB |
| redis | 0,39–0,53% | 6,73–7,12 MiB |
| notifier | 0,20–0,27% | 51,23 MiB |
| frontend | 0,00% | 7,45–7,46 MiB |

Docker CPU может превышать 100%: 100% соответствует одному полностью занятому CPU. Это моментальные пробы, не усреднённая утилизация за сутки. Host Docker memory limit 7,608 GiB. Сжатый checkpoint занимает 1,41–1,52 MB на инструмент; запись состояния остаётся узким местом и не позволяет объявить full-universe capacity принятой.

На момент замера БД **190.55 MB (181.72 MiB)**, 3336 snapshots, 83 detector signals, 23626 native bars. Средний физический размер snapshot payload ~24.34 kB (PostgreSQL `pg_column_size`). Таблица current с TOAST/index/free space занимает 81,35 MB, snapshots — 86,60 MB; checkpoint overwrite создаёт WAL/dead tuples, поэтому общий размер не равен сумме актуальных четырёх blobs.

Оценка **только payload новых snapshots**, без индексов, WAL, transitions, signals, market bars и checkpoint churn:

- Постоянно активный instrument/TF, интервал 15 s: 5 760 snapshots/day × 24.34 kB ≈ **140.2 MB/day**; для четырёх — 560.8 MB/day.
- WAIT SETUP без transitions, закрытие каждые 15 min: 96 snapshots/day ≈ **2.34 MB/day** на instrument/TF.
- Для 762 постоянно активных instrument/TF та же формула дала бы ~106.8 GB/day только payload. Это сценарная оценка, не измеренный production growth.

Исходные данные: `artifacts/local/performance.json`, `health.json`, `compose-ps.txt`; повторение измерения из корня проекта: `python3 -m tools.capture_local_validation`, с явным тестовым перезапуском — добавить `--restart`. В WSL при необходимости применить `DOCKER_CONFIG=/tmp/brokeweb-docker`.

## Продолжение: retention (2026-09-15)

Реализованы lossless gzip JSONL archive/restore для snapshots/events, durable file verification перед transactional delete, SHA-256 и каталог `archive_batches`, bounded batches, advisory lock, защита от конфликтующего restore и конкурентной выдачи IDs. Alembic 0004 добавляет chronological indexes CONCURRENTLY для существующего PostgreSQL. Непустой каталог защищён от downgrade, который потерял бы pointers к данным.

Compose теперь содержит 7 сервисов. Retention запускается в preview: рабочая история не удаляется. `GET /api/storage` показывает policy и архивы, health показывает состояние фонового цикла. Для включения архивирования пользователь выбирает сроки хранения в `.env`. Автоматического удаления архивных файлов нет. Current/checkpoints, signals/dedupe, Research и market bars не очищаются.

Проверки: **141 Python test PASS**, включая 15 retention regressions; PostgreSQL smoke проверил upgrade 0003→0004, valid concurrent indexes, exclusive lock, preview, archive/restore, неизменность checkpoint, sequence после restore, corruption rejection и downgrade guard. Evidence: `artifacts/local/retention-smoke.json`. Весь destructive-path smoke выполнялся только во временной схеме и каталоге; рабочие данные не очищались.

Инструкция, backup/restore и оставшиеся границы: [retention.md](retention.md). Pine parity остаётся UNVERIFIED; retention не является математической проверкой engine.

## Продолжение: monthly partitions (2026-09-15)

Alembic 0005 сохраняет SQL-строки, IDs, sequence и FK при переводе snapshots/events в UTC RANGE partitions. Миграция сравнивает исходную и новую таблицы через двусторонний EXCEPT ALL. DEFAULT обеспечивает запись вне подготовленных месяцев; retention создаёт будущие разделы и ограниченно перемещает DEFAULT backlog. Ошибка DDL откатывает перенос целиком.

**148 Python tests PASS.** Отдельный PostgreSQL smoke подтвердил migration/downgrade/reupgrade, границы високосного месяца/года, partition pruning, сохранность FK и IDs, fallback, лимит переноса, rollback при ошибке ATTACH, конкурирующего writer, archive/restore и неизменность checkpoint. Evidence: `artifacts/local/partition-smoke.json`. Это проверки storage, не доказательство Pine parity или full-universe throughput.

Инструкция окна обслуживания, физических/логических ключей и оставшихся ограничений: [partitioning.md](partitioning.md).

Рабочая БД переведена на **0005** после проверенного backup. Сохранены все строки и контрольные суммы шести таблиц: 3416 snapshots, 611 events, 4 current/checkpoints, 83 signals, Research и archive catalogue. Проверка выполнена при остановленных writers до возобновления мониторинга; evidence: `artifacts/local/live-partition-migration.json`.

Создан `artifacts/local/pre-partition.dump` (65.79 MB). Его реальный restore в отдельную временную PostgreSQL-БД совпал по количествам строк и checksums тех же шести таблиц; временная БД удалена. Evidence: `artifacts/local/backup-restore.json`. Каталог рабочих архивов на момент backup пуст; этот прогон проверяет PostgreSQL backup/restore, а полный disaster-recovery с непустым архивным диском остаётся отдельной проверкой.

Строгая проверка полного reference усилена: missing/invalid values, duplicate keys, identity и bounded realtime matching проверяются с отдельными причинами ошибок. 173 tests PASS; прежние результаты семи CSV не изменены. Подробности: [parity.md](parity.md), [comparator-validation.json](../reports/comparator-validation.json).

## Обновление 2026-09-21: 30m, rule builder и графики

Engine переключён на `ACTIVE_TIMEFRAMES=30`; сохранён режим полного universe `MAX_SYMBOLS=0`. Новые сигналы относятся к 30m. Сохранённая история 15m остаётся доступной; это не активный таймфрейм. Для 5m дополнительно поддерживается конфигурация `30,5`.

Правила получили обычный выпадающий список всех основных атрибутов и ввод произвольного пути. В новых правилах предустановлено условие TF IN ["30", "5"]. Существующие правила не переписаны. Оценки в UI/уведомлениях отображаются с максимум двумя дробными знаками; цены сохраняют точность, условия и исходные данные не округляются.

Свечи и графики метрик поддерживают wheel zoom X, wheel zoom Y над правой осью, сдвиг перетаскиванием, масштабирование перетаскиванием осей, Shift-линейку в процентах, Esc и сброс двойным кликом/Home. Ползунки убраны; clipPath идентификаторы уникальны для каждого графика.

Проверено: 218 Python tests, 84 parity/rule tests внутри Docker, 8 frontend tests, lint/typecheck/build. Браузер на реальных сохранённых данных проверил выбор атрибутов, оба масштаба, сдвиг истории, Shift-линейку, Esc, reset, масштаб графика метрик и mobile render; JS errors нет. Docker обновлён, семь контейнеров healthy; engine остаётся RECOVERING во время прогрева всего universe. [Отчёт](../reports/ui-update-validation.json).

Новый полный CSV ETHFI 30m выявил расхождения: 15/45 метрик и 23/26 сигналов прошли допуски, общий результат FAIL. `/parity` публикует этот новый отчёт. Разбор: [parity.md](parity.md).
