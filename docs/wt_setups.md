# WT_SETUPS — Weighted Trading 1.6.4

Исходник пользователя сохранён без изменения в `reference/WT_Setups_1.6.4.pine`.
Он исполняется существующим Pine AST-исполнителем через отдельный WT runtime;
формулы T1–T4, профили, уровни, Entry Quality, ACTION и Broke confluence берутся из
этого файла. Добавлены WT-реализации `ta.dmi` (Wilder smoothing), `ta.stdev` и
подтверждённого HTF-запроса с `[1]` / `lookahead_on`. Сравнение с внешним
TradingView reference ещё не выполнено: **parity UNVERIFIED**.

## Общие данные

Worker передаёт WT те же chart candles и индексированные Bybit/Binance контексты,
которые используются BROKE. Дополнительные необходимые TF включаются в общий набор
подписок/backfill. Для Auto 30m это Bybit 120/240 и Binance BTC 60. Отдельных
загрузчиков свечей, соединений с биржей и второй таблицы OHLCV для WT нет.
WT-график обращается к существующему `/api/setups/{symbol}/{timeframe}/bars`.
На символах вне общего universe свечи могут отсутствовать, но webhook всё равно
сохраняется и пересылается в Telegram.

WT checkpoint находится в том же сжатом checkpoint BROKE и сохраняется в одной
транзакции с обоими текущими состояниями. При первом запуске WT прогревается на
уже доступной chart history. При изменении correlated WT параметров выполняется
replay от сохранённого origin BROKE для корректной исторической связи. Длина
истории и её origin влияют на накопленные состояния Pine.

При включённом Broke correlation адаптер упаковывает из существующего BROKE snapshot
направление, AVG, Execution, Level, MAE, Exhaustion, gateBtcShock и WATCH/PINE entry
events в формат `brokePackedSrc` исходника. Свечной расчёт BROKE не повторяется.
При отсутствии валидных данных WT помечается DEGRADED, уведомления локального
расчёта подавляются. По умолчанию correlation выключена, как в приложенном Pine.

**Settings → WT_SETUPS** содержит 99 inputs. Изменение перезапускает worker через
штатную проверку конфигурации и повторный прогрев. Поддерживается общий BTC-источник
BINANCE:BTCUSDT и request TF не ниже chart TF; сервер отвергает неподдерживаемую
конфигурацию. `brokePackedSrc` закреплён за адаптером `wt_broke_bridge`.

## Страницы и сигналы

- `/setups` — BROKE_SETUPS, страница по умолчанию.
- `/wt-setups` — WT_SETUPS: источник «Расчет системы» / «Сигналы TradingView»,
  поиск, фильтры направления/типа/EQ/ACTION, сортировка, общие свечи, события.
- Таблица WT содержит типы, ACTION, EQ, Score L/S, Entry, SL, managed SL/BE,
  TP1–TP4, LIQ, RR LIQ, риск/позицию, возраст, Volume, ADX, контексты и Broke.
- `T1`–`T4` обозначают стратегии; `TP1`–`TP4` — ценовые цели.

В Alerts у правила есть **Стратегия**. Старые правила по умолчанию относятся к
BROKE_SETUPS. WT и BROKE не используют общее состояние cooldown/crossing.
`signal_source` позволяет выбрать `engine` или `tradingview`.

`signals contains T1+T3` означает одновременные события T1 и T3 одного направления,
включая более широкие комбинации. Доступны все 15 непустых сочетаний четырёх типов.
`signals contains_all [T1,T3]` эквивалентен такой проверке. `setup_combination == T1+T3`
выбирает точное сочетание последнего плана; это состояние, а не новое событие.
Для отдельного перехода ACTION используйте `READY TO ENTER`. Realtime — исходный
режим WT; обычное webhook-сообщение не доказывает confirmed close, поэтому правило
в режиме confirmed к нему не применяется. Импорт/экспорт правил сохраняет стратегию.

## TradingView → БД → Telegram

Webhook: `https://broke.smautomator.cloud/api/webhooks/tradingview/wt?exchange=BYBIT&key=<WT_TRADINGVIEW_WEBHOOK_KEY>`.
Это отдельный ключ от BROKE. Настройки сервера — `.env`:
`WT_TRADINGVIEW_WEBHOOK_KEY`, `WT_TRADINGVIEW_TELEGRAM_ENABLED=true`.
Последний переключатель включает автоматическую пересылку без отдельного правила.
Используется уже настроенный Telegram bot/chat.

На графике **30m** выбирайте **WT Setups → Any alert() function call** и оставляйте
Dynamic alerts включёнными. Принимаются исходные тексты:

```text
WT LONG | ETHFIUSDT.P | TF=30 | T1 T3 | Score=85 | EQ=72.5 | Action=WAIT RETEST | Entry=1.1 | SL=1.0 | TP1=1.175 | Pos=275 USDT | Risk=25 USDT
WT READY TO ENTER SHORT | ETHFIUSDT.P | TF=30 | T2 T4 | EQ=80.0 | Price=1.1 | PlanEntry=1.11 | SL=1.2 | TP1=1.04 | Broke=OFF
```

В сообщении используется `syminfo.ticker`, без биржи. Поэтому источник задаётся
параметром URL `exchange=BYBIT` (default) или `BINANCE`. Не смешивайте биржи на
одном адресе. Именованные alertcondition из исходника не содержат полный набор
атрибутов/типов; неподдерживаемые тексты возвращают 422 и не пересылаются.

При ответе 201 сообщение и Telegram outbox уже committed одной транзакцией.
API не ждёт Telegram. Notifier применяет штатные retries/uncertain и максимальный
возраст 120 секунд (`ALERT_MAX_AGE_SEC`); старые сигналы после простоя не рассылаются.
Входящие сигналы не требуют прогретого BROKE, но локальные WT-правила требуют
здоровых рыночных данных и подавляются во время replay.

Одинаковый текст в пределах 15 секунд помечается повтором и не ставится в очередь
вторично; оригинальные доставки сохраняются. Это эвристика, так как исходный Pine
не передаёт ID/время свечи. При использовании JSON-конверта поддержаны
`{"message":"...","event_id":"уникальный-ID","bar_start":1790553600000}`.
`event_id` обеспечивает устойчивое к рестарту подавление повторов; `bar_start`
должен быть началом 30m свечи UTC в миллисекундах. Без него время доставки никогда
не объявляется временем свечи. Автоматическая пересылка и пользовательское
WT-правило могут намеренно отправить два сообщения; для только правил выключите
`WT_TRADINGVIEW_TELEGRAM_ENABLED` у API и notifier.

Таблицы `wt_current` и `wt_events` добавлены миграцией 0007. API очищает WT events,
устаревшие входящие current и автоматический WT outbox старше 30 суток от получения
при старте и каждый час (до 10 000 событий за цикл). Локальный current/checkpoint
не удаляется. Ключ отсутствует в БД и access logs приложения.

REST: `/api/wt/setups?signal_source=engine`, `/api/wt/events?signal_source=tradingview`,
`GET/PUT /api/wt/parameters`. События поддерживают `symbol`, `limit`, `offset`.
