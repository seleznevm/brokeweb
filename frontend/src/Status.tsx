import { useQuery } from '@tanstack/react-query';
import { api } from './api';
import { Badge, Empty, ErrorMessage, JsonView, Section } from './common';
import { format } from './model';
import type { Data } from './types';
export function Status({kind}:{kind:'health'|'parity'}){
 const {data,error,isPending}=useQuery({queryKey:[kind],queryFn:()=>api<Data>(`/api/${kind}`),refetchInterval:10000});
 const parity=kind==='parity';
 return <><div className="page-heading"><div><p className="eyebrow">{parity?'PINE ↔ PYTHON':'SYSTEM · SOURCE COMPLETENESS'}</p><h1>{parity?'Parity validation':'Data health'}</h1><p>{parity?'Совпадение с TradingView подтверждается внешним reference export.':'Состояние источников, прогрев engine, полнота OHLCV и задержки.'}</p></div><Badge tone={data?.status==='ok'||data?.status==='PASS'?'good':'neutral'}>{format(data?.status)}</Badge></div><ErrorMessage error={error}/>{isPending&&<Empty>Загрузка…</Empty>}
 {parity&&data?.status!=='PASS'&&data?.status!=='VERIFIED'&&<div className="parity-notice"><strong>PARITY {format(data?.status??'UNVERIFIED')}</strong><p>{data?.observed_status==='PASS'?'Доступные значения и метки TradingView прошли сравнение. Полная проверка внутренних оценок, состояний и внутрисвечных сигналов ещё не завершена.':data?.observed_status==='FAIL'?'Сравнение доступных данных TradingView обнаружило расхождения. Подробности приведены ниже.':'Полное совпадение с TradingView ещё не подтверждено внешним сравнением.'}</p></div>}
 {data&&Object.entries(data).filter(([key])=>key!=='status').map(([key,value])=><Section title={key.replaceAll('_',' ')} key={key}>{Array.isArray(value)?value.length?<div className="table-scroll"><table><thead><tr>{[...new Set(value.flatMap(x=>typeof x==='object'&&x?Object.keys(x):['value']))].map(k=><th key={k}>{k}</th>)}</tr></thead><tbody>{value.map((item,i)=><tr key={i}>{[...new Set(value.flatMap(x=>typeof x==='object'&&x?Object.keys(x):['value']))].map(k=><td key={k} className="wrap">{format(typeof item==='object'&&item?item[k]:item)}</td>)}</tr>)}</tbody></table></div>:<Empty>Записи отсутствуют.</Empty>:typeof value==='object'&&value?<JsonView data={value}/>:<p className="status-value">{format(value)}</p>}</Section>)}
 </>;
}
