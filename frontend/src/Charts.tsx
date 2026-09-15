import { useState } from 'react';
import { format, timestamp, valueOf } from './model';
import type { Bar, Data, Snapshot } from './types';
import { Empty } from './common';
const W=1100,H=380,P=34,R=100;
export function CandleChart({bars,snapshot,events}:{bars:Bar[];snapshot?:Snapshot;events:Data[]}){
 const [count,setCount]=useState(100),[back,setBack]=useState(0);
 const candles=bars.slice(Math.max(0,bars.length-back-count),Math.max(0,bars.length-back)).filter(x=>[x.open,x.high,x.low,x.close].every(Number.isFinite));
 if(!candles.length)return <Empty>Свечи ещё не доступны. OHLCV не заменяются синтетическими данными.</Empty>;
 const m=snapshot?.metrics??{};
 const zones=[['R1','resistanceBottom1','resistanceTop1','#e97178'],['R2','resistanceBottom2','resistanceTop2','#ba656d'],['S1','supportBottom1','supportTop1','#39bfab'],['S2','supportBottom2','supportTop2','#3c8f88'],['LOCKED','lockedZoneBottom','lockedZoneTop','#d6ab62'],['CONSUMED','recentLifecycleZoneBottom','recentLifecycleZoneTop','#9e7fdc']];
 const levels=[['SL',snapshot?.sl,'#f06f78'],['T1',snapshot?.t1,'#63a6ff']];
 const min=Math.min(...candles.map(x=>x.low)),max=Math.max(...candles.map(x=>x.high)),pad=(max-min)*.08||max*.01||1;
 const y=(price:number)=>H-P-(price-min+pad)/(max-min+2*pad)*(H-2*P);
 const width=(W-P-R)/candles.length,x=(i:number)=>P+(i+.5)*width;
 const first=candles[0].start,last=candles.at(-1)!.end;
 return <><div className="chart-controls"><span>OHLCV · UTC · {candles.length} свечей</span><label>Масштаб <input aria-label="Количество свечей" type="range" min="30" max="500" step="10" value={count} onChange={e=>setCount(Number(e.target.value))}/></label><label>История <input aria-label="Сдвиг истории" type="range" min="0" max={Math.max(0,bars.length-count)} step="1" value={Math.min(back,Math.max(0,bars.length-count))} onChange={e=>setBack(Number(e.target.value))}/></label><button onClick={()=>setBack(0)}>Последние</button></div>
 <svg className="candle-chart" viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Свечной график, структурные зоны, SL, T1 и события">
 <defs><clipPath id="plot-clip"><rect x={P} y={P/2} width={W-P-R} height={H-P}/></clipPath></defs>
 {[0,.25,.5,.75,1].map(t=>{const price=min-pad+t*(max-min+2*pad);return <g key={t}><line x1={P} x2={W-R} y1={y(price)} y2={y(price)} stroke="#233044" strokeDasharray="3 5"/><text x={W-R+8} y={y(price)+4}>{format(price)}</text></g>;})}
 <g clipPath="url(#plot-clip)">{zones.map(([label,bottom,top,color])=>typeof m[bottom]==='number'&&typeof m[top]==='number'?<g key={label}><rect x={P} y={y(m[top] as number)} width={W-P-R} height={Math.max(1,y(m[bottom] as number)-y(m[top] as number))} fill={color} opacity="0.13"/><line x1={P} x2={W-R} y1={y(m[top] as number)} y2={y(m[top] as number)} stroke={color} opacity=".5"/><text x={P+6} y={y(m[top] as number)+13} fill={color}>{label}</text></g>:null)}
 {candles.map((bar,i)=>{const color=bar.close>=bar.open?'#39c8ac':'#ee7386';return <g key={bar.start}><title>{timestamp(bar.start)}{'\n'}O {format(bar.open)} H {format(bar.high)} L {format(bar.low)} C {format(bar.close)}{'\n'}Volume {format(bar.volume)} · {bar.confirmed===false?'realtime':'confirmed'}</title><line x1={x(i)} x2={x(i)} y1={y(bar.high)} y2={y(bar.low)} stroke={color}/><rect x={x(i)-Math.max(1,width*.65)/2} width={Math.max(1,width*.65)} y={y(Math.max(bar.open,bar.close))} height={Math.max(1,Math.abs(y(bar.open)-y(bar.close)))} fill={color} opacity={bar.confirmed===false?.6:1}/></g>;})}
 {levels.map(([label,value,color])=>typeof value==='number'?<g key={String(label)}><line x1={P} x2={W-R} y1={y(value)} y2={y(value)} stroke={String(color)} strokeDasharray="7 4"/><text x={W-R-6} y={y(value)-5} textAnchor="end" fill={String(color)}>{String(label)} {format(value)}</text></g>:null)}
 {events.filter(event=>{const time=Number(event.bar_start??event.event_time??event.timestamp);return time>=first&&time<=last;}).map((event,i)=>{const time=Number(event.bar_start??event.event_time??event.timestamp),index=Math.max(0,candles.findIndex(b=>b.start<=time&&time<b.end)),cx=x(index),cy=y(candles[index].high)-12;return <g key={i}><title>{format(event.event??event.kind??event.type??event.event_type??event.signal)} · {timestamp(time)}</title><path d={`M ${cx} ${cy+6} l -5 -9 h 10 Z`} fill="#eac470"/><text x={cx} y={cy-7} fontSize="8" textAnchor="middle">{String(event.event??event.kind??event.type??event.event_type??event.signal??'EVENT')}</text></g>;})}</g>
 <text x={P} y={H-5}>{timestamp(first)}</text><text x={W-R} y={H-5} textAnchor="end">{timestamp(last)}</text>
 </svg><p className="subtle chart-note">Зоны и метки отображаются только при наличии соответствующих значений engine. Наведите на свечу или метку для подробностей.</p></>;
}
export function MetricChart({items,metric,label}:{items:Snapshot[];metric:string;label:string}){
 const valid=items.filter(x=>typeof valueOf(x,metric)==='number'&&x.event_time).sort((a,b)=>(a.event_time??0)-(b.event_time??0));
 if(!valid.length)return <div className="metric-chart"><h3>{label}</h3><div className="subtle">История недоступна</div></div>;
 const values=valid.map(x=>valueOf(x,metric) as number),low=Math.min(...values),high=Math.max(...values),begin=valid[0].event_time!,end=valid.at(-1)!.event_time!;
 const points=valid.map(item=>`${10+(item.event_time!-begin)/(end-begin||1)*360},${74-((valueOf(item,metric) as number)-low)/(high-low||1)*55}`).join(' ');
 return <div className="metric-chart"><h3>{label}<span>{format(values.at(-1))}</span></h3><svg viewBox="0 0 400 92" role="img" aria-label={`${label}, ${timestamp(begin)} — ${timestamp(end)}`}><text x="375" y="20">{format(high)}</text><text x="375" y="76">{format(low)}</text><polyline points={points} fill="none" stroke="#64b5dc" strokeWidth="1.5"/>{valid.length===1&&<circle cx="10" cy="74" r="3" fill="#64b5dc"/>}</svg><small>{timestamp(begin)} → {timestamp(end)}</small></div>;
}
