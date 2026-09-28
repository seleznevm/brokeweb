import { useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { api, send } from './api';
import { Badge, ErrorMessage, Section } from './common';

interface AIAgentSettings {
  enabled: boolean;
  tg_bot_token: string;
  primary_ai: string;
  primary_api_token: string;
  primary_model: string;
  secondary_ai: string;
  secondary_api_token: string;
  secondary_model: string;
  nim_base_url: string;
  min_avg_setup: number;
  max_setups_in_report: number;
  allowed_chat_ids: string;
}

interface AIAgentStatus {
  status: string;
  telegram?: string;
  stale?: boolean;
  updated_at?: number;
  error?: string;
  note?: string;
  pending_updates?: number;
}

const DEFAULT_SETTINGS: AIAgentSettings = {
  enabled: true,
  tg_bot_token: '',
  primary_ai: 'NVIDIA NIM',
  primary_api_token: '',
  primary_model: 'meta/llama-3.1-70b-instruct',
  secondary_ai: 'NVIDIA NIM',
  secondary_api_token: '',
  secondary_model: 'meta/llama-3.1-70b-instruct',
  nim_base_url: 'https://integrate.api.nvidia.com/v1',
  min_avg_setup: 60,
  max_setups_in_report: 10,
  allowed_chat_ids: '',
};

const NIM_MODELS = [
  'meta/llama-3.1-70b-instruct',
  'meta/llama-3.1-8b-instruct',
  'meta/llama-3.3-70b-instruct',
  'nvidia/llama-3.1-nemotron-70b-instruct',
  'mistralai/mistral-large-2-instruct',
  'google/gemma-2-27b-it',
];

function statusTone(status: string, stale?: boolean) {
  if (stale) return 'bad';
  if (status === 'HEALTHY' || status === 'polling') return 'good';
  if (status === 'DISABLED' || status === 'NO_TOKEN') return 'neutral';
  return 'bad';
}

export function AIAgent() {
  const cache = useQueryClient();
  const settingsQ = useQuery<AIAgentSettings>({
    queryKey: ['ai-agent-settings'],
    queryFn: () => api<AIAgentSettings>('/api/ai-agent/settings'),
  });
  const statusQ = useQuery<AIAgentStatus>({
    queryKey: ['ai-agent-status'],
    queryFn: () => api<AIAgentStatus>('/api/ai-agent/status'),
    refetchInterval: 15000,
  });

  const live = settingsQ.data ?? DEFAULT_SETTINGS;
  const [form, setForm] = useState<AIAgentSettings | null>(null);
  const cfg = form ?? live;

  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>();
  const [notice, setNotice] = useState('');

  function field<K extends keyof AIAgentSettings>(key: K, value: AIAgentSettings[K]) {
    setForm(old => ({ ...(old ?? live), [key]: value }));
  }

  async function save() {
    if (!form) return;
    setBusy(true); setError(undefined); setNotice('');
    try {
      const saved = await send<AIAgentSettings>('/api/ai-agent/settings', 'PUT', form);
      cache.setQueryData(['ai-agent-settings'], saved);
      setForm(null);
      setNotice('Настройки AI агента сохранены.');
    } catch (e) { setError(e); }
    finally { setBusy(false); }
  }

  const status = statusQ.data;
  const statusLabel = status?.status ?? 'UNKNOWN';

  return (
    <>
      <div className="page-heading">
        <div>
          <p className="eyebrow">AI AGENT · NVIDIA NIM · TELEGRAM</p>
          <h1>AI Agent</h1>
          <p>Анализ сетапов по команде /ai_now в Telegram. Рекомендации входа, SL, TP1–TP3.</p>
        </div>
        <Badge tone={statusTone(statusLabel, status?.stale)}>
          <span className="dot" />
          {statusLabel}
        </Badge>
      </div>

      <ErrorMessage error={error ?? settingsQ.error} />
      {notice && <div role="status" className="success">{notice}</div>}

      {/* Status panel */}
      <Section title="Статус сервиса">
        {status ? (
          <dl className="metadata">
            <div><dt>Статус</dt><dd>{status.status}{status.stale ? ' (stale)' : ''}</dd></div>
            {status.telegram && <div><dt>Telegram</dt><dd>{status.telegram}</dd></div>}
            {status.pending_updates !== undefined && <div><dt>Pending updates</dt><dd>{status.pending_updates}</dd></div>}
            {status.error && <div><dt>Ошибка</dt><dd className="bad">{status.error}</dd></div>}
            {status.note && <div><dt>Заметка</dt><dd className="subtle">{status.note}</dd></div>}
            {status.updated_at && (
              <div>
                <dt>Heartbeat</dt>
                <dd>{new Date(status.updated_at).toLocaleTimeString()}</dd>
              </div>
            )}
          </dl>
        ) : (
          <p className="subtle">Сервис ai-agent не отвечает или ещё не запущен.</p>
        )}
      </Section>

      {/* Main settings form */}
      <form onSubmit={e => { e.preventDefault(); void save(); }}>
        <Section title="Основные настройки">
          <div className="form-grid">
            <label className="inline">
              <input
                type="checkbox"
                checked={cfg.enabled}
                onChange={e => field('enabled', e.target.checked)}
              />
              AI Agent включён
            </label>
          </div>
          <div className="form-grid">
            <label>
              AI TG Bot Token
              <input
                type="text"
                value={cfg.tg_bot_token}
                onChange={e => field('tg_bot_token', e.target.value)}
                placeholder="8513180460:AAH…"
                autoComplete="off"
              />
              <small style={{ color: 'var(--muted)', fontSize: 10 }}>
                Отдельный бот для AI агента (не путать с основным notification-ботом)
              </small>
            </label>
            <label>
              Разрешённые Chat IDs
              <input
                value={cfg.allowed_chat_ids}
                onChange={e => field('allowed_chat_ids', e.target.value)}
                placeholder="-1001234567890, 123456789"
              />
              <small style={{ color: 'var(--muted)', fontSize: 10 }}>
                Через запятую. Пустое поле = принимать команды от всех (не рекомендуется).
              </small>
            </label>
          </div>
        </Section>

        <Section title="Primary AI">
          <div className="form-grid">
            <label>
              Провайдер
              <input
                value={cfg.primary_ai}
                onChange={e => field('primary_ai', e.target.value)}
              />
            </label>
            <label>
              API Token
              <input
                type="text"
                value={cfg.primary_api_token}
                onChange={e => field('primary_api_token', e.target.value)}
                placeholder="nvapi-…"
                autoComplete="off"
              />
            </label>
            <label>
              Модель
              <select value={cfg.primary_model} onChange={e => field('primary_model', e.target.value)}>
                {NIM_MODELS.map(m => <option key={m} value={m}>{m}</option>)}
                {!NIM_MODELS.includes(cfg.primary_model) && (
                  <option value={cfg.primary_model}>{cfg.primary_model}</option>
                )}
              </select>
            </label>
          </div>
        </Section>

        <Section title="Secondary AI (резервный)">
          <p className="subtle">Используется автоматически если Primary AI вернул ошибку.</p>
          <div className="form-grid">
            <label>
              Провайдер
              <input
                value={cfg.secondary_ai}
                onChange={e => field('secondary_ai', e.target.value)}
              />
            </label>
            <label>
              API Token
              <input
                type="text"
                value={cfg.secondary_api_token}
                onChange={e => field('secondary_api_token', e.target.value)}
                placeholder="nvapi-…"
                autoComplete="off"
              />
            </label>
            <label>
              Модель
              <select value={cfg.secondary_model} onChange={e => field('secondary_model', e.target.value)}>
                {NIM_MODELS.map(m => <option key={m} value={m}>{m}</option>)}
                {!NIM_MODELS.includes(cfg.secondary_model) && (
                  <option value={cfg.secondary_model}>{cfg.secondary_model}</option>
                )}
              </select>
            </label>
          </div>
        </Section>

        <Section title="NVIDIA NIM">
          <div className="form-grid">
            <label>
              Base URL
              <input
                value={cfg.nim_base_url}
                onChange={e => field('nim_base_url', e.target.value)}
              />
            </label>
          </div>
          <p className="subtle">
            По умолчанию: <code>https://integrate.api.nvidia.com/v1</code>. Для локального NIM контейнера:&nbsp;
            <code>http://localhost:8000/v1</code>.
          </p>
        </Section>

        <Section title="Фильтры анализа">
          <div className="form-grid">
            <label>
              Минимальный avg_setup (0–100)
              <input
                type="number"
                min={0}
                max={100}
                step={1}
                value={cfg.min_avg_setup}
                onChange={e => field('min_avg_setup', Number(e.target.value))}
              />
            </label>
            <label>
              Максимум сетапов в отчёте
              <input
                type="number"
                min={1}
                max={50}
                step={1}
                value={cfg.max_setups_in_report}
                onChange={e => field('max_setups_in_report', Number(e.target.value))}
              />
            </label>
          </div>
          <p className="subtle">
            Агент отберёт лучшие N сетапов по avg_setup и RR для передачи в LLM.
          </p>
        </Section>

        <div className="toolbar">
          <button className="primary" type="submit" disabled={busy || !form}>
            {busy ? 'Сохранение…' : 'Сохранить настройки'}
          </button>
          <button type="button" disabled={!form} onClick={() => { setForm(null); setNotice(''); }}>
            Отменить
          </button>
        </div>
      </form>

      <Section title="Использование">
        <p>Отправьте <code>/ai_now</code> в Telegram-чат с ботом, токен которого указан выше.</p>
        <p>Агент:</p>
        <ol>
          <li>Загрузит текущие активные сетапы из БД.</li>
          <li>Отфильтрует по avg_setup ≥ {cfg.min_avg_setup}, возьмёт топ {cfg.max_setups_in_report}.</li>
          <li>Передаст в {cfg.primary_ai} ({cfg.primary_model}).</li>
          <li>Ответит рекомендациями: SL, TP1, TP2, TP3 для каждого сетапа.</li>
        </ol>
        <p className="subtle">
          При ошибке Primary AI автоматически переключится на Secondary ({cfg.secondary_model}).
        </p>
      </Section>
    </>
  );
}
