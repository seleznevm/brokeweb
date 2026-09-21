import {useChartView,W,H,P,R} from './useChartView';
import { format, formatField, timestamp, valueOf } from './model';
import type { Bar, Data, Snapshot } from './types';
import { Empty } from './common';
export function CandleChart({bars,snapshot,events}:{bars:Bar[];snapshot?:Snapshot;events:Data[]}){
 const valid=bars.filter(x=>[x.open,x.high,x.low,x.close].every(Number.isFinite));
 const view=useChartView(valid),candles=valid.slice(view.start,view.end);
 if(!candles.length)return <Empty>Свечи ещё не доступны. OHLCV не заменяются синтетическими данными.</Empty>;
 const m=snapshot?.metrics??{};
 const zones=[['R1','resistanceBottom1','resistanceTop1','#e97178'],['R2','resistanceBottom2','resistanceTop2','#ba656d'],['S1','supportBottom1','supportTop1','#39bfab'],['S2','supportBottom2','supportTop2','#3c8f88'],['LOCKED','lockedZoneBottom','lockedZoneTop','#d6ab62'],['CONSUMED','recentLifecycleZoneBottom','recentLifecycleZoneTop','#9e7fdc']];
 const levels=[['SL',snapshot?.sl,'#f06f78'],['T1',snapshot?.t1,'#63a6ff']];
 const {y,width}=view,x=(i:number)=>view.x(view.start+i);
 const first=candles[0].start,last=candles.at(-1)!.end;
 return <><div className="chart-controls"><span>OHLCV · UTC · {candles.length} свечей</span><button onClick={view.reset}>Сбросить вид</button></div>
 <svg ref={view.ref} {...view.props} className="candle-chart interactive-chart" viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Свечной график, структурные зоны, SL, T1 и события" data-visible-bars={candles.length} data-price-low={view.range.low} data-price-high={view.range.high}>

 <defs><clipPath id={view.id}><rect x={P} y={P/2} width={W-P-R} height={H-P}/></clipPath></defs>
 {[0,.25,.5,.75,1].map(t=>{const price=view.range.low+t*(view.range.high-view.range.low);return <g key={t}><line x1={P} x2={W-R} y1={y(price)} y2={y(price)} stroke="#233044" strokeDasharray="3 5"/><text x={W-R+8} y={y(price)+4}>{format(price,8)}</text></g>;})}
 <g clipPath={`url(#${view.id})`}>{zones.map(([label,bottom,top,color])=>typeof m[bottom]==='number'&&typeof m[top]==='number'?<g key={label}><rect x={P} y={y(m[top] as number)} width={W-P-R} height={Math.max(1,y(m[bottom] as number)-y(m[top] as number))} fill={color} opacity="0.13"/><line x1={P} x2={W-R} y1={y(m[top] as number)} y2={y(m[top] as number)} stroke={color} opacity=".5"/><text x={P+6} y={y(m[top] as number)+13} fill={color}>{label}</text></g>:null)}
 {candles.map((bar,i)=>{const color=bar.close>=bar.open?'#39c8ac':'#ee7386';return <g key={bar.start}><title>{timestamp(bar.start)}{'\n'}O {format(bar.open,8)} H {format(bar.high,8)} L {format(bar.low,8)} C {format(bar.close,8)}{'\n'}Volume {format(bar.volume)} · {bar.confirmed===false?'realtime':'confirmed'}</title><line x1={x(i)} x2={x(i)} y1={y(bar.high)} y2={y(bar.low)} stroke={color}/><rect x={x(i)-Math.max(1,width*.65)/2} width={Math.max(1,width*.65)} y={y(Math.max(bar.open,bar.close))} height={Math.max(1,Math.abs(y(bar.open)-y(bar.close)))} fill={color} opacity={bar.confirmed===false?.6:1}/></g>;})}
 {levels.map(([label,value,color])=>typeof value==='number'?<g key={String(label)}><line x1={P} x2={W-R} y1={y(value)} y2={y(value)} stroke={String(color)} strokeDasharray="7 4"/><text x={W-R-6} y={y(value)-5} textAnchor="end" fill={String(color)}>{String(label)} {format(value,8)}</text></g>:null)}
 {events.filter(event=>{const time=Number(event.bar_start??event.event_time??event.timestamp);return time>=first&&time<last;}).map((event,i)=>{const time=Number(event.bar_start??event.event_time??event.timestamp),index=Math.max(0,candles.findIndex(b=>b.start<=time&&time<b.end)),cx=x(index),cy=y(candles[index].high)-12;return <g key={i}><title>{format(event.event??event.kind??event.type??event.event_type??event.signal)} · {timestamp(time)}</title><path d={`M ${cx} ${cy+6} l -5 -9 h 10 Z`} fill="#eac470"/><text x={cx} y={cy-7} fontSize="8" textAnchor="middle">{String(event.event??event.kind??event.type??event.event_type??event.signal??'EVENT')}</text></g>;})}{view.ruler}</g>
 <text x={P} y={H-5}>{timestamp(first)}</text><text x={W-R} y={H-5} textAnchor="end">{timestamp(last)}</text>
 </svg><p className="subtle chart-note">Колесо над графиком — масштаб X; над правой осью — масштаб Y. Перетаскивание — сдвиг; Shift + перетаскивание — линейка %. Двойной клик / Home — сброс, Esc — убрать линейку.</p></>;
}
export function MetricChart({items,metric,label}:{items:Snapshot[];metric:string;label:string}){
 const valid=items.filter(x=>typeof valueOf(x,metric)==='number'&&Number.isFinite(valueOf(x,metric))&&x.event_time).sort((a,b)=>(a.event_time??0)-(b.event_time??0));
 const values=valid.map(x=>valueOf(x,metric) as number),view=useChartView(values.map(v=>({low:v,high:v})),Math.max(100,valid.length),metric==='price'?8:2);
 if(!valid.length)return <div className="metric-chart"><h3>{label}</h3><div className="subtle">История недоступна</div></div>;
 const shown=valid.slice(view.start,view.end),begin=shown[0].event_time!,end=shown.at(-1)!.event_time!;
 const points=shown.map((item,i)=>`${view.x(view.start+i)},${view.y(valueOf(item,metric) as number)}`).join(' ');
 return <div className="metric-chart"><h3>{label}<span>{formatField(metric,values.at(-1))}</span></h3><svg ref={view.ref} {...view.props} className="interactive-chart" viewBox={`0 0 ${W} ${H}`} role="img" aria-label={`${label}, ${timestamp(begin)} — ${timestamp(end)}`}><defs><clipPath id={view.id}><rect x={P} y={P} width={W-P-R} height={H-2*P}/></clipPath></defs>{[0,.5,1].map(t=>{const value=view.range.low+t*(view.range.high-view.range.low);return <g key={t}><line x1={P} x2={W-R} y1={view.y(value)} y2={view.y(value)} stroke="#233044"/><text x={W-R+8} y={view.y(value)+4}>{formatField(metric,value)}</text></g>;})}<g clipPath={`url(#${view.id})`}><polyline points={points} fill="none" stroke="#64b5dc" strokeWidth="2"/>{shown.length===1&&<circle cx={view.x(view.start)} cy={view.y(values[view.start])} r="4" fill="#64b5dc"/>}{view.ruler}</g></svg><small>{timestamp(begin)} → {timestamp(end)}</small></div>;
}
