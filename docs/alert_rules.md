# Как создавать alert-правила

Правило проверяет snapshot движка и при совпадении создаёт уведомление Telegram. Это не торговая заявка. Условия — данные, пользовательские выражения не исполняются как Python.

## Порядок создания

1. Откройте `/alerts` → «Новое правило», задайте название. Новое правило по умолчанию выключено.
2. В группе `AND` выберите `timeframe IN` и нужные TF. Добавьте направление, метрики или конкретное событие.
3. Для фиксированных значений используйте presets. `==`, `!=`, `changed_to` выбирают одно значение; `IN`, `NOT IN` — несколько. Для множественного выбора: Ctrl/⌘ + клик, диапазон — Shift. Пустой список недопустим.
4. Числа вводите без кавычек и `%`, дробную часть — через точку. У `BETWEEN` две отдельные границы, у `changed` значения нет.
5. Выберите режим, частоту и cooldown. «Проверить текущие setups» показывает текущие совпадения и **не отправляет Telegram**. Preview не воспроизводит прошлое состояние, crossings и фактическую доставку.
6. Сохраните правило. Для реальной работы включите его. Telegram должен быть настроен на сервере, данные инструмента — готовы и актуальны.

Выбор TF в правиле **не включает его расчёт**. Если сервер работает с `ACTIVE_TIMEFRAMES=30`, правило для `5` не получит 5m snapshot. Настройка потоков: [operations.md](operations.md).

## Как вводить значения: форма и JSON

| Тип | В форме | В JSON/API | Неправильно |
|---|---|---|---|
| Число | `70`, `65.5`, `1.5` | `"value": 70` | `"70"`, `70%`, `65,5` |
| Целое | `7` | `"value": 7` | `7.5`, `"7"` |
| Текст | `ETHFIUSDT` без кавычек | `"value": "ETHFIUSDT"` | Кавычки в обычном текстовом поле становятся частью искомой строки |
| Готовый текст | Выберите `LONG` | `"value": "LONG"` | `long` или `"\"LONG\""` |
| Boolean | «Да (true)» / «Нет (false)» | `"value": true` / `false` | Строка `"true"`, число `1` |
| Несколько presets | Выберите несколько строк | `"value": ["LONG", "SHORT"]` | Одна строка `"LONG, SHORT"` в API |
| Диапазон | Нижняя `60`, верхняя `80` | `"value": [60, 80]` | Переставленные границы `[80, 60]` |
| Свободный список | Через запятую или JSON-массив | Типизированный массив | Элемент с запятой задавайте JSON: `["A,B", "C"]` |

Текст сравнивается с учётом регистра и пробелов. Обычное текстовое поле не снимает кавычки. Для свободного списка можно ввести `ETHFIUSDT, BTCUSDT` либо `["ETHFIUSDT", "BTCUSDT"]`. В JSON названия полей, операторы и текст всегда в двойных кавычках; числа и boolean — без них. Блок «JSON условия» показывает фактическое условие для сервера.

Особые случаи:

- `timeframe` — **строка**, хотя выглядит как число: `"5"`, `"30"`, `"60"`, `"240"`, `"D"`. Форма сохраняет тип автоматически.
- `fsm` — **целое 0–8**. Текст состояния хранится в `setup_state`. Колонка «Family / State» показывает текст, но это не делает `fsm` строкой.
- `hard_gates` — **строка** `"0/5"` … `"5/5"`, не число.
- `metrics.direction` — числовой код `-1 / 0 / 1`; `direction` — текст `SHORT / NONE / LONG`.
- Отсутствующее значение `null` не означает ноль, false или текст `"n/a"`. Обычные сравнения, включая `!=` и `NOT IN`, не совпадают при отсутствующей текущей метрике. `changed` может заметить переход в/из отсутствующего значения. `target_freshness == "n/a"` проверяет реальный текстовый preset.

## Поля верхнего уровня

| Поля | Тип / единицы | Пример |
|---|---|---|
| `symbol` | Текст, Bybit symbol без `.P` | `== ETHFIUSDT` |
| `exchange` | Preset `BYBIT` | `== BYBIT` |
| `timeframe` | Строка: минуты или D/W/M | `IN 30, 5` |
| `direction` | `LONG`, `SHORT`, `NONE` | `== LONG` |
| `action` | Текст текущего действия; все точные варианты в preset | `== CHECK DOM/TAPE → ENTRY` |
| `setup_state` | Текст состояния, список ниже | `IN WATCH, APPROACH` |
| `fsm` | Целочисленный код стадии | `>= 4` |
| `family` | `CONTINUATION`, `ATH / OPEN SKY`, `BOUNCE WATCH`, `—` | `== CONTINUATION` |
| `avg_setup`, `continuation`, `formation`, `execution`, `geometry`, `context`, `level` | Число 0–100 | `avg_setup >= 70` |
| `mae`, `exhaustion`, `btc_shock` | Число, риск 0–100; больше = выше риск | `mae <= 45` |
| `approach` | Целое 0–9 | `>= 7` |
| `price`, `trigger_zone`, `sl`, `t1` | Число в единицах цены инструмента | `price > 1.25` |
| `rr` | Числовое отношение reward/risk | `>= 1.5` |
| `distance` | Число, расстояние в ATR | `<= 1.5` |
| `candidate_path`, `trigger_path`, `risk_path`, `fsm_path` | Текстовый execution path | `IN COMPRESSION, PRE-BREAK` |
| `btc_regime` | `BULL`, `BEAR`, `MIXED` | `!= BEAR` |
| `hard_gates` | Текст `0/5` … `5/5` | `== 5/5` |
| `confirmed`, `fresh_trigger`, `addon_gate`, `in_play` | Boolean | `confirmed == true` |
| `active_plan_health` | `HEALTHY`, `DEGRADED`, `REDUCE`, `EXIT`; может отсутствовать | `IN DEGRADED, REDUCE` |
| `target_freshness` | `FRESH`, `BROKEN/RETEST`, `CONSUMED / UNRESOLVED`, `n/a` | `== FRESH` |
| `event` | Одно проверяемое событие среди signals текущего snapshot | `IN BRONZE, STRONG` |
| `signals` | Список событий текущего snapshot | `contains_any BRONZE, STRONG` |
| `last_signal` | Последнее известное событие; сохраняется после его появления | `== BRONZE` не означает новый BRONZE |
| `blockers` | Список текстовых токенов причин | `contains MAE` |
| `setup_age` | Целое, возраст setup в минутах | `<= 30` |
| `event_time`, `bar_start`, `received_at` | Целое, UTC Unix timestamp в миллисекундах | `event_time > 1790000000000` |
| `data_health` | `HEALTHY`, `RECOVERING`, `DEGRADED`, `STALE`; совместимость с `FULL_REALTIME`, `KLINE_REALTIME` | `== HEALTHY` |
| `data_quality` | `FULL_REALTIME`, `KLINE_REALTIME` | `== FULL_REALTIME` |
| `parity_status` | Сейчас `UNVERIFIED` | Не подтверждает готовность торговли |
| `setup_generation_id`, `parameter_set_id`, `engine_version` | Текстовые идентификаторы | Значение из JSON snapshot |

Актуальный каталог: `GET /api/alerts/fields`. Форма и серверная проверка используют один каталог; ACTION, setup state и типы Pine-полей выводятся из сохранённого исходника. Неизвестные значения ранее сохранённого правила видны с ошибкой и не заменяются автоматически: исправьте значение или оператор перед сохранением.

### FSM, состояния и пути

Коды `fsm`: `0 NONE`, `1 FORMING`, `2 WATCH`, `3 APPROACH`, `4 ARMED`, `5 TRIGGERED`, `6 RETEST`, `7 READY`, `8 ACTIVE`. Для изменения стадии: `fsm changed_to 4`. Для зрелой стадии: `fsm >= 4`; это само по себе не подтверждает здоровье плана.

`setup_state`: `NO SETUP`, `IN-PLAY`, `BOUNCE WATCH`, `FORMING`, `WATCH`, `WATCH / DEGRADED`, `APPROACH`, `APPROACH / DEGRADED`, `ARMED`, `ARMED / DEGRADED`, `TRIGGERED`, `TRIGGERED / DEGRADED`, `POST-BREAK RETEST`, `POST-BREAK HOLD / OPEN SKY`, `PINE READY`, `ACTIVE / HEALTHY`, `ACTIVE / DEGRADED`, `ACTIVE / INVALIDATED`.

Пути: `WAIT`, `PRE-BREAK`, `DIRECT MOMENTUM`, `COMPRESSION`, `PROTORGOVKA BREAK`, `BREAKOUT`, `BREAKOUT + RETEST`, `BREAKDOWN`, `BREAKDOWN + RETEST`, `OPEN SKY`. Candidate описывает формирующийся маршрут; Trigger — сработавший сейчас; Risk/FSM могут сохранять маршрут. Одинаковые presets не означают одинаковую семантику.

### События и blockers

Готовые значения для `event`, `signals`, `last_signal`:

- `L WATCH`, `S WATCH`, `LONG WATCH ENTRY`, `SHORT WATCH ENTRY`, `MATURED PRE-BREAK ENTRY`;
- `LONG ARMED`, `SHORT ARMED`, `PINE READY LONG`, `PINE READY SHORT`, `BREAKOUT`, `BREAKDOWN`;
- `BRONZE`, `STRONG`, `LONG REVERSAL RISK`, `SHORT REVERSAL RISK`;
- `ACTIVE PLAN DEGRADED`, `ACTIVE PLAN EXIT`, `ADD-ON`;
- `LONG TP HIT`, `SHORT TP HIT`, `LONG ARMED LOST`, `SHORT ARMED LOST`;
- `LONG BOUNCE WATCH`, `SHORT BOUNCE WATCH`, `AVG SETUP >= 70`, `EXECUTION QUALITY >= 65`.

`event IN [BRONZE, STRONG]` означает любое событие. Для **одновременного** наличия обоих: `signals contains_all [BRONZE, STRONG]`. Условия `event == BRONZE AND event == STRONG` не совпадут: один event не может быть двумя строками. `setup_state == PINE READY` и `event == PINE READY LONG` — разные проверки.

Основные `blockers`: `IN-PLAY`, `LIQUIDITY`, `BTC_SHOCK`, `LEVEL`, `QUALITY`, `STRUCTURE`, `APPROACH`, `DISTANCE`, `TARGET_CONSUMED`, `TARGET`, `RR`, `SL`, `EXHAUSTION`, `GEOMETRY`, `CONTEXT`, `MAE`, `TRIGGER`, `STALE_TRIGGER`, `PREBREAK_SAFETY`, `ENTRY`, `NONE`. Snapshot разбивает исходный blockerText по пробелам; без setup встречаются также слова пояснения (`No`, `locked`, `continuation`, `setup` и т. п.). Presets содержат реальные токены; пустой список и `["NONE"]` различаются.

## Исходные Pine-переменные

Выберите «Исходное поле Pine / другое поле…», введите dot path, например `metrics.gateStructure`. Для известных scalar-полей тип подставляется автоматически; boolean и конечные наборы получают presets. Для неизвестного пути выберите тип вручную. Выражения, индексы `[0]` и сравнение поля с другим полем, записанным в строке, не поддерживаются.

| Путь | Тип | Пример |
|---|---|---|
| `metrics.gateLiquidity`, `metrics.gateStructure`, `metrics.gateTrigger`, `metrics.gateBtcShock`, `metrics.gateFreshReadyTrigger`, `metrics.preBreakSafetyAllowed` | Boolean | `== true` |
| `metrics.formationQuality`, `metrics.executionQuality`, `metrics.maeRisk`, `metrics.avgSetup` | Число | `>= 70` / `<= 45` |
| `metrics.mtfTrendQualityLong`, `metrics.dynamicSupportQualityLong`, `metrics.htfBaseQualityLong` | Число 0–100 | `>= 60` |
| `metrics.activeApproachScore`, `metrics.activityCheckCount` | Целое | `>= 7` / `>= 3` |
| `metrics.direction` | Целое `-1, 0, 1` | `== 1` |
| `metrics.setupFsmState` | Целое 0–8 | `changed_to 4` |
| `metrics.entryPath`, `metrics.candidateEntryPath`, `metrics.riskPath` | Preset пути | `== COMPRESSION` |
| `metrics.newLongEntry`, `metrics.setupQualityBronzeSignal` | Boolean события | `== true` |

Полный список известных типов — `extra_fields` в `/api/alerts/fields`, mapping — [pine_mapping.md](pine_mapping.md). Фактические значения смотрите в `GET /api/setups/{symbol}/{tf}`. Некоторые поля бывают отсутствующими; описание типа не гарантирует значение на каждом баре. Неизвестный путь не вычисляется автоматически.

Временная зона из Settings меняет отображение времени графиков и событий. Числовые `event_time`, `bar_start`, `received_at` в правилах по-прежнему сравниваются как UTC Unix milliseconds, без прибавления смещения. Настройка и примеры: [operations.md](operations.md#временная-зона-интерфейса).

## Операторы

| Оператор | Значение справа | Смысл |
|---|---|---|
| `==`, `!=` | Один scalar | Равно / не равно |
| `>`, `>=`, `<`, `<=` | Число | Численное сравнение |
| `IN`, `NOT IN` | Массив scalars | Текущее **одно** значение входит / не входит в список |
| `BETWEEN` | Два числа `[от, до]` | Обе границы включены |
| `contains` | Один текстовый элемент | Элемент списка; для text — подстрока |
| `contains_any` | Непустой массив | Список содержит хотя бы один выбранный элемент |
| `contains_all` | Непустой массив | Список содержит все выбранные элементы, другие допустимы |
| `changed` | Не требуется | Текущее значение отличается от предыдущего |
| `changed_to` | Один scalar | Значение изменилось и теперь равно указанному |
| `crosses_above` | Числовой порог | Предыдущее `<= порог`, текущее `> порог` |
| `crosses_below` | Числовой порог | Предыдущее `>= порог`, текущее `< порог` |

`69 → 70` не выполняет `crosses_above 70`, а `70 → 70.1` выполняет. Исходные Pine-события `AVG SETUP >= 70` и `EXECUTION QUALITY >= 65` используют собственную `>=` семантику и latch; для них выбирайте `event` с готовым названием.

Latched флаг может оставаться истинным на многих intrabar-обновлениях одной свечи. Эти строки в parity-журнале не равны независимым уведомлениям. Для одного уведомления о `EXECUTION QUALITY >= 65` на свечу задайте `event == EXECUTION QUALITY >= 65` через пресет, режим `realtime`, частоту `once_per_bar`. В UI кавычки не нужны; в JSON значение — строка `"EXECUTION QUALITY >= 65"`. Числовое условие `execution >= 65` проверяет текущее значение score и имеет другой смысл, чем исходное Pine-событие.

`AND` — все условия; `OR` — хотя бы одно; `NOT` — отрицание **одного** условия/группы. Группы содержат 1–100 условий, вложенность до 10 уровней; списки — 1–100 значений. Изменения/crossings требуют предыдущего snapshot: без него сами predicates возвращают false, но `NOT` может инвертировать результат.

## Режим и частота

- `realtime` проверяет доступные обновления; `confirmed` — только закрытия свечи. Confirmed сравнивает с предыдущим eligible confirmed snapshot правила, realtime — с предыдущим snapshot.
- `once_per_bar`: максимум одна доставка на правило/версию и exchange/symbol/TF/бар.
- `once_per_generation`: одна на setup generation. Без setup идентификатор `none` общий; режим не подходит для регулярных уведомлений вне setup.
- `first_occurrence`: фронт false→true; для повтора условие должно сначала стать false. Это не «один раз навсегда».
- `repeat`: повтор при следующих совпадениях после cooldown; отдельного таймера отправки нет.
- `cooldown_seconds`: целое 0–604800, отдельно для правила/версии и exchange/symbol/TF. При first_occurrence фронт, подавленный cooldown, не откладывается автоматически до его окончания.

Сохранение создаёт новую версию и отдельное dedupe state. Старые правила автоматически не переписываются. Неверный тип или preset даёт 422; исправьте значение перед сохранением.

## Примеры JSON/API

Это примеры синтаксиса, не рекомендация торговых порогов:

```json
{
  "name": "LONG: качество",
  "enabled": false,
  "conditions": {
    "op": "AND",
    "conditions": [
      {"field": "timeframe", "op": "IN", "value": ["30", "5"]},
      {"field": "direction", "op": "==", "value": "LONG"},
      {"field": "avg_setup", "op": ">=", "value": 70},
      {"field": "execution", "op": ">=", "value": 65},
      {"field": "mae", "op": "<=", "value": 45},
      {"field": "metrics.gateStructure", "op": "==", "value": true}
    ]
  },
  "mode": "confirmed",
  "frequency": "once_per_bar",
  "cooldown_seconds": 60,
  "template": "{symbol} {timeframe} {direction}\nAVG: {avg_setup}; SL: {sl}; T1: {t1}\n{detail_url}"
}
```

Любой milestone без причины MAE/BTC_SHOCK:

```json
{
  "op": "AND",
  "conditions": [
    {"field": "signals", "op": "contains_any", "value": ["BRONZE", "STRONG"]},
    {"op": "NOT", "conditions": [
      {"field": "blockers", "op": "contains_any", "value": ["MAE", "BTC_SHOCK"]}
    ]}
  ]
}
```

Переход в одно из двух состояний: `OR` из `setup_state changed_to "ARMED"` и `setup_state changed_to "PINE READY"`. Для простого нахождения в них достаточно `IN`. Для исходного intrabar crossing: `event == "AVG SETUP >= 70"`, режим `realtime`, частота `once_per_bar`.

## Шаблон Telegram и доставка

Placeholder: `{symbol}`, `{timeframe}`, `{direction}`, `{event}`, `{action}`, `{avg_setup}`, `{formation}`, `{execution}`, `{geometry}`, `{context}`, `{level}`, `{approach}`, `{mae}`, `{exhaustion}`, `{btc_shock}`, `{continuation}`, `{price}`, `{sl}`, `{t1}`, `{rr}`, `{blockers}`, `{detail_url}`. Доступны также другие верхние scalar-поля snapshot. Вложенные `{metrics.gateStructure}`, вычисления и `{rr:.2f}` не поддерживаются. Неизвестный простой placeholder даёт `n/a`. Пустой template использует серверный шаблон.

`{event}` выводит все сигналы snapshot через запятую либо имя правила, если сигналов нет. `{blockers}` — список через запятую. Для ссылки вставьте `{detail_url}`: сервер вычисляет URL, но не дописывает его к произвольному template. В JSON перевод строки задаётся `\n`, в textarea — Enter.

Alerts не ставятся в очередь при replay/recovering/stale/degraded данных. Перед отправкой проверяются готовность feed, возраст уведомления, включённость и версия правила. Поэтому `data_health == STALE` не обходит защиту и не создаёт Telegram-alert о stale feed; инфраструктуру смотрите на `/health`.

429 приводит к retry после `retry_after`; неоднозначный timeout — `uncertain` без автоматического повтора. `/api/alerts/rules/test` не отправляет сообщения. «Отправить test Telegram» и `POST /api/alerts/test-telegram` создают **реальную** отправку. Credentials не входят в rule JSON и отсутствуют в репозитории.
