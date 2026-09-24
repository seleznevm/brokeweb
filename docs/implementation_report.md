# Отчёт реализации Brokeweb — обновлено 2026-09-24

В `C:\dev\brokeweb` работает локальный стенд с реальными публичными данными Bybit и Binance. **Полный Definition of Done ещё не принят:** исторические метрики нового reference прошли проверку, но сигналы этой сессии, intrabar, нагрузка всего universe и эксплуатационная приёмка хранения ещё требуют подтверждения.

Исходный Pine сохранён без изменений, SHA-256: `782ff6575c9e6e997dea386d429264ea277de22f170e29c0886c62a63c76881e`. Engine version: `1.15.2-interpreter.3`. Новый CSV `30_22f96` с подтверждёнными defaults: **45/45 метрик PASS, 10 117 закрытых свечей**, без пропусков. FSM/ACTION, пути, gates, уровни, SL/T1 совпали точно; для численных метрик применены прежние допуски. Общий статус UNVERIFIED: сигнальные колонки отсутствуют. FX взят из отдельного диагностического request; для live пока остаётся исходный fallback. [Текущий отчёт](../reports/context-parity.json).

Ниже сохранены более ранние эксплуатационные проверки; их дата и ограниченный universe не описывают текущую нагрузку. Последний локальный Python suite: **365 passed**, затем **17 capacity-probe tests passed** после добавления двух проверок покрытия universe/shards. Предыдущая frontend-проверка: **22 tests**, lint, typecheck и production build. Прежние сравнения семи обычных CSV и первого полного ETHFI CSV сохранены отдельно. Формулы исполняются из AST исходника; совпадение одного исторического набора не означает полной совместимости с Pine.

## Что реализовано

- Полный парсинг исходника, каталог 323 inputs, 1 594 строки mapping переменных и локальных вычислений. Исходный порядок вычислений, persistent arrays/UDT, отдельные `var`/`varip`, rollback и confirmed commit, независимые contexts `request.security` / lower TF.
- Динамический discovery Bybit USDT perpetual, фильтрация статусов, backfill, native kline WebSocket, incremental subscriptions/shards, public trades, построение 30S с признаками полноты покрытия. Native turnover хранится отдельно от Pine volume proxy.
- Binance BTCUSDT perpetual history и native kline WebSocket для requested TF. REST восстанавливает пропуски; старый ответ не затирает свежий WS sample. Недоступный/устаревший streaming context явно помечается DEGRADED.
- PostgreSQL: бары, current state/checkpoints, snapshots, transitions, detector signals, Research samples, версии параметров/правил, durable alert outbox. Redis: realtime fanout и исключительное владение worker shard.
- API, dense setup table, detail с историей и Decision Panel, редактор всех inputs, visual rule builder, журнал доставок, health и parity pages.
- Правила AND/OR/NOT, comparisons/crossings, частоты/cooldown, confirmed/realtime modes, подавление replay/stale/recovering, версии и deduplication после restart. Telegram transport проверен mock-сервером; реальная отправка в чат в ходе проверки не выполнялась.
- Checkpoint recovery, lossless compressed checkpoint storage с чтением прежнего JSON, backfill после обрыва, historical rebuild при исправленной confirmed kline, graceful stop с ожиданием текущих расчётов/транзакций до освобождения lease.
- Docker Compose, миграции Alembic, healthchecks, localhost binding, тесты/CI, exporter/replay/comparator для внешней Pine-проверки.

## Дополнение 2026-09-24: checkpoint и достоверность health

Ускорены скалярные ветви `encode`/`clean`, убран лишний запрос активной версии параметров при сохранении известного `parameter_set_id`. Формат checkpoint BWC1, Pine source и engine semantics не изменены. Экспорт перенесён из event loop в ожидаемый worker thread; блокировка символа и ожидание завершения при cancellation сохраняются. Каждый realtime execution по-прежнему сохраняет intrabar checkpoint.

Read-only benchmark на реальном AXSUSDT TF30 checkpoint **1 321 137 bytes**, три пары запусков: медиана export + clean/pack **820,35 → 343,47 ms**, ускорение **2,39×**. Сжатые байты совпали во всех повторах, restore/export сохранил состояние. Замер выполнен при работающем стенде; это один checkpoint, без SQL write и без доказательства full-universe throughput. [Отчёт benchmark](../reports/checkpoint-optimization.json); воспроизведение: `tools/checkpoint_benchmark.py`.

Health теперь учитывает старение последнего расчёта и lag, полноту symbol/TF, freshness WS/BTC и recovery. Старый HEALTHY не остаётся свежим при остановке данных. Добавлены export latency, максимальный market lag и calculations/s в Prometheus. `tools/capacity_probe.py` сохраняет ограниченную выборку даже при RECOVERING и перечисляет причины неготовности; положительная короткая проба означает только готовность к длительному испытанию.

После обновления Docker все семь контейнеров healthy; API и Prometheus возвращают новые метрики. Короткий post-deploy probe: **NOT_READY / UNVERIFIED**, 7 из 777 инструментов восстановлены в последней пробе, +17 расчётов за 20.075 s. Это стартовое окно после restart, не устойчивый throughput. Причины и последний heartbeat включены в отчёт benchmark.

До окончательной реализации остаются: внешний recorder v3 capture с активными сигналами и согласованным initial state/request contexts; оптимизация и длительная нагрузочная приёмка полного universe без пропуска обязательных executions; sizing БД/WAL и backup/restore с непустым архивным диском; реальная приёмка Telegram-доставки.

## Дополнение 2026-09-23: правила и intrabar replay

Редактор условий получает каталог типов с сервера. Для ограниченных наборов значений доступны одиночный select и multiselect в зависимости от оператора; boolean сохраняется как boolean, TF — строкой, score — числом. `BETWEEN` показывает две границы, `changed` не требует значения. Добавлены `contains_any` / `contains_all` для списков событий и blockers. Сервер проверяет типы, операторы и пресеты при сохранении; старые неизвестные значения остаются видимыми для исправления. Все поля и примеры ввода описаны в [alert_rules.md](alert_rules.md).

Добавлен offline replay recorder v3: исполняется исходный AST, а результаты десяти request-вызовов подаются из capture. Проверяются последовательность, начало бара, rollback/varip, повторные confirmed updates и 45 метрик / 26 сигналов. Отчёт разделяет поставленные входы и downstream-вычисления. Даже `DIAGNOSTIC_MATCH` сохраняет полный статус **UNVERIFIED**: native warmup не является проверенным checkpoint TradingView, а сами request-вычисления здесь обходятся. v1/v2 явно отклоняются, поскольку необходимых contexts в них нет. Запуск и ограничения: [tradingview_capture.md](tradingview_capture.md).

Проверки этого дополнения: **334 Python tests**, compileall, **16 frontend tests**, ESLint, TypeScript и production build. Новые frontend tests проверяют преобразования типов и HTML контролов через React SSR; интерактивная браузерная проверка нового редактора в этот прогон не входила. Исходный Pine не изменён.

До окончательной реализации и приёмки остаётся:

1. Получить реальный recorder v3 capture после компиляции в TradingView: активные setup, положительные сигналы, согласованные настройки и начальное состояние. Проверить downstream-метрики и порядок intrabar-событий, затем расширить reference на другие символы/TF. Историческому reference текущей версии также нужны сигнальные колонки.
2. Завершить full-universe capacity acceptance: выйти из восстановления, измерить lag/throughput и DB writes под устойчивой нагрузкой; подтвердить восстановление потоков BTC и полноту 30S/trades. При необходимости оптимизировать или разделить worker-нагрузку.
3. Принять эксплуатацию хранения: sizing, WAL, retention вне preview, архивный диск и восстановление из backup с непустыми архивами на репрезентативном объёме.
4. Проверить реальную Telegram-доставку отдельным явно запрошенным тестом и интерактивный цикл создания/редактирования правил в браузере. Mock transport и render tests не заменяют эти проверки.
5. Закрыть оставшиеся ограничения источников: live FX и временное согласование native contexts с TradingView; отсутствующие внешние OI/CVD/input.source не подменять фиктивными данными.

## Дополнение: подписи графиков и временная зона

Исправлены пересечения подписей R/S, LOCKED/CONSUMED и SL/T1: текст разнесён по вертикали, точный уровень отмечен соединительной линией. События одной свечи и близкие маркеры объединены в нумерованные группы с полным списком по клику/Enter и tooltip. При zoom группы пересчитываются без удаления событий. На мобильной ширине прокручивается сам график, а не вся страница.

В Settings добавлена общая серверная настройка `timezone_offset_minutes`, default **420 / UTC+07:00**. Она применяется к графикам, подсказкам, Timeline и времени обновлений; custom history range интерпретируется в выбранной зоне. UTC timestamps и расчёты не меняются. Старые settings получают default автоматически; изменение зоны и snapshot interval сохраняет остальные настройки.

Проверено: **335 backend tests**, **22 frontend tests**, lint/typecheck/build, compileall. Chromium regression `tools/browser_chart_settings.cjs` прошёл на desktop/mobile с шестью совпадающими зонами и 40 синтетическими событиями, при browser timezone America/New_York: отсутствие пересечений, zoom, полный список событий, UTC+7/UTC/UTC−5:30, сохранение/перезагрузка формы, перевод пользовательского периода в UTC и типы alert select/multiselect. Settings и рыночные данные в браузерном сценарии перехватываются тестовыми routes, live правила не меняются и сообщения не отправляются; серверное сохранение проверено отдельным API integration test. Evidence: `artifacts/local/chart-settings/chart-settings-browser.json` и PNG. На обновлённом стенде `/api/settings` возвращает `timezone_offset_minutes: 420`; семь контейнеров healthy, готовность рынка по-прежнему оценивается отдельно через `/api/health`.

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
# Текущее окружение: MAX_SYMBOLS=0, ACTIVE_TIMEFRAMES=30
docker compose up -d --build
docker compose ps
docker compose logs --tail=100 engine
```

Открыть [локальный стенд](http://localhost:8080/setups). Первичная установка, workaround WSL credential helper, настройки Telegram и TF: [README](../README.md). Docker volumes сохраняются при обычном restart/down.

Ранние проверки выполнялись на четырёх контрактах. Текущее окружение использует `MAX_SYMBOLS=0`, `ACTIVE_TIMEFRAMES=30`: весь обнаруженный universe и независимые сигналы 30m. Capacity acceptance всего universe не выполнен.

## HTTP/API и UI

| Интерфейс | Возможности |
|---|---|
| `GET /api/setups` | Active-only, поиск, диапазоны метрик, sorting/pagination |
| `GET /api/setups/{symbol}/{tf}` и `/history`, `/bars`, `/events` | Current, исторические метрики, свечи с текущей открытой, события по периоду |
| `GET /api/signals`, `/api/instruments`, `/api/research` | Сигналы, universe, Research |
| `GET/PUT /api/parameters`, `/api/settings` | Source-derived inputs, версии, snapshot interval |
| `GET/POST /api/alerts/rules`, `PUT/DELETE /api/alerts/rules/{id}` | Правила и версии |
| `POST /api/alerts/rules/test` | Preview совпадений без отправки |
| `GET /api/alerts/fields` | Типы полей, допустимые операторы и пресеты редактора |
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
| Полная parity ещё не принята | 45/45 исторических метрик прошли; новый CSV не содержит сигналов. Intrabar v1/v2 получены: ATR/EMA проверены, остальные вычисления пока не подтверждены. Нужен внешний capture v3 с синхронными request results и активными сигналами. Live FX использует исходный fallback |
| Pine compatibility subset | Парсер/интерпретатор собственного изготовления; tie cases, barmerge и рекурсивные seeds требуют внешней проверки |
| Историческое начало влияет на зоны/EMA | Persistent состояние может зависеть от данных за пределами конечного warmup; origin сохраняется, reference должен его учитывать |
| Intrabar sampling отличается от возможного TradingView feed | Native exchange WS не доказывает тот же порядок/частоту executions у TradingView; источник BTC теперь streaming, но tick parity остаётся gate |
| Пропущенные 30S/trades и ticks при аварии | OHLCV backfill не восстанавливает unseen intrabar samples; полнота явно снижается, данные не фабрикуются |
| External `input.source` / BTC symbol / TF | Произвольный внешний plot не предоставлен; поддерживаются native series. BTC adapter обслуживает BINANCE:BTCUSDT.P; изменение symbol или выбор ненативного TF требует отдельного adapter. Отсутствующий context не подменяется |
| Масштаб всего universe | Испытано четыре symbol/TF; большие checkpoints и синхронное сохранение ограничивают throughput |
| Retention и native partitions реализованы; sizing ещё не принят | Bounded archive/restore и месячные разделы snapshots/events проверены; по умолчанию preview. Остальные таблицы и archive disk требуют отдельной политики/backup; full-universe capacity не измерен |
| Telegram live delivery не проверена | Transport/dedupe проверены mock-сервером; реальная доставка в чат ещё не принята |

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
