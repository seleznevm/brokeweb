# Pine parity: частичная проверка PASS, полная — UNVERIFIED

15 сентября 2026 получены 7 реальных выгрузок TradingView из `tradingview_data/`: 3000 свечей Bybit USDT perpetual, TF 30 минут. После исправления интерпретатора сопоставлены **2993 закрытые свечи**. На проверяемых свечах R1/R2/S1/S2, Forecast T1 и Estimated Structural SL совпали точно: median, P95 и max абсолютной ошибки — **0**. Все **124 положительные метки** WATCH / ARMED / PINE READY / REVERSAL RISK совпали по свечам; лишних меток не обнаружено.

Это проверка доступных графиков исходного индикатора при исходных настройках. Внутренние оценки панели, FSM, часть сигналов и intrabar не представлены в обычном CSV. Поэтому `reports/parity.json` имеет `observed_status: PASS`, а полный `status: UNVERIFIED`. Параметры и начало загруженной истории TradingView не записаны в CSV; применены исходные defaults, подтверждение настроек пользователем пока не получено.

## Проверенные выгрузки

| Инструмент | Строк CSV | Закрытых свечей в сравнении | Результат доступных графиков | Свечей с отличием объёма TV/Bybit |
|---|---:|---:|---|---:|
| ARBUSDT | 300 | 299 | PASS | 7 |
| DASHUSDT | 300 | 299 | PASS | 6 |
| DOTUSDT | 300 | 299 | PASS | 1 |
| ETHFIUSDT | 300 | 299 | PASS | 6 |
| OPUSDT | 300 | 299 | PASS | 6 |
| PENGUUSDT | 300 | 299 | PASS | 7 |
| VVVUSDT | 1200 | 1199 | PASS | 7 |

VVV: 2026-08-21 05:30 — 2026-09-15 04:30 UTC; остальные: 2026-09-08 23:30 — 2026-09-15 04:30 UTC. Последняя строка каждого CSV, 2026-09-15 05:00 UTC, исключена: потенциально открытая свеча, нет точного времени экспорта/флага confirmed, есть расхождения с последующим закрытием Bybit.

OHLC закрытых свечей совпали с Bybit во всех 2993 случаях; объём отличается на 40 свечах. В сравниваемом окне Python использовал **OHLCV из CSV**, перед ним — 780 настоящих свечей Bybit для прогрева. MTF/micro и BTC-контексты загружены из Bybit и Binance USD-M для соответствующих исторических периодов. Различия объёма сохранены отдельно; произвольные OI/CVD из соседних индикаторов не подмешивались в расчёт.

## Численные результаты по доступным колонкам

| Metric | Pine численных значений | Python численных значений | Error median | Error P95 | Status |
|---|---:|---:|---:|---:|---|
| R1 — цена на шкале | 2726 | 2726 | 0 | 0 | PASS |
| R2 — цена на шкале | 2595 | 2595 | 0 | 0 | PASS |
| S1 — цена на шкале | 2680 | 2680 | 0 | 0 | PASS |
| S2 — цена на шкале | 2101 | 2101 | 0 | 0 | PASS |
| Forecast T1 | 1923 | 1923 | 0 | 0 | PASS |
| Estimated Structural SL | 1923 | 1923 | 0 | 0 | PASS |

| Signal | Pine events | Python events | Match по свечам | Status |
|---|---:|---:|---:|---|
| L WATCH label | 22 | 22 | 100% | PASS |
| S WATCH label | 29 | 29 | 100% | PASS |
| LONG ARMED label | 11 | 11 | 100% | PASS |
| SHORT ARMED label | 16 | 16 | 100% | PASS |
| LONG PINE READY label | 2 | 2 | 100% | PASS |
| SHORT PINE READY label | 0 | 0 | 100% | PASS: только отсутствие |
| LONG BOUNCE WATCH label | 0 | 0 | 100% | PASS: только отсутствие |
| SHORT BOUNCE WATCH label | 0 | 0 | 100% | PASS: только отсутствие |
| LONG REVERSAL RISK warning | 21 | 21 | 100% | PASS |
| SHORT REVERSAL RISK warning | 23 | 23 | 100% | PASS |

Пары NA/NA учитываются отдельно от численных значений; NA mismatch отсутствуют. Нулевые события не доказывают поведение при появлении сигнала.

## Исправление, найденное на реальном reference

В `1.15.2-interpreter.1` была одна ошибка Forecast T1 на ETHFIUSDT, 2026-09-10 **15:30 UTC**: TradingView **0.8**, Python **0.7000000000000001**. На десятичной границе интерпретатор считал результат умножения чуть выше 0.7 и не переходил к следующему круглому уровню.

Согласно [документации Pine о float](https://www.tradingview.com/pine-script-docs/language/type-system/#float), операторы сравнения округляют float-операнды до девяти знаков после запятой. Эта семантика добавлена для всех шести операторов, арифметика не округляется. Версия engine повышена до **1.15.2-interpreter.2**; исходный Pine и допуски не изменены. Все 7 datasets повторно исполнены после исправления. Исходный результат сохранён в [baseline.json](../reports/tradingview/baseline.json), новые отчёты — в [reports/tradingview](../reports/tradingview/).

Старые checkpoints не принимаются новой версией. Worker пересчитывает историю с сохранённого начала и подавляет исторические уведомления до последней сохранённой свечи. Отсутствие первоначальной свечи останавливает восстановление, вместо незаметного сокращения истории.

## Покрытие и ограничения

- Сравниваются **выражения plot/plotshape из AST исходного Pine**, включая видимость уровней и настройки показа меток. Названия не угадываются по похожим колонкам.
- EMA Fast/Slow и LONG/SHORT T1 label во всех CSV пусты: **NOT_OBSERVED**, не подтверждение EMA или TP HIT.
- BOUNCE и SHORT PINE READY не имеют положительных примеров: подтверждено отсутствие лишних меток, появление таких сигналов ещё не проверено.
- LONG/SHORT T1 label — цена визуального маркера, а не событие достижения цели.
- Колонка `direction` не является объявленным в исходнике графиком `ALERT_DIR`; её принадлежность не установлена. Направление индикатора по ней не валидируется.
- Нет численных reference для ATR, activity/NATR/volume24h, Formation/Execution/Geometry/Context, Exhaustion/MAE/AVG/Continuation/BTC Shock, RR, generation/FSM/paths/ACTION и остальных сигналов.
- Обычный CSV не содержит последовательность тиков и не проверяет intrabar/varip, AVG70/Execution65 realtime.
- Конечные 780 свечей прогрева не доказывают независимость всех зон от более ранней истории. Фактический origin, hashes входных файлов и параметров сохранены в provenance.

## Воспроизведение обычных CSV

```bash
# Скачивает настоящий прогрев/контексты с привязкой к датам CSV,
# исполняет Python и публикует сравнение. Нужен доступ к биржевым REST API.
python3 -m tools.pine_reference.native \
  --directory tradingview_data \
  --output-dir artifacts/local/tradingview-fixed \
  --report reports/parity.json

# Повторное сравнение сохранённых Python observations без сети/пересчёта:
python3 -m tools.pine_reference.native \
  --directory tradingview_data \
  --output-dir artifacts/local/tradingview-fixed \
  --compare-only --report reports/parity.json
```

Если inputs изменены, передать `--parameters parameters.json` и новый `--output-dir`. Для диагностического подмножества предусмотрен `--symbols ETHFIUSDT`. Fixture cache проверяет SHA256 CSV, параметры и Pine hash; observations дополнительно проверяются по engine version и fingerprint fixture. Сырые fixtures/observations сохраняются локально в `artifacts/local/`, компактные отчёты — в `reports/`. Docker API читает `reports/` через readonly bind mount; результаты доступны на `/parity` без пересборки API.

## Полный reference для внутренних оценок

```bash
python3 -m tools.pine_reference.exporter
```

Создаются три отдельные копии в `tools/pine_reference/generated/`: metrics, signals и intrabar. Original не меняется. Исключена финальная визуализация/dispatch, сохранены detector, Research и frozen plan; группы ограничены plot budget. Компиляция debug-копий на TradingView в этой среде не выполнялась. [Пошаговая инструкция по сбору reference](tradingview_capture.md).

1. Открыть `BYBIT:<symbol>.P`, нужный TF и те же 323 inputs; зафиксировать начало истории и параметры.
2. Добавить metrics и signals на один график и экспортировать общий CSV с колонками `PARITY_*`.
3. Если CSV выгружены отдельно, совместить их по UTC time, не теряя строки и identity.
4. Для intrabar использовать отдельную копию и CSV журнала Alerts Log; импорт не заменяет синхронизированное сравнение.

```bash
python3 -m tools.pine_reference.merge metrics.csv signals.csv --output reference.csv
python3 -m tools.pine_reference.replay --input fixture.json --output python.jsonl
python3 -m tools.pine_reference.compare --reference reference.csv --python python.jsonl --tick-size 0.0001 --output reports/full-parity.json
```

Допуски: ATR/EMA relative ≤0.05%; scores median ≤0.5, P95 ≤1.5; levels ≤1 tick, SL/T1 ≤2 ticks; discrete states и сигнальные свечи exact. NA mismatch не игнорируется. Для realtime score P95 ≤2, timestamp matching ≤1500 ms, one-to-one.

## Проверки реализации

173 Python regression/integration test прошёл, включая округление сравнений, десятичную границу T1, видимость native plots, NA/missing data, historical REST timestamps и пересчёт старых checkpoints. Эти тесты дополняют реальное сравнение CSV, но не заменяют недостающие reference. Эксплуатационные проверки и ограничения: [implementation_report.md](implementation_report.md).

Docker обновлён: 7 контейнеров healthy, 4/4 инструмента пересчитаны версией `1.15.2-interpreter.2` с прежним `history_start`. Приложение HEALTHY; шесть страниц проверены без JavaScript errors. Краткий результат: [deployment.json](../reports/deployment.json).

## Строгая проверка полного reference

`tools.pine_reference.compare` теперь проверяет наличие каждой колонки с обеих сторон: отсутствие Python-сигнала не считается нулём. Значения сигналов должны быть 0/1 или JSON bool; пустые, ошибочные и нечисловые значения приводят к FAIL. Для численных метрик NA допустим, но NA mismatch — FAIL; полностью пустая метрика получает NOT_OBSERVED и сохраняет общий UNVERIFIED.

Дубликаты comparison keys и неоднозначная принадлежность инструменту отклоняются. Анонимный CSV допустим только для одного Python-инструмента. Сначала резервируются точные timestamp matches, затем для realtime применяется ближайшее свободное наблюдение в пределах 1500 ms. Сопоставление one-to-one; неиспользованные Python-записи внутри окна reference означают FAIL. Прогрев до окна сравнения не считается лишним наблюдением. Поля `bar_start` и `event_time` заданы в миллисекундах; TradingView `time` поддерживает Unix seconds. ISO-время требует timezone.

В отчёте отдельно указаны отсутствующие колонки reference/Python, NA mismatch, недопустимые значения, численные пары, positive event coverage и первые расхождения с timestamp. `agreement_percent` — точное равенство наблюдений, включая совпадающие NA, а PASS численных метрик по-прежнему определяется прежними допусками. Все проценты находятся в диапазоне 0–100.

CLI по умолчанию пишет `reports/full-parity.json`, сохраняя отчёт проверенных обычных CSV отдельно. Запись атомарная; входные файлы нельзя использовать как output. Ошибка формата создаёт INVALID_INPUT и возвращает ненулевой exit code, поэтому старый PASS не остаётся результатом неудавшегося запуска.

Проверены **173 Python tests**, включая **25 parity tests**. Повторное сравнение сохранённых Python observations со всеми семью реальными CSV не изменило результаты: 2993 свечи, 124 положительные метки, observed PASS / full UNVERIFIED. [Проверки comparator](../reports/comparator-validation.json). Новых TradingView reference для внутренних оценок или intrabar этим не создано.

Те же 25 parity tests прошли внутри Docker. API обновлён, все 7 сервисов healthy, работают 4 engines; опубликованный отчёт обычных CSV сохранён.

## Подготовка capture, 2026-09-21

Добавлены intrabar Pine recorder и импорт CSV журнала оповещений, а metrics/signals получили отдельные названия и колонки происхождения данных. Порядковые номера сохраняют обновления с одинаковым timestamp; пропуски, переполнение и неполный диапазон отражаются в отчёте. Импорт не присваивает parity PASS. [Инструкция для TradingView](tradingview_capture.md).

Прошли **200 Python tests** и **52 parity tests в новом Docker-образе**. API обновлён; `/setups`, `/parity` и `/api/parity` отвечают HTTP 200, семь контейнеров healthy. Текущая локальная конфигурация уже выбирает весь universe (`MAX_SYMBOLS=0`, 771 инструмент), поэтому приложение сообщает **RECOVERING** во время инициализации; прежнее измерение на четырёх инструментах не описывает этот запуск. Конфигурация universe в этой работе не менялась. [Отчёт проверки инструментов capture](../reports/capture-tooling-validation.json).

Исходник 1.15.2 и опубликованный отчёт обычных CSV не изменены. Новых reference из TradingView ещё нет; компиляция диагностических копий на TradingView и full parity остаются UNVERIFIED. Версия 1.16.5 в этот набор проверки не включена.

## Первый полный CSV ETHFI 30m, 2026-09-21

Получен `tradingview_data/full_reference/BYBIT_ETHFIUSDT.P, 30_caaff.csv`: 8758 строк, 45 внутренних метрик, 26 сигналов, metadata обеих копий. Начало расчёта `1735689600000` (2025-01-01 UTC); первый экспортированный bar_index 21398. На всех закрытых свечах metadata metrics/signals согласованы. Последняя свеча имеет confirmed=0, и объём двух одновременно работающих копий уже различается; она исключена.

Python replay выполнен на **30155 свечах от того же origin**, включая 21398 свечей прогрева. OHLCV окна взяты из CSV; более ранняя история и внешние request-контексты — из Bybit/Binance. Inputs пока предполагаются стандартными, подтверждение пользователя не получено. Исторический replay подаёт контекстные свечи по мере их закрытия; эквивалентность с прежним полным потоком проверена отдельно.

Итог **FAIL**: сопоставлены все 8757 закрытых строк, пропущенных или лишних записей нет. По установленным допускам прошли **15/45 метрик** и **23/26 сигналов**. Среди прошедших — OHLC, ATR, EMA, Level, Formation, Execution, Geometry, Exhaustion, MAE, Continuation, Dynamic Support. Это не означает точного совпадения каждой строки для метрик с численным допуском.

Расхождения сигналов: LONG WATCH ENTRY (13 событий TV / 14 Python), BRONZE (5 / 6), AVG SETUP >=70 (42 / 41). Часовые return1h/6h/24h и NATR в CSV повторяют итог текущего часа на обеих его половинах; все 4378 первых получасовых значений точно совпадают со **следующей** Python-строкой. Это установленная закономерность данных, но причина ещё не подтверждена. Кроме неё есть расхождения volume24h, HTF, состояний и других показателей. Логика lookahead engine для подгонки не менялась.

Подробности: [полный отчёт](../reports/full-parity.json), [диагностика](../reports/full-parity-diagnostics.json). `/api/parity` и страница `/parity` предпочитают полный отчёт, когда он существует. Прежний результат ограниченного сравнения семи CSV сохранён в `reports/parity.json`; он не отменяет новые расхождения. Intrabar остаётся UNVERIFIED.

```bash
python3 -m tools.pine_reference.full \
  --input 'tradingview_data/full_reference/BYBIT_ETHFIUSDT.P, 30_caaff.csv' \
  --output-dir artifacts/local/full-ethfi \
  --report reports/full-parity.json
```

`--parameters` задаёт inputs для replay; `--parameters-confirmed` указывается только после независимого подтверждения соответствия настроек TradingView. Сырые fixtures и Python observations хранятся локально в `artifacts/local/full-ethfi`.

## Диагностика HTF и конвертации объёма, 2026-09-21

Добавлен воспроизводимый анализ сохранённых observations без повторного полного replay:

```bash
python3 -m tools.pine_reference.alignment \
  --reference 'tradingview_data/full_reference/BYBIT_ETHFIUSDT.P, 30_caaff.csv' \
  --actual artifacts/local/full-ethfi/python.jsonl \
  --output reports/htf-alignment-diagnostics.json
```

[Отчёт диагностики](../reports/htf-alignment-diagnostics.json) содержит hashes обоих входов, сравнение исходных timestamps и отдельную гипотезу проекции результата конца часа на его предыдущие свечи. Он не изменяет observations или полный parity-отчёт. Постоянные ряды отделены от примеров, позволяющих различить варианты привязки; отсутствующий конец часа не заменяется соседней доступной строкой.

На 4378 ранних получасовых свечах return1h/6h/24h и NATR совпадают с Python в конце того же часа; на 4378 закрытиях часа совпадают исходные timestamps. Для return6h один ранний пример совпадает в обеих привязках. Последняя закрытая строка CSV не имеет закрытия своего часа в Python observations, поэтому для гипотезы она отмечена unavailable. Все 8757 строк имеют Python-наблюдение на исходном timestamp.

После такой диагностической проекции отношение TV/Python volume24h постоянно с допуском 1e-9 внутри **175 из 184 UTC-дней**. В первые дни это 0.99986, 0.99973, 0.99990. **12–20 сентября** отношение меняется и внутри дня; единый дневной множитель эти участки полностью не объясняет. Это согласуется с гипотезой конвертации, но не доказывает фактический FX rate: курс в исходном CSV отсутствует. Interpreter пока возвращает NA для `request.currency_rate`, и исходный Pine применяет stablecoin fallback 1:1. TradingView описывает [currency_rate как дневной курс](https://www.tradingview.com/pine-script-docs/concepts/other-timeframes-and-data/#requestcurrency_rate).

Исторический `lookahead_off` по [документации TradingView](https://www.tradingview.com/pine-script-docs/concepts/other-timeframes-and-data/#lookahead) обновляется в конце HTF-периода. Поэтому наблюдаемый сдвиг недостаточен для изменения production-логики. Подготовлена дополнительная экспортная копия **PARITY contexts** с исходными расчётами, off/on-измерениями и реальным курсом конвертации; [порядок следующей выгрузки](tradingview_capture.md#следующая-выгрузка-время-htf-и-курс-конвертации). Компиляция этой новой копии на TradingView ещё не проверена.

Full parity остаётся **FAIL**, intrabar — **UNVERIFIED**; версия engine и исходный Pine не изменены. Статус нового отчёта `DIAGNOSTIC_ONLY` не является результатом приёмки.

Проверки: **229 Python tests**, **69 parity tests в изолированном Docker-контейнере**; все семь сервисов локального стенда healthy, `/api/parity` возвращает FAIL и engine `1.15.2-interpreter.2`. Здоровье контейнеров не означает завершённую инициализацию всего universe. Изменения относятся к диагностическим инструментам и документации; работающие сервисы не пересоздавались.

## Новый contexts + metrics CSV, 2026-09-22

Пользователь подтвердил **настройки по умолчанию** для `BYBIT_ETHFIUSDT.P, 30_22f96.csv`. SHA-256: `35fdf5b66bc7bd63cede1d6ddc8cf898c3e5fe2f5370d9386d531ff8aecdeb41`. Файл содержит 10 118 строк; последняя открытая свеча исключена. Origin остаётся 2025-01-01 UTC; первый bar_index 20072. Replay выполнил 30 189 свечей, включая 20 072 свечи прогрева, и сопоставил **все 10 117 закрытых строк** без пропусков или лишних наблюдений.

Результат: **45/45 метрик PASS** по прежним допускам. FSM/ACTION, direction/generation, execution paths, gates, уровни, SL/T1 и R:R совпали точно, включая NA. BTC Shock P95 ошибки ≈7.1e-15; AVG SETUP ≈2.84e-14. На части строк сохраняются небольшие различия float/биржевого объёма: PASS не означает побитовое равенство всех чисел. [Машинный отчёт](../reports/context-parity.json), [контекстные доказательства](../reports/context-capture-evidence.json).

Новая выгрузка на всех закрытых строках соответствует `lookahead_off`; timestamps requested-часа не выходят за конец chart-свечи. На общем участке предыдущая выгрузка совпадает с новым диагностическим `lookahead_on` в 4378 из 4379 различимых случаев. Причина отличия исходного старого capture не установлена; история не сдвигалась для получения PASS, старый FAIL сохранён в `full-parity.json`.

Обнаружена ошибка интерпретатора: он заимствовал вычисленный на 30m графике `effectiveBtcShockLookback=2`. Pine пересчитывает зависимости внутри requested context 15m, где Auto-профиль выбирает 3. Это соответствует [семантике declared variables в request.security](https://www.tradingview.com/pine-script-docs/concepts/other-timeframes-and-data/#declared-variables). Версия `.3` исполняет необходимые неизменяемые объявления в собственном контексте запроса; неподдержанные mutable-зависимости явно отклоняет. Исходник Pine и thresholds не менялись.

Курс USDT→USD доступен во всех 10 117 строках, диапазон 0.99842–1.00048; quote-volume × rate точно объясняет экспортированный USD volume. Replay использует **отдельно экспортированный `PARITY_CTX_quote_usd_requested`** как внешний вход, с ограниченными интервалами доступности. Курс не выводится из сравниваемых метрик и не распространяется за пределы выгрузки. До её начала и в live worker действует исходный fallback; live FX feed этим не реализован. Native exchange warmup/request data также остаются отдельным источником.

В CSV нет signal columns. Их статус — MISSING_REFERENCE, intrabar — UNVERIFIED, общий статус — **UNVERIFIED** при `metrics_status=PASS`. Старые сигналы не объединяются с несовместимым HTF capture. `/api/parity` предпочитает новый отчёт; явный `PARITY_REPORT_PATH` и сохранённый database result сохраняют приоритет.

Воспроизведение (нужен локальный cache предыдущего native replay):

```bash
python3 -m tools.pine_reference.context_capture \
  --input 'tradingview_data/full_reference/BYBIT_ETHFIUSDT.P, 30_22f96.csv' \
  --cached-fixture artifacts/local/full-ethfi/fixture.json \
  --output-dir artifacts/local/contexts-ethfi \
  --report reports/context-parity.json --parameters-confirmed
```

Fixture сохраняет OHLCV, native contexts и bounded FX inputs. Контекстные хвосты догружаются с публичных Bybit/Binance API. Оптимизированы rolling SMA/sum и уже прогретые RMA/ATR; порядок сложения, NA и границы истории проверены differential-тестами. Полный suite: **256 passed**.

API и engine `.3` развёрнуты локально; 71 профильный тест прошёл внутри нового Docker-образа. `/setups`, `/parity`, `/api/parity`, `/api/health` отвечают HTTP 200, семь контейнеров healthy. Весь universe (774 инструмента, TF30) ещё прогревается: приложение RECOVERING, нагрузочная приёмка не заявляется. [Проверка развёртывания](../reports/context-deployment.json).

## Intrabar capture, 2026-09-22

Обработан `TradingView_Alerts_Log_2026-09-22_0c635.csv`, SHA-256 `63926d14ea180c0d43bb456287388be3aeb6992799f954c0d477b13bf4304476`. [Отчёт](../reports/intrabar-capture.json): 48 пакетов, 253 последовательных обновления ETHFIUSDT.P 5m, 1121.714 секунд, 0 dropped/duplicates. Три закрытые свечи, две полные; 16 соседних обновлений имеют одинаковый timestamp. На каждом закрытии присутствуют два исполнения — они сохранены, а не удалены как дубликаты.

Ранняя версия encoder испортила два enum inputs. Импорт восстанавливает только единственный допустимый вариант, полностью соответствующий известному преобразованию; произвольные строки и частично повреждённые значения отклоняются. Raw metadata остаются в локальном import, исправления перечислены в отчёте, исходный CSV неизменен. Все 298 переданных параметров после восстановления соответствуют defaults. Регистратор revision 2 устраняет ошибку; исходный детектор и engine version не менялись.

Отдельный `trace_prices` исполняет исходные выражения ATR/EMA с native 5m warmup (20 500 свечей от записанного origin), затем подаёт captured OHLCV в порядке `seq`. Повторные закрытия не удваивают историю TA. **3/3 метрики PASS на 253 обновлениях**, прежний относительный допуск 0.0005. Максимальная относительная ошибка ATR 1.40e-14, EMA fast 3.15e-16, EMA slow 7.83e-16. OHLCV являются входом, не результатом этой проверки.

Direction=0 и все 26 signal flags равны нулю на всём capture. **Полная intrabar parity остаётся UNVERIFIED**: синхронных requested BTC/HTF/lower-TF/FX inputs нет, поведение setup FSM/varip/scores/signals не проверено. Generic comparator по timestamp неприменим к этой записи напрямую. Исторический отчёт 45/45 остаётся отдельным результатом; runtime/API не изменены. [Воспроизведение и дальнейшие ограничения](tradingview_capture.md#полученный-intrabar-журнал-2026-09-22).

Проверки: **269 tests passed**. Регрессии покрывают строгое восстановление enum, сохранение одинаковых timestamps/повторных закрытий, однократное добавление свечи в историю ATR/EMA, отказ при разрыве warmup/trace и FAIL при изменённом reference.

### Intrabar da14b: recorder revision 2

Новый [отчёт](../reports/intrabar-capture-da14b.json) относится только к `TradingView_Alerts_Log_2026-09-22_da14b.csv`, SHA-256 `5302bfa34b253682ca42e3ee8ab0834bf475d76b45475ffa03136878f6c40d0f`. Recorder revision 2 исполняется и передаёт корректные defaults без восстановления enum. Это наблюдаемое исполнение версии регистратора; отдельного TradingView compiler log нет.

Получены пакеты 2–120 и обновления 2–543: отсутствует начальный пакет с одним обновлением, внутри полученного участка пропусков нет. 542 наблюдения за 2898.042 секунд, десять закрытых свечей (девять полных), 35 соседних пар с одинаковыми timestamps, десять повторных confirmed executions. Общий `sequence_contiguous=false` сохранён. Новое поле `received_sequence_contiguous=true` описывает только имеющийся участок; `complete_bars` теперь учитывает его полноту независимо от отсутствующего префикса. Внутренний разрыв или reported drops по-прежнему блокируют ограниченный price replay.

**3/3 ATR/EMA метрики PASS на всех 542 обновлениях** при прежнем допуске 0.0005. Native warmup — 20 562 свечи. Максимальные относительные ошибки: ATR 1.61e-14, EMA20 3.19e-16, EMA50 1.28e-15. Начальный пропуск не реконструируется: TA использует историю закрытых свечей и текущий captured OHLCV, без intrabar накопления. Это разрешение не распространяется на FSM/varip/AVG SETUP или полный replay.

Direction=0, все 26 сигналов отсутствуют. **Полная intrabar parity — UNVERIFIED**, внешние request contexts не записаны. Предыдущий отчёт сохранён отдельно, production engine/API и Pine-код этим разбором не изменены.

Проверки: **272 tests passed**, включая различение отсутствующего префикса и внутреннего разрыва и сохранение полного статуса UNVERIFIED при ограниченном TA PASS.

## Intrabar pool ad4e4, 2026-09-24

[Машиночитаемый отчёт](../reports/intrabar-pool-ad4e4.json) и исходный `tradingview_data/intrabar/TradingView_Alerts_Log_2026-09-24_ad4e4.csv` сохранены отдельно от прежних проверок. SHA-256 CSV: `f42e21e88d130e85a6512db0a7d7433ddfc328e1c060fb3559ee0c54faeed691`. Исходный Pine 1.15.2 и его hash не изменены.

Получено **1815 пакетов, 296 отдельных symbol/run сессий TF30, 13 551 исполнение**, без дублирующихся пакетов. Все 298 переданных inputs соответствуют defaults. Общий временной диапазон — **01:24:43.861–01:27:12.007 UTC** (148.146 s); закрытых и полных свечей **0**. Активные сетапы наблюдались в 55 сессиях, на 3282 обновлениях. Пять сессий не содержат начальное обновление. Переполнение bounded recorder потеряло **32 обновления**: AAVE 15, AERO 14, ALLO 3. Данные не дополнялись искусственными executions.

Все сессии записаны **recorder v2**, поэтому request contexts не предоставлены. Label одинаковый и содержит старое `ETHFI-5m`, но реальные metadata однозначно определяют каждую монету и TF30. В 89 сессиях записанный `bar_index` не соответствует непрерывной временной сетке от `history_start`; это отдельный blocker для строгого stateful replay, а не основание переписывать индексы.

Ненулевые флаги:

| Семейство | Положительных обновлений | Сессий | Наблюдаемых переходов 0→1 | Положительных состояний после неизвестного префикса/пропуска |
|---|---:|---:|---:|---:|
| AVG SETUP >= 70 | 548 | 9 | 1 | 9 |
| EXECUTION QUALITY >= 65 | 170 | 6 | 4 | 2 |

Это флаги после расчёта, в том числе latched состояния. **548/170 не являются количеством alerts или независимых событий.** Положительное первое наблюдение не доказывает пересечение порога именно в этот момент. Остальные 24 семейства в журнале не наблюдались; их parity не подтверждена.

### Пакетная TA-диагностика

`trace_pool` подаёт captured OHLCV по `seq` в исходные выражения ATR/EMA20/EMA50. Для native-прогрева загружено до **1000** предшествующих закрытых Bybit свечей (20 × максимальная TA length). Для повторения сохранены исходные native bars и их SHA-256. Начальная рекурсивная seed TradingView не доказана, поэтому результат ограничен статусами **DIAGNOSTIC_MATCH / DIAGNOSTIC_MISMATCH**, без origin/full parity PASS. Прежний `trace_prices` по умолчанию сохраняет строгий прогрев от исходного `history_start`.

- **281 сессия / 13 040 обновлений: DIAGNOSTIC_MATCH** по всем трём TA-метрикам.
- **12 сессий / 286 обновлений: DIAGNOSTIC_MISMATCH**. Перечень: ADBE, AEHR, AMAT, BX, EWT, KSTR, LLY, NVDL, SMH, TSM, TTWO, XLE (везде USDT.P).
- **3 сессии / 225 полученных обновлений: NOT_ELIGIBLE** из-за recorder drops. Эти строки учтены в capture audit, но не получили результат TA parity.

Численный допуск не изменён: max relative error ≤ 0.0005. В bounded-режиме историческая сетка до прогрева не предполагается непрерывной; согласованность `bar_index` и времени внутри самого capture проверяется. Строгий replay от origin по-прежнему отклоняет несовпадение его сетки.

На 12 расходящихся сессиях проведён отдельный эксперимент: из сохранённого native warmup исключены бары с volume=0, остальные timestamps/OHLCV сохранены. **Все 12 HYPOTHESIS_MATCH**: максимальная относительная ошибка ATR около 1.89e-16, EMA — до 2.46e-12. Это сильное свидетельство различия состава баров, но не доказательство состава истории TradingView. Основные 12 DIAGNOSTIC_MISMATCH сохранены; production feed, формулы и thresholds не изменены. Следующая проверка — обычный исторический TradingView OHLCV экспорт, прежде всего EWTUSDT.P 30m, через нулевые интервалы.

Raw import: `artifacts/local/intrabar-ad4e4/`. Native fixtures, результаты и начальный отчёт с отклонённой исторической сеткой: `artifacts/local/intrabar-ad4e4-ta-evidence.tar.gz`. Воспроизведение:

```bash
python3 -m tools.pine_reference.trace_pool \
  --input tradingview_data/intrabar/TradingView_Alerts_Log_2026-09-24_ad4e4.csv \
  --output-dir artifacts/local/intrabar-pool-new --native-ta
```

Без `--native-ta` выполняется только audit, без сети. `--resume` повторно использует native fixtures того же входного SHA/engine; исходный CSV не меняется. Concurrency ограничена 1..5, default 3. Exit 1 означает наличие TA mismatch, exit 2 — ошибку импорта/запуска; exit 0 сам по себе не является parity PASS. Недоступные данные и исключённые сессии остаются отдельными статусами в отчёте.

На `/parity` добавлен отдельный intrabar-раздел; `GET /api/parity/intrabar` возвращает сводку, `?include_sessions=true` — подробности по всем 296 сессиям. Исторический отчёт 45/45 и его статус не заменяются новым capture. Полная intrabar parity остаётся **UNVERIFIED**: нужны v3 request results, закрытия свечей, непрерывная запись и согласованное начальное состояние. [Инструкция следующей записи](tradingview_capture.md#текущий-шаг-после-pool-отчёта-ad4e4-2026-09-24).


## Intrabar 52214: recorder v3 (2026-09-24)

[Отчёт](../reports/intrabar-pool-52214.json), исходный CSV `tradingview_data/intrabar/TradingView_Alerts_Log_2026-09-24_52214.csv`, SHA-256 `881a819f3d3e2dc272b0d0e859a18a8cc0c406d9b31d21304a03996f5358ed03`.

- 8 инструментов: ARPA, AVAX, DOGE, ETC, INJ, SAHARA, SOL, ZEC; TF30, recorder v3, captured inputs defaults.
- 1016 пакетов, 3454 executions, 3454 синхронных request observations, дублей пакетов и отсутствующего начала seq нет.
- Окно 14:25:32.085–15:15:00.616 UTC: 49 минут 28.531 секунды. 16 закрытых свечей по всем инструментам, из них **8 полных** (14:30–15:00 UTC, по одной на инструмент).
- 121 dropped execution, только batch 2: SAHARA 23, INJ 19, DOGE 18, AVAX/ZEC/ETC по 17, SOL 10. ARPA без потерь. Глобальное состояние 7 сессий неполно; последующие свечи могут быть полными локально.
- Исправлен подсчёт coverage: полнота определяется по каждой свече отдельно. Потери, исходные seq и запрет полного stateful replay сохраняются. Тесты покрывают начальный пропуск, локальные пропуски и отсутствие подходящей непрерывной части.
- `--clean-bar-ta` выбирает непрерывную часть после последнего seq-gap, начиная с полной свечи. Номера seq не перенумеровываются. Native-прогрев до 20× наибольшей длины ATR/EMA — гипотеза initial seed; исходная сессия не объявляется восстановленной.
- **8/8 DIAGNOSTIC_MATCH, 2868 обновлений** для ATR/EMA20/EMA50. 586 полученных стартовых обновлений исключены из этой отдельной проверки. Максимальная относительная ошибка среди всех пар около 3.023e-9 при прежнем допуске 0.0005. Проверка охватывает repeated confirmed executions и переход на следующую свечу.
- Для ARPA исходный history_start/bar_index не соответствует непрерывной сетке (8 баров разницы); bounded TA допускает неизвестную историю, строгий origin replay не ослаблен.

Положительные флаги присутствуют у шести семейств: LONG ARMED, LONG ARMED LOST, LONG REVERSAL RISK, BREAKOUT, AVG SETUP >=70 и EXECUTION QUALITY >=65. Это reference-наблюдения, не проверенная Python signal parity и не счётчик доставленных уведомлений. Полный intrabar статус остаётся **UNVERIFIED**: начальное persistent/varip состояние и native request calculations не подтверждены.

Воспроизведение (новый output-dir):

```bash
python3 -m tools.pine_reference.trace_pool \
  --input tradingview_data/intrabar/TradingView_Alerts_Log_2026-09-24_52214.csv \
  --output-dir artifacts/local/intrabar-52214-check --clean-bar-ta
```

Evidence: `artifacts/local/intrabar-52214-clean-ta/` (warmup JSON, Python JSONL, TA reports). `/api/parity/intrabar` теперь показывает 52214; прежний ad4e4 сохранён отдельным JSON. Исходный Pine, правила и production feed не менялись. Повторять запись только из-за отсутствия 160 минут не требуется; дальнейший capture нужен для покрытия без потерь, а historical CSV — для согласования истории.


## Intrabar 14573 (2026-09-25)

[Отчёт](../reports/intrabar-pool-14573.json), CSV `tradingview_data/intrabar/TradingView_Alerts_Log_2026-09-25_14573.csv`, SHA-256 `2d0bc3bd5ab41e5e4f9cfcd92feb2fcad511e132a1c8c22229e6408c4a0d01ef`.

5 сессий / инструментов: AVAX, BTC, ETH, SOL, ZEC, TF30, recorder v3, captured inputs defaults. Получено 1797 пакетов, 4609 executions с request observations, 30 закрытых свечей, 25 полных. Время 2026-09-24 23:58:38.984 — 2026-09-25 02:44:20.654 UTC (165 минут 41.670 секунды). Внутренних seq-пропусков и reported drops нет; однако у каждой сессии отсутствует начало.

| Символ | Первый batch | Первый seq | Отсутствует начальных executions |
|---|---:|---:|---:|
| AVAX | 48 | 194 | 193 |
| BTC | 48 | 198 | 197 |
| ETH | 48 | 191 | 190 |
| SOL | 49 | 193 | 192 |
| ZEC | 48 | 193 | 192 |

`sequence_contiguous=false` сохраняется, несмотря на `received_sequence_contiguous=true`. Отсутствующие 964 executions не восстановлены догадками. Начало alerts 23:41:23–23:41:26 UTC; все history_start/bar_index соответствуют непрерывной временной сетке, но это не доказывает одинаковый historical OHLCV или initial state. В 3 сессиях есть активные setup; положительный сигнал в этой выборке — только EXECUTION QUALITY >=65 у AVAX (44 положительных флага, 1 наблюдаемый переход 0→1).

**TA: 5/5 DIAGNOSTIC_MATCH, все 4609 полученных обновлений**, ATR/EMA20/EMA50, native warmup до 20× наибольшей длины, прежний допуск 0.0005. В отличие от 52214, стартовая полученная часть не вырезалась: внутри неё нет потерь.

**Request components: 5/5 DIAGNOSTIC_MATCH, все 4609 обновлений.** Новый `trace_components.py` исполняет только 11 выбранных исходных AST-присваиваний и их чистые helper-функции: выходы `volume24hProxy`, `mtfTrendQualityLong`, `htfBaseQualityLong`, `btcShockScore`. Зависимости — captured parameters, quote/volume metadata и записанные request results. Текущие reference scores не подаются обратно на вход. История/requests/stateful calls в выбранных выражениях запрещены; остальные 41 метрика и 26 сигналов здесь не проверяются. Погрешность четырёх выходов не превышает 1.422e-14, допуски не менялись.

Результаты запросов здесь **поставлены как входы**, поэтому совпадение последующих формул не подтверждает расчёт самих `request.security`/currency/lower-TF на native feeds. Полный intrabar статус **UNVERIFIED**: missing prefix, initial state, FSM/alerts и native requests остаются отдельными gates.

```bash
python3 -m tools.pine_reference.trace_pool \
  --input tradingview_data/intrabar/TradingView_Alerts_Log_2026-09-25_14573.csv \
  --output-dir artifacts/local/intrabar-14573-check --native-ta --request-components
```

`--request-components` доступен отдельно от TA: ему не нужен warmup, каждое полученное исполнение проверяется независимо. v1/v2 или повреждённые observations отклоняются. Exit 1 при mismatch TA либо request components; успешная диагностика не означает full parity PASS. Evidence: `artifacts/local/intrabar-14573-pool/` с native warmup, TA и component Python JSONL. Текущая страница Parity/API показывает 14573; 52214/ad4e4 сохранены отдельно. По решению пользователя от 2026-09-25 проверка полного replay этой сессии пропущена: начало записи недоступно. Повторный запрос первых пакетов снят; результаты диагностики сохраняются, полный intrabar статус остаётся UNVERIFIED. Остальная разработка продолжается, см. [границы проверки](tradingview_capture.md).
