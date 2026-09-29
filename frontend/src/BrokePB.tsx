import { useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { api } from './api';
import { Badge, Empty, ErrorMessage, Section } from './common';
import { useDisplayTime } from './Timezone';

interface ActivePosition {
  id: number;
  symbol: string;
  direction: 'LONG' | 'SHORT';
  status: string;
  nominal_usdt: number;
  entry_price: number;
  current_price: number;
  entry_time: number;
  sl: number;
  original_sl: number;
  tp1: number;
  runner: number | null;
  tp1_hit: boolean;
  runner_be: number | null;
  underwater: boolean;
  reduced: boolean;
  pnl_usdt: number;
  pnl_pct: number;
  action: string;
  updated_at: number;
}

interface PositionRecord {
  id: number;
  symbol: string;
  exchange: string;
  timeframe: string;
  direction: 'LONG' | 'SHORT';
  status: string;
  nominal_usdt: number;
  entry_price: number;
  entry_time: number;
  bar_start: number;
  sl: number;
  tp1: number;
  runner: number | null;
  tp1_hit: boolean;
  runner_hit: boolean;
  runner_be: number | null;
  underwater: boolean;
  reduced: boolean;
  exhaustion_taken: boolean;
  close_price: number | null;
  close_time: number | null;
  close_reason: string | null;
  pnl_usdt: number | null;
  pnl_pct: number | null;
  updated_at: number;
}

function formatPrice(p: number | null | undefined): string {
  if (p == null || !Number.isFinite(p)) return '—';
  if (Math.abs(p) >= 100) return p.toFixed(2);
  if (Math.abs(p) >= 1) return p.toFixed(4);
  return p.toFixed(6);
}

export function BrokePB() {
  const { timestamp } = useDisplayTime();
  const [tab, setTab] = useState<'active' | 'history'>('active');

  const activeQuery = useQuery({
    queryKey: ['broke-pb-active'],
    queryFn: () => api<{ items: ActivePosition[]; total: number }>('/api/broke-pb/positions/active'),
    refetchInterval: 5000,
  });

  const historyQuery = useQuery({
    queryKey: ['broke-pb-history'],
    queryFn: () => api<{ items: PositionRecord[] }>('/api/broke-pb/positions?status=CLOSED&limit=200'),
    refetchInterval: 15000,
  });

  const activeItems = useMemo(() => activeQuery.data?.items ?? [], [activeQuery.data]);
  const historyItems = useMemo(() => historyQuery.data?.items ?? [], [historyQuery.data]);

  const stats = useMemo(() => {
    const closed = historyItems;
    const wins = closed.filter(x => (x.pnl_usdt ?? 0) > 0).length;
    const winRate = closed.length > 0 ? (wins / closed.length) * 100 : 0;
    const totalClosedPnl = closed.reduce((acc, x) => acc + (x.pnl_usdt ?? 0), 0);
    const activePnl = activeItems.reduce((acc, x) => acc + x.pnl_usdt, 0);

    return {
      activeCount: activeItems.length,
      closedCount: closed.length,
      winRate: winRate.toFixed(1),
      totalClosedPnl: totalClosedPnl.toFixed(2),
      activePnl: activePnl.toFixed(2),
    };
  }, [activeItems, historyItems]);

  return (
    <>
      <div className="page-heading">
        <div>
          <p className="eyebrow">STRATEGY · SUPPORT & RESISTANCE LEVEL CAMPAIGN</p>
          <h1>BROKE-PB <span className="count">{stats.activeCount} активных</span></h1>
          <p>Торговля от уровней поддержки и сопротивления (30m Direction + виртуальный вход 500 USDT + Position Manager)</p>
        </div>
        <div style={{ display: 'flex', gap: '8px' }}>
          <button className={tab === 'active' ? 'primary' : ''} onClick={() => setTab('active')}>
            Открытые позиции ({stats.activeCount})
          </button>
          <button className={tab === 'history' ? 'primary' : ''} onClick={() => setTab('history')}>
            История сделок ({stats.closedCount})
          </button>
        </div>
      </div>

      {/* KPI METRICS */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: '14px', marginBottom: '20px' }}>
        <div style={{ background: 'var(--surface)', padding: '16px', borderRadius: '8px', border: '1px solid var(--border)' }}>
          <p className="subtle" style={{ margin: '0 0 6px 0', fontSize: '12px' }}>АКТИВНЫЙ PnL (UNREALIZED)</p>
          <p style={{ margin: 0, fontSize: '24px', fontWeight: 700, color: Number(stats.activePnl) >= 0 ? 'var(--good)' : 'var(--bad)' }}>
            {Number(stats.activePnl) >= 0 ? '+' : ''}{stats.activePnl} USDT
          </p>
        </div>
        <div style={{ background: 'var(--surface)', padding: '16px', borderRadius: '8px', border: '1px solid var(--border)' }}>
          <p className="subtle" style={{ margin: '0 0 6px 0', fontSize: '12px' }}>ЗАКРЫТЫЙ PnL (REALIZED)</p>
          <p style={{ margin: 0, fontSize: '24px', fontWeight: 700, color: Number(stats.totalClosedPnl) >= 0 ? 'var(--good)' : 'var(--bad)' }}>
            {Number(stats.totalClosedPnl) >= 0 ? '+' : ''}{stats.totalClosedPnl} USDT
          </p>
        </div>
        <div style={{ background: 'var(--surface)', padding: '16px', borderRadius: '8px', border: '1px solid var(--border)' }}>
          <p className="subtle" style={{ margin: '0 0 6px 0', fontSize: '12px' }}>WIN RATE (ИСТОРИЯ)</p>
          <p style={{ margin: 0, fontSize: '24px', fontWeight: 700 }}>
            {stats.winRate}% <small style={{ fontSize: '13px', fontWeight: 400 }} className="subtle">({stats.closedCount} сделок)</small>
          </p>
        </div>
      </div>

      <ErrorMessage error={activeQuery.error ?? historyQuery.error} />

      {tab === 'active' && (
        <Section title="Активные виртуальные позиции (BROKE-PB Position Manager)">
          {activeItems.length === 0 ? (
            <Empty>
              {activeQuery.isPending
                ? 'Загрузка позиций…'
                : 'Нет открытых позиций. Система ожидает касания верхней границы поддержки (при 30m LONG) или нижней границы сопротивления (при 30m SHORT).'}
            </Empty>
          ) : (
            <div className="table-scroll">
              <table className="screener">
                <thead>
                  <tr>
                    <th>Монета</th>
                    <th>Направление</th>
                    <th>Номинал</th>
                    <th>Цена входа</th>
                    <th>Текущая цена</th>
                    <th>PnL USDT</th>
                    <th>PnL %</th>
                    <th>Текущий SL</th>
                    <th>TP1 (50%)</th>
                    <th>Runner Цель</th>
                    <th>Position Manager</th>
                    <th>Время входа</th>
                  </tr>
                </thead>
                <tbody>
                  {activeItems.map(p => (
                    <tr key={p.id}>
                      <td className="sticky-symbol">
                        <strong>{p.symbol}</strong>
                      </td>
                      <td>
                        <Badge tone={p.direction === 'LONG' ? 'good' : 'bad'}>
                          {p.direction}
                        </Badge>
                      </td>
                      <td>{p.nominal_usdt} USDT</td>
                      <td>{formatPrice(p.entry_price)}</td>
                      <td><strong>{formatPrice(p.current_price)}</strong></td>
                      <td style={{ color: p.pnl_usdt >= 0 ? 'var(--good)' : 'var(--bad)', fontWeight: 600 }}>
                        {p.pnl_usdt >= 0 ? '+' : ''}{p.pnl_usdt.toFixed(2)} USDT
                      </td>
                      <td style={{ color: p.pnl_pct >= 0 ? 'var(--good)' : 'var(--bad)', fontWeight: 600 }}>
                        {p.pnl_pct >= 0 ? '+' : ''}{p.pnl_pct.toFixed(2)}%
                      </td>
                      <td>
                        <span style={{ color: p.tp1_hit ? 'var(--accent)' : 'inherit' }}>
                          {formatPrice(p.sl)} {p.tp1_hit ? '(BE)' : ''}
                        </span>
                      </td>
                      <td>{formatPrice(p.tp1)}</td>
                      <td>{formatPrice(p.runner)}</td>
                      <td>
                        <Badge tone={p.tp1_hit ? 'good' : p.underwater ? 'warning' : 'neutral'}>
                          {p.action}
                        </Badge>
                      </td>
                      <td>{timestamp(p.entry_time)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Section>
      )}

      {tab === 'history' && (
        <Section title="История закрытых сделок BROKE-PB">
          {historyItems.length === 0 ? (
            <Empty>{historyQuery.isPending ? 'Загрузка истории…' : 'История закрытых сделок пока пуста.'}</Empty>
          ) : (
            <div className="table-scroll">
              <table className="screener">
                <thead>
                  <tr>
                    <th>Монета</th>
                    <th>Направление</th>
                    <th>Номинал</th>
                    <th>Вход</th>
                    <th>Выход</th>
                    <th>PnL USDT</th>
                    <th>PnL %</th>
                    <th>Причина выхода</th>
                    <th>Время входа</th>
                    <th>Время закрытия</th>
                  </tr>
                </thead>
                <tbody>
                  {historyItems.map(p => (
                    <tr key={p.id}>
                      <td className="sticky-symbol">
                        <strong>{p.symbol}</strong>
                      </td>
                      <td>
                        <Badge tone={p.direction === 'LONG' ? 'good' : 'bad'}>
                          {p.direction}
                        </Badge>
                      </td>
                      <td>{p.nominal_usdt} USDT</td>
                      <td>{formatPrice(p.entry_price)}</td>
                      <td>{formatPrice(p.close_price)}</td>
                      <td style={{ color: (p.pnl_usdt ?? 0) >= 0 ? 'var(--good)' : 'var(--bad)', fontWeight: 600 }}>
                        {(p.pnl_usdt ?? 0) >= 0 ? '+' : ''}{(p.pnl_usdt ?? 0).toFixed(2)} USDT
                      </td>
                      <td style={{ color: (p.pnl_pct ?? 0) >= 0 ? 'var(--good)' : 'var(--bad)', fontWeight: 600 }}>
                        {(p.pnl_pct ?? 0) >= 0 ? '+' : ''}{(p.pnl_pct ?? 0).toFixed(2)}%
                      </td>
                      <td>
                        <Badge tone={(p.pnl_usdt ?? 0) >= 0 ? 'good' : 'bad'}>
                          {p.close_reason || 'CLOSED'}
                        </Badge>
                      </td>
                      <td>{timestamp(p.entry_time)}</td>
                      <td>{timestamp(p.close_time)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Section>
      )}
    </>
  );
}
