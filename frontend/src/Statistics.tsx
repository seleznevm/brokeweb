import {useState} from 'react';
import {useQuery} from '@tanstack/react-query';
import {api} from './api';
import {useDisplayTime} from './Timezone';
import {parseDisplayDate} from './displayTime';
import {Section,ErrorMessage} from './common';
type Rates={total:number;resolved:number;counts:Record<string,number>;winrate:number|null;horizon_success:number|null;coverage:number|null};
type Item={id:string;source:string;family:string;symbol:string;direction:string;event_time:number;status:string;plan:{entry:number|null;sl:number|null;parameter_version:string|null};outcome:{reason?:string;mfe_pct?:number;mae_pct?:number;elapsed_minutes?:number}};
type Result=Rates&{items:Item[];breakdowns:(Rates&{source:string;family:string})[];last_update:number|null};
const pct=(v:number|null|undefined)=>v==null?'н/д':v.toFixed(1)+'%';
export function Statistics(){
 const {offset,label,timestamp}=useDisplayTime();
 const [strategy,setStrategy]=useState('BROKE'),[source,setSource]=useState(''),[family,setFamily]=useState(''),[mode,setMode]=useState('live');
 const [days,setDays]=useState('7'),[start,setStart]=useState(''),[end,setEnd]=useState(''),[symbol,setSymbol]=useState(''),[direction,setDirection]=useState(''),[page,setPage]=useState(0);
 const [rangeEnd,setRangeEnd]=useState(Date.now());
 const from=days==='custom'?parseDisplayDate(start,offset):rangeEnd-Number(days)*86400000;
 const until=days==='custom'?parseDisplayDate(end,offset):rangeEnd;
 const valid=Number.isFinite(from)&&Number.isFinite(until)&&from<until;
 const params=new URLSearchParams({strategy,mode,start:String(from),end:String(until),limit:'50',offset:String(page*50)});
 if(source)params.set('source',source);if(family)params.set('family',family);if(symbol)params.set('symbol',symbol.trim());if(direction)params.set('direction',direction);
 const q=useQuery({queryKey:['statistics',params.toString()],queryFn:()=>api<Result>('/api/statistics?'+params),enabled:valid,refetchInterval:30000});
 const data=q.data;const reset=()=>setPage(0);
 return <Section title="STATISTICS">
 <p>WIN: +2% раньше исходного SL. После этого сигнал остаётся WIN при возврате в безубыток. Это оценка сигнала, а не фактический доход. Горизонт — 24 часа.</p>
 <div className="toolbar">
 <label>Стратегия <select value={strategy} onChange={e=>{setStrategy(e.target.value);setFamily('');setSource('');reset();}}><option>BROKE</option><option>WT_SETUPS</option></select></label>
 <label>Источник <select value={source} onChange={e=>{setSource(e.target.value);reset();}}><option value="">Все источники (раздельно в таблице)</option><option value="engine">Расчёт системы</option>{strategy==='WT_SETUPS'&&<option value="tradingview">Сигналы TradingView</option>}</select></label>
 <label>Сигнал <select value={family} onChange={e=>{setFamily(e.target.value);reset();}}><option value="">Все</option>{(strategy==='BROKE'?['WE','PINE READY']:['T1','T2','T3','T4']).map(v=><option key={v}>{v}</option>)}</select></label>
 <label>Выборка <select value={mode} onChange={e=>{setMode(e.target.value);reset();}}><option value="live">Live</option><option value="replay">Исторический replay</option></select></label>
 <label>Период <select value={days} onChange={e=>{setDays(e.target.value);setRangeEnd(Date.now());reset();}}>{['1','7','30','90'].map(v=><option key={v} value={v}>{v} дн.</option>)}<option value="custom">Свой диапазон</option></select></label>
 {days==='custom'&&<><label>От ({label}) <input type="datetime-local" value={start} onChange={e=>{setStart(e.target.value);reset();}}/></label><label>До (не включая) <input type="datetime-local" value={end} onChange={e=>{setEnd(e.target.value);reset();}}/></label></>}
 <label>Монета <input value={symbol} placeholder="BTCUSDT" onChange={e=>{setSymbol(e.target.value);reset();}}/></label>
 <label>Направление <select value={direction} onChange={e=>{setDirection(e.target.value);reset();}}><option value="">Все</option><option>LONG</option><option>SHORT</option></select></label>
 <button onClick={()=>{setRangeEnd(Date.now());void q.refetch();}}>Обновить</button>
 {valid&&<a href={'/api/statistics?'+params+'&export=csv'}>Экспорт CSV</a>}
 </div>
 {!valid&&<p>Укажите корректный диапазон дат в {label}.</p>}<ErrorMessage error={q.error}/>
 {q.isLoading&&<p>Загрузка…</p>}
 {data&&<>
 <p>Наблюдений: <b>{data.total}</b> · WIN: <b>{data.counts.WIN??0}</b> · LOSS: <b>{data.counts.LOSS??0}</b> · Winrate W/(W+L): <b>{pct(data.winrate)}</b> · W/(W+L+EXPIRED): {pct(data.horizon_success)} · Полные исходы: {pct(data.coverage)}</p>
 <p>{Object.entries(data.counts).filter(([k])=>!['WIN','LOSS'].includes(k)).map(([k,n])=>k+': '+n).join(' · ')||'Нет незавершённых наблюдений'} · Расчёт: {timestamp(data.last_update)}</p>
 <p>WT: один сигнал с T1+T3 даёт отдельное наблюдение каждой семьи. Сумма — число наблюдений, не независимых сделок. Сигналы TradingView оцениваются с момента доставки; точного времени возникновения в тексте нет.</p>
 <div className="table-scroll"><table><thead><tr><th>Источник</th><th>Сигнал</th><th>Всего</th><th>WIN</th><th>LOSS</th><th>Winrate</th><th>Полные исходы</th></tr></thead><tbody>{data.breakdowns.map(r=><tr key={r.source+r.family}><td>{r.source==='engine'?'Расчёт системы':'TradingView'}</td><td>{r.family}</td><td>{r.total}</td><td>{r.counts.WIN??0}</td><td>{r.counts.LOSS??0}</td><td>{pct(r.winrate)}</td><td>{pct(r.coverage)}</td></tr>)}</tbody></table></div>
 <p>AMBIGUOUS — неизвестен порядок касаний; DATA_GAP — не хватает минутных данных; INVALID — нет корректного Entry/SL; EXPIRED — 24 часа без достижения уровней. Они не входят в W/(W+L). MFE/MAE — экстремумы полных минут до определения исхода, включая минуту касания.</p>
 <div className="table-scroll"><table><thead><tr><th>Время</th><th>Источник</th><th>Сигнал</th><th>Монета</th><th>Сторона</th><th>Entry / SL</th><th>Исход</th><th>MFE / MAE</th></tr></thead><tbody>{data.items.map(r=><tr key={r.id}><td>{timestamp(r.event_time)}</td><td>{r.source}</td><td>{r.family}</td><td>{r.symbol}</td><td>{r.direction}</td><td>{r.plan.entry??'н/д'} / {r.plan.sl??'н/д'}</td><td title={r.outcome.reason}>{r.status}{r.outcome.reason&&<small> — {r.outcome.reason}</small>}</td><td>{pct(r.outcome.mfe_pct)} / {pct(r.outcome.mae_pct)}</td></tr>)}</tbody></table></div>
 {!data.total&&<p>В выбранной выборке сигналов нет. Исторические пересчёты находятся в отдельной выборке replay.</p>}
 <div className="toolbar"><button disabled={!page} onClick={()=>setPage(page-1)}>Назад</button><span>Страница {page+1}</span><button disabled={(page+1)*50>=data.total} onClick={()=>setPage(page+1)}>Далее</button></div>
 </>}
 </Section>;
}
