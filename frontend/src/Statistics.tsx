import {Fragment,useState} from 'react';
import {useQuery} from '@tanstack/react-query';
import {api} from './api';
import {useDisplayTime} from './Timezone';
import {parseDisplayDate} from './displayTime';
import {Section,ErrorMessage} from './common';
import {PerformanceSummary,policies,policyLabel,metricNumber,type Performance} from './StatisticsMetrics';
import {StatisticsTradeChart,TelegramStatus,type TelegramEvidence} from './StatisticsTradeChart';
type Rates={total:number;resolved:number;counts:Record<string,number>;winrate:number|null;horizon_success:number|null;coverage:number|null};
type Item={id:string;strategy:string;source:string;family:string;symbol:string;timeframe:string;direction:string;event_time:number;status:string;telegram?:TelegramEvidence|null;plan:{entry:number|null;sl:number|null;t1:number|null;parameter_version:string|null};outcome:{reason?:string;mfe_pct?:number;mae_pct?:number;elapsed_minutes?:number;net_r?:number|null;economics_status?:string}};
type Result=Rates&{items:Item[];breakdowns:(Rates&{strategy:string;source:string;family:string;group?:string;performance:Performance})[];performance:Performance;last_update:number|null};
const pct=(v:number|null|undefined)=>v==null?'н/д':v.toFixed(1)+'%';
export function Statistics(){
 const {offset,label,timestamp}=useDisplayTime();
 const [strategies,setStrategies]=useState(['BROKE_SETUPS','BROKE_PB']);
 const [expanded,setExpanded]=useState<string>();
 const [source,setSource]=useState(''),[family,setFamily]=useState(''),[mode,setMode]=useState('live');
 const [policy,setPolicy]=useState('frozen-t1-before-sl-24h-1m-v1'),[group,setGroup]=useState('family'),[timeframe,setTimeframe]=useState(''),[path,setPath]=useState(''),[version,setVersion]=useState(''),[quality,setQuality]=useState('');
 const [engineVersion,setEngineVersion]=useState('');
 const [days,setDays]=useState('7'),[start,setStart]=useState(''),[end,setEnd]=useState(''),[symbol,setSymbol]=useState(''),[direction,setDirection]=useState(''),[page,setPage]=useState(0);
 const [rangeEnd,setRangeEnd]=useState(Date.now());
 const from=days==='custom'?parseDisplayDate(start,offset):rangeEnd-Number(days)*86400000;
 const until=days==='custom'?parseDisplayDate(end,offset):rangeEnd;
 const valid=Number.isFinite(from)&&Number.isFinite(until)&&from<until;
 const params=new URLSearchParams({strategies:strategies.join(','),mode,policy,group_by:group,start:String(from),end:String(until),limit:'50',offset:String(page*50)});
 for(const [key,value] of Object.entries({timeframe,path,version,quality,engine_version:engineVersion}))if(value)params.set(key,value);
 if(source)params.set('source',source);if(family)params.set('family',family);if(symbol)params.set('symbol',symbol.trim());if(direction)params.set('direction',direction);
 const q=useQuery({queryKey:['statistics',params.toString()],queryFn:()=>api<Result>('/api/statistics?'+params),enabled:valid,refetchInterval:30000});
 const data=q.data;const reset=()=>{setPage(0);setExpanded(undefined);};
 return <Section title="STATISTICS">
 <p>BROKE_SETUPS: вход по WE или PINE READY на 5m и 30m; {policyLabel(policy)}; WIN — цель раньше исходного SL. BROKE_PB: сохранённые paper-сделки с дозакупками и частичными выходами; WIN / LOSS определяются по результату кампании. Старые сделки без расходов отмечены LEGACY_COSTS_UNKNOWN.</p>
 <div className="toolbar">
 {['BROKE_SETUPS','BROKE_PB'].map(v=><button key={v} className="strategy-filter" aria-pressed={strategies.includes(v)} onClick={()=>{setStrategies(old=>old.includes(v)?old.filter(s=>s!==v):['BROKE_SETUPS','BROKE_PB'].filter(s=>s===v||old.includes(s)));reset();}}>{v}</button>)}
 <label>Политика <select value={policy} onChange={e=>{setPolicy(e.target.value);reset();}}>{policies.map(v=><option key={v} value={v}>{policyLabel(v)}</option>)}</select></label>
 <label>Группировать <select value={group} onChange={e=>setGroup(e.target.value)}>{[['family','Семья'],['direction','LONG / SHORT'],['path','Путь входа'],['timeframe','Таймфрейм']].map(([v,title])=><option key={v} value={v}>{title}</option>)}</select></label>
 <label>Источник <select value={source} onChange={e=>{setSource(e.target.value);reset();}}><option value="">Все источники (раздельно в таблице)</option><option value="engine">Расчёт системы</option></select></label>
 <label>Сигнал <select value={family} onChange={e=>{setFamily(e.target.value);reset();}}><option value="">Все</option>{['WE','PINE READY','LEVEL CAMPAIGN'].map(v=><option key={v}>{v}</option>)}</select></label>
 <label>Выборка <select value={mode} onChange={e=>{setMode(e.target.value);reset();}}><option value="live">Live</option><option value="replay">Исторический replay</option></select></label>
 <label>Период <select value={days} onChange={e=>{setDays(e.target.value);setRangeEnd(Date.now());reset();}}>{['1','7','30','90'].map(v=><option key={v} value={v}>{v} дн.</option>)}<option value="custom">Свой диапазон</option></select></label>
 {days==='custom'&&<><label>От ({label}) <input type="datetime-local" value={start} onChange={e=>{setStart(e.target.value);reset();}}/></label><label>До (не включая) <input type="datetime-local" value={end} onChange={e=>{setEnd(e.target.value);reset();}}/></label></>}
 <label>Монета <input value={symbol} placeholder="BTCUSDT" onChange={e=>{setSymbol(e.target.value);reset();}}/></label>
 <label>TF <input value={timeframe} placeholder="30" onChange={e=>{setTimeframe(e.target.value);reset();}}/></label>
 <label>Путь <input value={path} placeholder="BREAKOUT" onChange={e=>{setPath(e.target.value);reset();}}/></label>
 <label>Версия параметров <input value={version} onChange={e=>{setVersion(e.target.value);reset();}}/></label>
 <label>Версия движка <input value={engineVersion} onChange={e=>{setEngineVersion(e.target.value);reset();}}/></label>
 <label>Качество данных <select value={quality} onChange={e=>{setQuality(e.target.value);reset();}}><option value="">Все</option>{['HEALTHY','RECOVERING','DEGRADED','STALE'].map(v=><option key={v}>{v}</option>)}</select></label>
 <label>Направление <select value={direction} onChange={e=>{setDirection(e.target.value);reset();}}><option value="">Все</option><option>LONG</option><option>SHORT</option></select></label>
 <button onClick={()=>{setRangeEnd(Date.now());void q.refetch();}}>Обновить</button>
 {valid&&<a href={'/api/statistics?'+params+'&export=csv'}>Экспорт CSV</a>}
 </div>
 {!valid&&<p>Укажите корректный диапазон дат в {label}.</p>}<ErrorMessage error={q.error}/>
 {q.isLoading&&<p>Загрузка…</p>}
 {data&&<>
 <PerformanceSummary data={data.performance}/>
 <p>Наблюдений: <b>{data.total}</b> · <span className="statistics-win">WIN: <b>{data.counts.WIN??0}</b></span> · <span className="statistics-loss">LOSS: <b>{data.counts.LOSS??0}</b></span> · Winrate W/(W+L): <b>{pct(data.winrate)}</b> · W/(W+L+EXPIRED): {pct(data.horizon_success)} · Полные исходы: {pct(data.coverage)}</p>
 <p>{Object.entries(data.counts).filter(([k])=>!['WIN','LOSS'].includes(k)).map(([k,n])=>k+': '+n).join(' · ')||'Нет незавершённых наблюдений'} · Расчёт: {timestamp(data.last_update)}</p>
 <div className="table-scroll"><table><thead><tr><th>Стратегия</th><th>Источник</th><th>Группа</th><th>Всего</th><th className="statistics-win">WIN</th><th className="statistics-loss">LOSS</th><th>Winrate</th><th>Полные исходы</th><th>Средний net R</th></tr></thead><tbody>{data.breakdowns.map((r,i)=><tr key={i}><td>{r.strategy}</td><td>{r.source==='engine'?'Расчёт системы':'TradingView'}</td><td>{r.group??r.family}</td><td>{r.total}</td><td className="statistics-win">{r.counts.WIN??0}</td><td className="statistics-loss">{r.counts.LOSS??0}</td><td>{pct(r.winrate)}</td><td>{pct(r.coverage)}</td><td>{metricNumber(r.performance.expectancy_r)}</td></tr>)}</tbody></table></div>
 <p>PENDING — ожидает первого расчёта; OPEN — наблюдение продолжается; AMBIGUOUS — неизвестен порядок касаний; DATA_GAP — не хватает минутных данных; INVALID — нет корректного плана; EXPIRED — горизонт завершён; MANAGED_EXIT — выход по сигналу управления. Они не входят в W/(W+L). MFE/MAE включают полную минуту исхода. Недоступный funding исключает исход из net-метрик. WIN может иметь отрицательный net R после расходов.</p>
 <div className="table-scroll"><table className="statistics-trades"><thead><tr><th>График</th><th>Время</th><th>Стратегия</th><th>Сигнал</th><th>Монета / TF</th><th>Сторона</th><th>Entry / SL или риск / T1</th><th>Исход</th><th>Net R</th><th>MFE / MAE</th><th>Telegram</th></tr></thead><tbody>{data.items.map(r=><Fragment key={r.id}><tr className="statistics-trade-row" onClick={()=>setExpanded(expanded===r.id?undefined:r.id)}><td><button aria-label={`График сделки ${r.symbol} ${timestamp(r.event_time)}`} aria-expanded={expanded===r.id} onClick={e=>{e.stopPropagation();setExpanded(expanded===r.id?undefined:r.id);}}>{expanded===r.id?'Свернуть':'График'}</button></td><td>{timestamp(r.event_time)}</td><td>{r.strategy}</td><td>{r.family}</td><td>{r.symbol} / {r.timeframe}m</td><td>{r.direction}</td><td>{r.plan.entry??'н/д'} / {r.plan.sl??'н/д'} / {r.plan.t1??'н/д'}</td><td className={r.status==='WIN'?'statistics-win':r.status==='LOSS'?'statistics-loss':undefined} title={r.outcome.reason}>{r.status}{r.outcome.reason&&<small> — {r.outcome.reason}</small>}</td><td title={r.outcome.economics_status}>{metricNumber(r.outcome.net_r)}</td><td>{pct(r.outcome.mfe_pct)} / {pct(r.outcome.mae_pct)}</td><td><TelegramStatus value={r.telegram}/></td></tr>{expanded===r.id&&<tr><td colSpan={11}><StatisticsTradeChart id={r.id}/></td></tr>}</Fragment>)}</tbody></table></div>
 {!data.total&&<p>В выбранной выборке сигналов нет. Исторические пересчёты находятся в отдельной выборке replay.</p>}
 <div className="toolbar"><button disabled={!page} onClick={()=>setPage(page-1)}>Назад</button><span>Страница {page+1}</span><button disabled={(page+1)*50>=data.total} onClick={()=>setPage(page+1)}>Далее</button></div>
 </>}
 </Section>;
}
