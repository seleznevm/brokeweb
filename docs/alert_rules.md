# Telegram rules

Правила являются данными JSON. Никакие пользовательские выражения не исполняются как Python.

```json
{
  "name": "Long quality",
  "enabled": false,
  "conditions": {
    "op": "AND",
    "conditions": [
      {"field": "direction", "op": "==", "value": "LONG"},
      {"field": "avg_setup", "op": ">=", "value": 70},
      {"field": "execution", "op": ">=", "value": 65},
      {"field": "mae", "op": "<=", "value": 45}
    ]
  },
  "mode": "realtime",
  "frequency": "once_per_generation",
  "cooldown_seconds": 60,
  "template": "{symbol} {direction}: {action}; AVG {avg_setup}; SL {sl}; T1 {t1}"
}
```

Операторы: `== != > >= < <= IN NOT IN BETWEEN contains changed changed_to crosses_above crosses_below`. `crosses_above` означает previous <= threshold и current > threshold; `crosses_below` — previous >= threshold и current < threshold. Это generic rules, отдельные Pine AVG70/Execution65 events сохраняют собственную исходную >= семантику.

AND/OR могут вкладываться, NOT содержит одно условие. Максимум 10 уровней вложенности. Поля поддерживают dot path (`metrics.gateStructure`), в UI предлагаются canonical metrics. `event` сопоставляется с каждым detector signal текущего snapshot.

Режим confirmed сравнивает с предыдущим confirmed snapshot. Частоты: один раз в баре; один раз за generation; first_occurrence — фронт false→true с новым срабатыванием после false; repeat — повтор пока true с cooldown. Изменение правила создаёт новую версию и отдельное dedupe state.

Alerts не ставятся в очередь при неготовых/stale данных и повторно проверяются непосредственно перед отправкой. Отключённое/изменённое правило, устаревший сигнал или деградировавший feed подавляют очередь. Ответ 429 приводит к retry_after; неоднозначный timeout после отправки — `uncertain` без автоматического повтора.

Проверка `/api/alerts/rules/test` не отправляет сообщений. `/api/alerts/test-telegram` создаёт реальную test delivery только по действию оператора. Credentials отсутствуют в репозитории.
