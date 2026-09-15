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

Создаются две отдельные копии в `tools/pine_reference/generated/`: metrics и signals. Original не меняется. Исключена финальная визуализация/dispatch, сохранены detector, Research и frozen plan; группы ограничены plot budget. Компиляция debug-копий на TradingView в этой среде не выполнялась.

1. Открыть `BYBIT:<symbol>.P`, нужный TF и те же 323 inputs; зафиксировать начало истории и параметры.
2. Добавить каждую debug copy и экспортировать chart data с колонками `PARITY_*`.
3. Совместить metrics/signals CSV по UTC time, не теряя строки и identity.
4. Для intrabar нужен отдельный поток наблюдений TradingView по timestamp.

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
