import { useQuery } from '@tanstack/react-query';
import { api } from './api';
import { Badge, Empty, ErrorMessage, JsonView, Section } from './common';
import { format } from './model';
import type { Data } from './types';
export function Status({kind}:{kind:'health'|'parity'}){
 const {data,error,isPending}=useQuery({queryKey:[kind],queryFn:()=>api<Data>(`/api/${kind}`),refetchInterval:10000});
 const parity=kind==='parity';
 const webhook=useQuery({queryKey:['parity','tradingview'],queryFn:()=>api<Data>('/api/parity/tradingview'),enabled:parity,refetchInterval:30000});
 const intrabar=useQuery({queryKey:['parity','intrabar'],queryFn:()=>api<Data>('/api/parity/intrabar'),enabled:parity,refetchInterval:30000});
 return <><div className="page-heading"><div><p className="eyebrow">{parity?'PINE ↔ PYTHON':'SYSTEM · SOURCE COMPLETENESS'}</p><h1>{parity?'Parity validation':'Data health'}</h1><p>{parity?'Совпадение с TradingView подтверждается внешним reference export.':'Состояние источников, прогрев engine, полнота OHLCV и задержки.'}</p></div><Badge tone={data?.status==='ok'||data?.status==='PASS'?'good':'neutral'}>{format(data?.status)}</Badge></div><ErrorMessage error={error}/>{isPending&&<Empty>Загрузка…</Empty>}
 {parity&&data?.status!=='PASS'&&data?.status!=='VERIFIED'&&<div className="parity-notice"><strong>PARITY {format(data?.status??'UNVERIFIED')}</strong><p>{data?.observed_status==='PASS'?'Доступные значения и метки TradingView прошли сравнение. Полная проверка внутренних оценок, состояний и внутрисвечных сигналов ещё не завершена.':data?.observed_status==='FAIL'?'Сравнение доступных данных TradingView обнаружило расхождения. Подробности приведены ниже.':'Полное совпадение с TradingView ещё не подтверждено внешним сравнением.'}</p></div>}
 {parity&&<Section title="TradingView webhook · сигналы 30m"><ErrorMessage error={webhook.error}/><p>Обычные сообщения не содержат время свечи. Кандидаты в пределах ±90 секунд от получения служат для диагностики; полная parity остаётся UNVERIFIED. Повторные доставки сохранены отдельно.</p>{webhook.data&&<><JsonView data={webhook.data.counts}/><details><summary>Сопоставления и отклонения метрик</summary><JsonView data={webhook.data.items}/></details></>}<a href="/api/tradingview/alerts" target="_blank" rel="noreferrer">Исходные сообщения</a></Section>}
 {parity&&<ErrorMessage error={intrabar.error}/>}
 {parity&&intrabar.data&&typeof intrabar.data.sessions==='number'&&intrabar.data.sessions>0&&<Section title="Intrabar — отдельная проверка">
  <p>Монет: {format(intrabar.data.symbols)} · обновлений: {format(intrabar.data.rows)} · полных свечей: {format(intrabar.data.complete_bars)} · потерянных обновлений: {format(intrabar.data.reported_dropped_updates)}</p>
  <p>Сессий с отсутствующим началом: {format(intrabar.data.sessions_with_missing_prefix)}. Нулевой dropped не подтверждает наличие первых пакетов в экспорте.</p>
  <p>Файл: <code>{format(intrabar.data.input_file)}</code></p>
  {intrabar.data.ta_selection==='clean_bar_suffix'&&<p>ATR/EMA проверены отдельно на непрерывной части записи после стартовых пропусков: {format(intrabar.data.ta_diagnostic_matched_updates)} обновлений. Пропуски исходной записи сохранены в отчёте.</p>}
  <p>Полная intrabar parity: {format(intrabar.data.full_intrabar_status)}. Совпадение ATR/EMA с ограниченным прогревом не подтверждает FSM, начальное состояние и сигналы. Повторяющиеся сигнальные флаги не равны числу уведомлений.</p>
  <JsonView data={intrabar.data.ta_diagnostic_status_counts??{}}/>
  {!!intrabar.data.request_component_status_counts&&<>
   <p>24h Volume, MTF Trend, HTF Base и BTC Shock: проверено {format(intrabar.data.request_component_compared_updates)} обновлений по записанным результатам запросов. Это проверка последующих формул; вычисления самих запросов и FSM остаются непроверенными.</p>
   <JsonView data={intrabar.data.request_component_status_counts}/>
  </>}
  <a href="/api/parity/intrabar?include_sessions=true" target="_blank" rel="noreferrer">Полный отчёт по монетам</a>
 </Section>}
 {data&&Object.entries(data).filter(([key])=>key!=='status').map(([key,value])=><Section title={key.replaceAll('_',' ')} key={key}>{Array.isArray(value)?value.length?<div className="table-scroll"><table><thead><tr>{[...new Set(value.flatMap(x=>typeof x==='object'&&x?Object.keys(x):['value']))].map(k=><th key={k}>{k}</th>)}</tr></thead><tbody>{value.map((item,i)=><tr key={i}>{[...new Set(value.flatMap(x=>typeof x==='object'&&x?Object.keys(x):['value']))].map(k=><td key={k} className="wrap">{format(typeof item==='object'&&item?item[k]:item)}</td>)}</tr>)}</tbody></table></div>:<Empty>Записи отсутствуют.</Empty>:typeof value==='object'&&value?<JsonView data={value}/>:<p className="status-value">{format(value)}</p>}</Section>)}
 </>;
}
