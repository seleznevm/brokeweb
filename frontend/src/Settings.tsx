import { useState, useRef, type ChangeEvent } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { api, send } from './api';
import { Empty, ErrorMessage, Section } from './common';
import { format } from './model';
import { DEFAULT_TIMEZONE_OFFSET, timezoneOffsets, timezoneLabel } from './displayTime';
import type { RuntimeSettings } from './Timezone';
import type { Data, Parameter, Parameters } from './types';

function ParameterInput({ field, value, onChange }: { field: Parameter; value: unknown; onChange: (value: unknown) => void }) {
  if (field.options?.length) return <select value={String(value ?? '')} onChange={e => onChange(field.options?.find(x => String(x) === e.target.value) ?? e.target.value)}>{field.options.map(x => <option key={String(x)} value={String(x)}>{format(x)}</option>)}</select>;
  if (field.type === 'bool') return <input type="checkbox" checked={value === true} onChange={e => onChange(e.target.checked)} />;
  if (['int', 'float'].includes(field.type)) return <input type="number" required step={field.type === 'int' ? 1 : 'any'} min={field.minval} max={field.maxval} value={typeof value === 'number' ? value : ''} onChange={e => onChange(e.target.value === '' ? null : Number(e.target.value))} />;
  return <input value={value == null ? '' : String(value)} onChange={e => onChange(e.target.value)} />;
}

function ParameterEditor({ parameters, endpoint = '/api/parameters', cacheKey = 'parameters' }: { parameters: Parameters; endpoint?: string; cacheKey?: string }) {
  const cache = useQueryClient();
  const [values, setValues] = useState<Data>(parameters.values);
  const [search, setSearch] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>();
  const [notice, setNotice] = useState('');

  const changed = parameters.schema.filter(field => JSON.stringify(values[field.name]) !== JSON.stringify(parameters.values[field.name]));
  const filtered = parameters.schema.filter(field => `${field.name} ${field.title} ${field.group} ${field.tooltip}`.toLowerCase().includes(search.toLowerCase()));
  const groups = [...new Set(filtered.map(field => field.group ?? 'General'))];

  async function save() {
    setBusy(true);
    setError(undefined);
    try {
      const saved = await send<Parameters>(endpoint, 'PUT', { values });
      cache.setQueryData([cacheKey], saved);
      setNotice(`Сохранён parameter set ${saved.id}.`);
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={e => { e.preventDefault(); void save(); }}>
      <div className="toolbar sticky-toolbar">
        <input className="parameter-search" aria-label="Поиск параметров" placeholder="Поиск по названию, группе или пояснению…" value={search} onChange={e => setSearch(e.target.value)} />
        <span>{filtered.length} / {parameters.schema.length} inputs</span>
        <button disabled={busy || changed.length === 0} className="primary">Сохранить версию ({changed.length})</button>
        <button type="button" onClick={() => { setValues({ ...parameters.values }); setNotice(''); }}>Отменить изменения</button>
      </div>
      <ErrorMessage error={error} />
      {notice && <div className="success" role="status">{notice}</div>}
      {groups.map(group => (
        <details className="parameter-group" key={group} open={search ? true : undefined}>
          <summary>{group} <span>{filtered.filter(x => (x.group ?? 'General') === group).length}</span></summary>
          <div className="parameters">
            {filtered.filter(x => (x.group ?? 'General') === group).map(field => (
              <div className={`parameter ${changed.some(x => x.name === field.name) ? 'modified' : ''}`} key={field.name}>
                <label htmlFor={field.name}>{field.title || field.name}<code>{field.name}</code></label>
                <div id={field.name}>
                  <ParameterInput field={field} value={values[field.name] ?? field.default} onChange={value => setValues(old => ({ ...old, [field.name]: value }))} />
                </div>
                <p>{field.tooltip || 'Исходный input Pine Script.'}<small>Default: {format(field.default)}{field.minval != null ? ` · min ${field.minval}` : ''}{field.maxval != null ? ` · max ${field.maxval}` : ''}</small></p>
                <button type="button" title="Вернуть Pine default" onClick={() => setValues(old => ({ ...old, [field.name]: field.default }))}>↺</button>
              </div>
            ))}
          </div>
        </details>
      ))}
      {!groups.length && <Empty>Параметры не найдены.</Empty>}
      <p className="subtle">Сохранение создаёт воспроизводимую версию параметров. Валидация ограничений выполняется сервером; расчётные значения на фронтенде не изменяются.</p>
    </form>
  );
}

export function Settings() {
  const cache = useQueryClient();
  const query = useQuery({ queryKey: ['parameters'], queryFn: () => api<Parameters>('/api/parameters') });
  const runtime = useQuery({ queryKey: ['settings'], queryFn: () => api<RuntimeSettings>('/api/settings') });

  const [turnover, setTurnover] = useState<string | null>(null);
  const [zone, setZone] = useState<number | null>(null);
  const [interval, setIntervalValue] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>();
  const [notice, setNotice] = useState('');

  // Telegram WE setups
  const [tgToken, setTgToken] = useState<string | null>(null);
  const [tgChat, setTgChat] = useState<string | null>(null);
  const [tgTopic, setTgTopic] = useState<string | null>(null);
  const [tgTestBusy, setTgTestBusy] = useState(false);
  const [tgTestResult, setTgTestResult] = useState<unknown>();

  // BROKE-PB settings
  const [pbNominal, setPbNominal] = useState<string | null>(null);
  const [pbTgToken, setPbTgToken] = useState<string | null>(null);
  const [pbTgChat, setPbTgChat] = useState<string | null>(null);
  const [pbTgTopic, setPbTgTopic] = useState<string | null>(null);
  const [pbPmEnabled, setPbPmEnabled] = useState<boolean | null>(null);
  const [pbPmToken, setPbPmToken] = useState<string | null>(null);
  const [pbPmChat, setPbPmChat] = useState<string | null>(null);
  const [pbPmTopic, setPbPmTopic] = useState<string | null>(null);

  const [pbTestBusy, setPbTestBusy] = useState(false);
  const [pbPmTestBusy, setPbPmTestBusy] = useState(false);
  const [exportBusy, setExportBusy] = useState(false);
  const [importBusy, setImportBusy] = useState(false);

  const fileInputRef = useRef<HTMLInputElement>(null);

  async function handleExport() {
    setExportBusy(true);
    setError(undefined);
    try {
      const res = await api<unknown>('/api/settings/export');
      const blob = new Blob([JSON.stringify(res, null, 2)], { type: 'application/json' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `brokeweb-settings-${new Date().toISOString().slice(0, 10)}.json`;
      a.click();
      URL.revokeObjectURL(url);
      setNotice('Настройки успешно экспортированы в JSON файл.');
    } catch (err) {
      setError(err);
    } finally {
      setExportBusy(false);
    }
  }

  async function handleImport(e: ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    setImportBusy(true);
    setError(undefined);
    try {
      const text = await file.text();
      const payload = JSON.parse(text);
      const res = await send<unknown>('/api/settings/import', 'POST', payload);
      await cache.invalidateQueries({ queryKey: ['settings'] });
      await cache.invalidateQueries({ queryKey: ['parameters'] });
      setNotice('Настройки и параметры успешно импортированы из файла.');
      setTgTestResult(res);
    } catch (err) {
      setError(err);
    } finally {
      setImportBusy(false);
      if (fileInputRef.current) fileInputRef.current.value = '';
    }
  }

  return (
    <>
      <div className="page-heading">
        <div>
          <p className="eyebrow">PINE INPUTS · VERSIONED · EXPORT / IMPORT</p>
          <h1>Settings</h1>
          <p>Полный набор параметров исходного Pine и конфигурации стратегий. Текущая версия параметров: <code>{query.data?.id ?? 'н/д'}</code></p>
        </div>
        <div style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
          <button type="button" disabled={exportBusy} onClick={handleExport}>
            {exportBusy ? 'Экспорт...' : '📥 Экспорт настроек (JSON)'}
          </button>
          <button type="button" disabled={importBusy} className="primary" onClick={() => fileInputRef.current?.click()}>
            {importBusy ? 'Импорт...' : '📤 Импорт настроек'}
          </button>
          <input ref={fileInputRef} type="file" accept=".json,application/json" style={{ display: 'none' }} onChange={handleImport} />
        </div>
      </div>

      {notice && <div className="success" role="status" style={{ marginBottom: '16px' }}>{notice}</div>}
      <ErrorMessage error={runtime.error ?? error} />

      {/* STRATEGY BROKE-PB SECTION */}
      <Section title="BROKE-PB · Торговля от уровней поддержки и сопротивления">
        <form onSubmit={e => {
          e.preventDefault();
          setBusy(true);
          setError(undefined);
          const nominalVal = Number(pbNominal ?? runtime.data?.broke_pb_position_usdt ?? 500);
          const pmActive = pbPmEnabled ?? runtime.data?.broke_pb_pm_telegram_enabled ?? true;

          void send<RuntimeSettings>('/api/settings', 'PUT', {
            broke_pb_position_usdt: nominalVal,
            broke_pb_telegram_bot_token: (pbTgToken ?? runtime.data?.broke_pb_telegram_bot_token ?? '').trim() || undefined,
            broke_pb_telegram_chat_id: (pbTgChat ?? runtime.data?.broke_pb_telegram_chat_id ?? '').trim() || undefined,
            broke_pb_telegram_topic_id: (pbTgTopic ?? runtime.data?.broke_pb_telegram_topic_id ?? '').trim() || undefined,
            broke_pb_pm_telegram_enabled: pmActive,
            broke_pb_pm_telegram_bot_token: (pbPmToken ?? runtime.data?.broke_pb_pm_telegram_bot_token ?? '').trim() || undefined,
            broke_pb_pm_telegram_chat_id: (pbPmChat ?? runtime.data?.broke_pb_pm_telegram_chat_id ?? '').trim() || undefined,
            broke_pb_pm_telegram_topic_id: (pbPmTopic ?? runtime.data?.broke_pb_pm_telegram_topic_id ?? '').trim() || undefined,
          }).then(saved => {
            cache.setQueryData(['settings'], saved);
            setNotice('Настройки стратегии BROKE-PB успешно сохранены.');
          }).catch(setError).finally(() => setBusy(false));
        }}>
          <div style={{ marginBottom: '18px' }}>
            <label style={{ display: 'block', fontWeight: 600, marginBottom: '6px' }}>
              BROKE-PB position USDT (номинал виртуального входа)
              <input
                required
                type="number"
                min="1"
                max="1000000"
                step="any"
                style={{ display: 'block', width: '220px', marginTop: '4px' }}
                value={pbNominal ?? String(runtime.data?.broke_pb_position_usdt ?? 500)}
                onChange={e => setPbNominal(e.target.value)}
              />
            </label>
            <p className="subtle">
              Виртуальный вход осуществляется размером {pbNominal ?? runtime.data?.broke_pb_position_usdt ?? 500} USDT:
              от верхней границы поддержки при наличии LONG direction на 30m индикатора, либо от нижней границы сопротивления при наличии SHORT direction на 30m.
            </p>
          </div>

          <div style={{ background: 'var(--surface-sunken)', padding: '14px', borderRadius: '8px', marginBottom: '18px', border: '1px solid var(--border)' }}>
            <h3 style={{ fontSize: '14px', margin: '0 0 10px 0', textTransform: 'uppercase', letterSpacing: '0.05em' }}>Telegram для сетапов BROKE-PB</h3>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(260px, 1fr))', gap: '14px', marginBottom: '12px' }}>
              <label>
                Токен Telegram бота
                <input
                  type="text"
                  value={pbTgToken ?? runtime.data?.broke_pb_telegram_bot_token ?? ''}
                  onChange={e => setPbTgToken(e.target.value)}
                  placeholder="Оставьте пустым для использования общего бота"
                />
              </label>
              <label>
                Номер канала / группы
                <input
                  type="text"
                  value={pbTgChat ?? runtime.data?.broke_pb_telegram_chat_id ?? ''}
                  onChange={e => setPbTgChat(e.target.value)}
                  placeholder="ID канала или группы (-100...)"
                />
              </label>
              <label>
                Номер топика
                <input
                  type="text"
                  value={pbTgTopic ?? runtime.data?.broke_pb_telegram_topic_id ?? ''}
                  onChange={e => setPbTgTopic(e.target.value)}
                  placeholder="Номер топика в канале"
                />
              </label>
            </div>
            <button
              type="button"
              disabled={busy || pbTestBusy}
              onClick={async () => {
                setPbTestBusy(true);
                setError(undefined);
                try {
                  const res = await send('/api/alerts/test-telegram', 'POST', { target: 'broke_pb' });
                  setTgTestResult(res);
                  setNotice('Тестовый сигнал сетапа BROKE-PB отправлен в Telegram.');
                } catch (err) {
                  setError(err);
                } finally {
                  setPbTestBusy(false);
                }
              }}
            >
              {pbTestBusy ? 'Отправка...' : 'Отправить test в топик сетапов PB'}
            </button>
          </div>

          <div style={{ background: 'var(--surface-sunken)', padding: '14px', borderRadius: '8px', marginBottom: '18px', border: '1px solid var(--border)' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '10px' }}>
              <h3 style={{ fontSize: '14px', margin: 0, textTransform: 'uppercase', letterSpacing: '0.05em' }}>
                Telegram для менеджмента открытых позиций (Position Manager)
              </h3>
              <label className="inline" style={{ fontWeight: 600 }}>
                <input
                  type="checkbox"
                  checked={pbPmEnabled ?? runtime.data?.broke_pb_pm_telegram_enabled ?? true}
                  onChange={e => setPbPmEnabled(e.target.checked)}
                />
                Отправка менеджмент сигналов активна
              </label>
            </div>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(260px, 1fr))', gap: '14px', marginBottom: '12px' }}>
              <label>
                Токен Telegram бота
                <input
                  type="text"
                  value={pbPmToken ?? runtime.data?.broke_pb_pm_telegram_bot_token ?? ''}
                  onChange={e => setPbPmToken(e.target.value)}
                  placeholder="Оставьте пустым для использования общего бота"
                />
              </label>
              <label>
                Номер канала / группы
                <input
                  type="text"
                  value={pbPmChat ?? runtime.data?.broke_pb_pm_telegram_chat_id ?? ''}
                  onChange={e => setPbPmChat(e.target.value)}
                  placeholder="ID канала или группы (-100...)"
                />
              </label>
              <label>
                Номер топика
                <input
                  type="text"
                  value={pbPmTopic ?? runtime.data?.broke_pb_pm_telegram_topic_id ?? ''}
                  onChange={e => setPbPmTopic(e.target.value)}
                  placeholder="Номер топика менеджмента"
                />
              </label>
            </div>
            <button
              type="button"
              disabled={busy || pbPmTestBusy}
              onClick={async () => {
                setPbPmTestBusy(true);
                setError(undefined);
                try {
                  const res = await send('/api/alerts/test-telegram', 'POST', { target: 'broke_pb_pm' });
                  setTgTestResult(res);
                  setNotice('Тестовое сообщение Position Manager отправлено в Telegram.');
                } catch (err) {
                  setError(err);
                } finally {
                  setPbPmTestBusy(false);
                }
              }}
            >
              {pbPmTestBusy ? 'Отправка...' : 'Отправить test в топик менеджмента PB'}
            </button>
          </div>

          <div className="toolbar">
            <button className="primary" disabled={busy || !runtime.data} type="submit">
              Сохранить настройки BROKE-PB
            </button>
          </div>
        </form>
      </Section>

      <Section title="Пул монет — ликвидность">
        <form className="toolbar" onSubmit={e => {
          e.preventDefault();
          setBusy(true);
          setError(undefined);
          void send<RuntimeSettings>('/api/settings', 'PUT', { universe_min_turnover24h_usdt: Number(turnover ?? (runtime.data?.universe_min_turnover24h_usdt ?? 10000000) / 1000000) * 1000000 }).then(saved => {
            cache.setQueryData(['settings'], saved);
            setNotice('Порог ликвидности сохранён. Применится при следующем обновлении пула или запуске engine.');
          }).catch(setError).finally(() => setBusy(false));
        }}>
          <label>Минимальный оборот за 24ч, млн USDT
            <input required type="number" min="0" max="1000000" step="any" value={turnover ?? String((runtime.data?.universe_min_turnover24h_usdt ?? 10000000) / 1000000)} onChange={e => setTurnover(e.target.value)} />
          </label>
          <button disabled={busy || !runtime.data}>Сохранить порог</button>
          <p className="subtle">По умолчанию 10 млн USDT; 0 отключает фильтр. Используется оборот Bybit. Пул обновляется после прогрева, затем раз в час.</p>
        </form>
      </Section>

      <Section title="Сохранение snapshots">
        <form className="toolbar" onSubmit={e => {
          e.preventDefault();
          setBusy(true);
          setError(undefined);
          void send<RuntimeSettings>('/api/settings', 'PUT', { snapshot_interval_sec: Number(interval ?? runtime.data?.snapshot_interval_sec ?? 15) }).then(saved => {
            cache.setQueryData(['settings'], saved);
            setNotice('Интервал snapshots сохранён.');
          }).catch(setError).finally(() => setBusy(false));
        }}>
          <label>Интервал, секунды
            <input required type="number" min="1" step="1" value={interval ?? String(runtime.data?.snapshot_interval_sec ?? '')} onChange={e => setIntervalValue(e.target.value)} />
          </label>
          <button disabled={busy || !runtime.data}>Сохранить интервал</button>
          <p className="subtle">Периодическая запись состояния всех атрибутов для истории и исследований.</p>
        </form>
      </Section>

      <Section title="Временная зона">
        <form className="toolbar" onSubmit={e => {
          e.preventDefault();
          setBusy(true);
          setError(undefined);
          void send<RuntimeSettings>('/api/settings', 'PUT', { timezone_offset_minutes: zone ?? runtime.data?.timezone_offset_minutes ?? DEFAULT_TIMEZONE_OFFSET }).then(saved => {
            cache.setQueryData(['settings'], saved);
            setZone(null);
            setNotice('Временная зона сохранена. Все графики используют ' + timezoneLabel(saved.timezone_offset_minutes) + '.');
          }).catch(setError).finally(() => setBusy(false));
        }}>
          <label>Временная зона графиков
            <select value={zone ?? runtime.data?.timezone_offset_minutes ?? DEFAULT_TIMEZONE_OFFSET} onChange={e => setZone(Number(e.target.value))}>
              {timezoneOffsets.map(value => <option key={value} value={value}>{timezoneLabel(value)}{value === 420 ? ' — по умолчанию' : ''}</option>)}
            </select>
          </label>
          <button disabled={busy || !runtime.data}>Сохранить временную зону</button>
          <p className="subtle">Общая настройка осей, подсказок, времени событий и пользовательского периода истории. Фиксированное смещение UTC.</p>
        </form>
      </Section>

      <Section title="Telegram — сигналы сетапов WE (BROKE)">
        <form onSubmit={e => {
          e.preventDefault();
          setBusy(true);
          setError(undefined);
          void send<RuntimeSettings>('/api/settings', 'PUT', {
            telegram_bot_token: (tgToken ?? runtime.data?.telegram_bot_token ?? '8384688195:AAH5sLNK4su7cV6vW7pehE-7mJYeRE4JBG0').trim(),
            telegram_chat_id: (tgChat ?? runtime.data?.telegram_chat_id ?? '-1003788053657').trim(),
            telegram_topic_id: (tgTopic ?? runtime.data?.telegram_topic_id ?? '25152').trim()
          }).then(saved => {
            cache.setQueryData(['settings'], saved);
            setTgToken(null);
            setTgChat(null);
            setTgTopic(null);
            setNotice('Настройки Telegram сохранены. Сигналы сетапов WE будут отправляться в указанный топик канала.');
          }).catch(setError).finally(() => setBusy(false));
        }}>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(260px,1fr))', gap: '16px', marginBottom: '14px' }}>
            <label>Токен Telegram бота
              <input type="text" value={tgToken ?? runtime.data?.telegram_bot_token ?? '8384688195:AAH5sLNK4su7cV6vW7pehE-7mJYeRE4JBG0'} onChange={e => setTgToken(e.target.value)} placeholder="8384688195:AAH5sLNK4su7cV6vW7pehE-7mJYeRE4JBG0" />
              <small className="subtle">Бот для отправки алертов и сетапов WE</small>
            </label>
            <label>Номер канала / группы
              <input type="text" value={tgChat ?? runtime.data?.telegram_chat_id ?? '-1003788053657'} onChange={e => setTgChat(e.target.value)} placeholder="-1003788053657" />
              <small className="subtle">ID канала или супергруппы (с -100)</small>
            </label>
            <label>Номер топика в канале
              <input type="text" value={tgTopic ?? runtime.data?.telegram_topic_id ?? '25152'} onChange={e => setTgTopic(e.target.value)} placeholder="25152" />
              <small className="subtle">Topic ID форума супергруппы</small>
            </label>
          </div>
          <div className="toolbar">
            <button className="primary" disabled={busy || !runtime.data} type="submit">Сохранить настройки Telegram WE</button>
            <button type="button" disabled={busy || tgTestBusy} onClick={async () => {
              setTgTestBusy(true);
              setError(undefined);
              try {
                const res = await send('/api/alerts/test-telegram', 'POST', { target: 'broke_we' });
                setTgTestResult(res);
                setNotice('Тестовое сообщение WE отправлено в Telegram.');
              } catch (err) {
                setError(err);
              } finally {
                setTgTestBusy(false);
              }
            }}>
              {tgTestBusy ? 'Отправка...' : 'Отправить test Telegram в топик WE'}
            </button>
          </div>
          <p className="subtle">Сигналы сетапов WE (Watch Entry LONG / SHORT) генерируются системой по стратегии BROKE и автоматически пересылаются в указанный топик супергруппы.</p>
        </form>
        {tgTestResult !== undefined && (
          <div style={{ marginTop: '12px' }}>
            <p className="subtle">Результат тестовой отправки:</p>
            <pre className="json">{JSON.stringify(tgTestResult, null, 2)}</pre>
          </div>
        )}
      </Section>

      <ErrorMessage error={query.error} />
      {query.data ? <ParameterEditor parameters={query.data} /> : <Empty>{query.isPending ? 'Загрузка схемы Pine inputs…' : 'Схема параметров недоступна.'}</Empty>}
    </>
  );
}
