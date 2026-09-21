# Brokeweb — Scalping_SMA 1.15.2

Локальный исследовательский монитор Bybit USDT perpetual с Python-исполнителем исходного Pine, PostgreSQL, Redis, FastAPI, React и Telegram rule builder.

**Статус: локальное Docker-развёртывание, частичная TradingView parity PASS.** На 2993 закрытых свечах из 7 реальных CSV совпали доступные уровни/SL/T1 и 124 положительные метки. Полная проверка внутренних оценок и intrabar остаётся UNVERIFIED; production-приёмка и нагрузка на весь universe ещё не завершены. Отчёт: [docs/parity.md](docs/parity.md), [reports/parity.json](reports/parity.json).

## Запуск

Требуется Docker Desktop с Linux containers и Compose.

```powershell
cd C:\dev\brokeweb
Copy-Item .env.example .env  # только при первой установке
# Для локальной проверки установите MAX_SYMBOLS=4 в .env.
docker compose up -d --build
```

Открыть **http://localhost:8080/setups**. Порт привязан к localhost. API/БД/Redis не публикуются наружу.

Текущее тестовое окружение использует `MAX_SYMBOLS=4`, `ACTIVE_TIMEFRAMES=15`. Universe обнаруживается целиком, ограничение применяется к мониторингу явно; инструменты выбираются автоматически по launch time. `MAX_SYMBOLS=0` снимает ограничение, но сначала оцените ресурсы: производительность на всей вселенной не принята. В `.env.example` значение 0.

```bash
docker compose ps
docker compose logs -f engine
docker compose exec api alembic upgrade head
# Проверка контейнеров без удаления данных
docker compose restart engine
docker compose down
```

Проверено 148 Python-тестов, 6 страниц в браузере и восстановление четырёх checkpoints после Docker restart. Текущий протокол: [отчёт реализации](docs/implementation_report.md).

Данные переживают restart/down в named volumes. `down -v` удаляет их — для обычного перезапуска не нужен.

В данной WSL-среде обнаружена несовместимость Windows credential helper. Только для этого случая:

```bash
mkdir -p /tmp/brokeweb-docker
printf '{}\n' > /tmp/brokeweb-docker/config.json
DOCKER_CONFIG=/tmp/brokeweb-docker docker compose up -d --build
```

Пользовательская Docker-конфигурация не изменяется.

## Страницы

- `/setups` — только `ACTION != WAIT SETUP`, поиск, сортировка и фильтры метрик; можно включить все состояния.
- `/setups/BYBIT/{symbol}/{timeframe}` — свечи, зоны, frozen SL/T1, 42 строки Decision Panel, история атрибутов и событий.
- `/alerts` — вложенные AND/OR/NOT, условия, частота, preview совпадений, версии и журнал доставки.
- `/settings` — все 323 Pine inputs и интервал snapshots.
- `/health` — состояние потоков, прогрев и ошибки.
- `/parity` — отчёт сравнения или явное отсутствие reference.

REST: `/api/setups`, `/api/setups/{symbol}/{timeframe}`, подмаршруты `/history`, `/bars`, `/events`, `/api/signals`, `/api/instruments`, `/api/parameters`, `/api/settings`, `/api/alerts/rules`, `/api/alerts/rules/test`, `/api/alerts/deliveries`, `/api/research`, `/api/storage`, `/api/parity`, `/api/health`, `/metrics`. Realtime: `/ws/setups`. OpenAPI доступен внутри API container на `/docs`.

## Telegram

Указать `TELEGRAM_BOT_TOKEN` и `TELEGRAM_CHAT_ID` только в `.env`, затем `docker compose up -d notifier`. Создать правило на `/alerts`. Тестовая отправка — отдельная кнопка, отправляет реальное сообщение. Во время разработки реальные сообщения не отправлялись. Без credentials notifier остаётся запущен с `telegram: disabled`.

Rule engine подавляет stale/recovering данные. При неоднозначном сетевом результате доставка получает `uncertain`, чтобы рестарт не отправил сообщение повторно. Это осознанный выбор: Telegram не предоставляет idempotency key для sendMessage.

## Таймфреймы и параметры

`ACTIVE_TIMEFRAMES=5,15,60` создаёт независимые engines по symbol/TF; после изменения `.env` пересоздать engine. Используются Pine-строки TF. Exchange adapter проверяет поддержку TF. Параметры индикатора меняются через UI/API и образуют новую неизменяемую версию. Worker автоматически переходит к replay для новой версии; прежние snapshots сохраняются.

External `input.source` принимает native OHLCV series. Произвольные внешние TradingView plots требуют отдельного предоставленного ряда; система отклоняет неподдержанный source вместо имитации. Bybit OI/CVD адаптеры существуют отдельно и не подключаются к baseline автоматически.

## Проверки

```bash
python3 -m pip install -r requirements.txt
python3 -m pytest
python3 -m compileall -q backend tools
cd frontend
npm ci
npm run lint
npm run typecheck
npm test
npm run build
```

Docker-вариант Python tests:

```bash
docker compose run --rm -v "$PWD/tests:/app/tests:ro" api python -m pytest -q -s
```

Parity workflow и экспорт: [docs/parity.md](docs/parity.md). Пошаговая инструкция для TradingView: [внутренние оценки через CSV графика и intrabar через Alerts Log](docs/tradingview_capture.md). Не маркируйте release как готовый до получения полного reference и выполнения требований качества/масштабирования.

## Документация

[Архитектура](docs/architecture.md), [полный Pine mapping](docs/pine_mapping.md), [источники](docs/data_sources.md), [rollback/MTF](docs/pine_realtime_semantics.md), [сигналы](docs/signals.md), [БД](docs/database.md), [правила](docs/alert_rules.md), [эксплуатация](docs/operations.md), [архивирование истории](docs/retention.md), [месячные разделы](docs/partitioning.md), [отчёт реализации](docs/implementation_report.md).

## Архивирование истории

Новый сервис `retention` работает в preview и показывает объём старой истории без удаления. Для snapshots/events доступны lossless archive/restore, checksum и каталог. Сроки задаются в `.env`; текущая конфигурация не удаляет рабочие данные. Команды и гарантии: [docs/retention.md](docs/retention.md), состояние: `/api/storage`.

Таблицы snapshots/events разделены по UTC-месяцам миграцией 0005; обслуживание будущих месяцев и DEFAULT выполняет retention. Список: `/api/storage`, инструкция обновления существующей БД: [docs/partitioning.md](docs/partitioning.md).
