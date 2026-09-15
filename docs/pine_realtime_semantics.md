# Realtime / rollback / MTF

На каждом update обычные Pine `var` восстанавливаются из последнего committed close. Изменяемые arrays/UDT копируются, поэтому открытая свеча не загрязняет confirmed state. Обычные series вычисляются заново; history `[n]` читает предыдущие committed executions соответствующего scope/callsite. `varip` хранится отдельно и переживает rollback.

При closing update исходный код исполняется ещё раз с `barstate.isconfirmed=true`, затем commit выполняется один раз. Повторное подтверждение старого бара требует replay и не допускается прямым `PineEngine.update`. Открытая свеча не считается закрытой только потому, что наступило локальное время: используется exchange confirmation.

Тесты проверяют два intrabar samples, сброс `barstate.isnew`, independent callsites, исторические индексы и average по generation. Исходные latch `avgSetup70CrossThisBar` и `execution65CrossThisBar` сохраняются весь бар; source risk override влияет на первый crossing по исходным условиям.

`request.security` имеет отдельный execution context. На истории доступны только source bars с end <= chart close; future HTF candles не подглядываются. На realtime допускается предоставленная открытая HTF candle. `gaps_off` возвращает последнее доступное значение, одновременно health сообщает отсутствие/устаревание источника. `request.security_lower_tf` возвращает chronological arrays закрывшихся intrabars внутри текущей chart candle.

Контексты worker передаёт engine как снимок списков, чтобы фоновое обновление feeds не меняло набор данных посередине расчёта.

30S строятся по timestamp сделок. Первый неполный bucket, reconnect gap и late trade не считаются полным покрытием. Missing history не заполняется синтетическими нулевыми свечами. После restart нельзя восстановить неполученные исторические intrabar samples одним OHLCV replay; это отдельное ограничение realtime parity. Коррекция confirmed kline вызывает historical replay, воспроизводящий режим перезагрузки Pine по закрытым свечам.

Официальная семантика: [Execution model](https://www.tradingview.com/pine-script-docs/language/execution-model/), [Variable declarations](https://www.tradingview.com/pine-script-docs/language/variable-declarations/), [Other timeframes and data](https://www.tradingview.com/pine-script-docs/concepts/other-timeframes-and-data/). Эти документы использовались для реализации, но не являются числовым reference данного индикатора.

BTC uses an independent native Binance kline WebSocket. Each Bybit execution captures the latest available context; BTC messages alone do not execute the chart script. Disconnect/REST repair and per-TF freshness are visible in health. Identical tick ordering across exchanges and TradingView remains unverified.

Graceful worker cancellation waits for actual background calculation/database completion before releasing shard ownership; cancelling an asyncio wrapper does not stop a running Python thread. Restart restores the persisted intrabar checkpoint. An abrupt crash can still lose updates after the latest durable transaction.
