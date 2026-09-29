import {useState} from 'react';
import {useDisplayTime} from './Timezone';
import {clusterEvents,eventName,spreadLabels,tradeSegments} from './chartAnnotations';
import {useChartView,W,H,P,R} from './useChartView';
import { format, formatField, valueOf } from './model';
import type { Bar, Data, Snapshot } from './types';
import { Empty } from './common';
export function CandleChart({bars,snapshot,events,plans=[]}:{bars:Bar[];snapshot?:Snapshot;events:Data[];plans?:Data[]}){
 const {timestamp,label:zoneLabel}=useDisplayTime();
 const [eventsOpen,setEventsOpen]=useState(false);
 const valid=bars.filter(x=>[x.open,x.high,x.low,x.close].every(Number.isFinite));
 const view=useChartView(valid),candles=valid.slice(view.start,view.end);
 if(!candles.length)return <Empty>Свечи ещё не доступны. OHLCV не заменяются синтетическими данными.</Empty>;
 const m=snapshot?.metrics??{};
 const zones=[['R1','resistanceBottom1','resistanceTop1','#e97178'],['R2','resistanceBottom2','resistanceTop2','#ba656d'],['S1','supportBottom1','supportTop1','#39bfab'],['S2','supportBottom2','supportTop2','#3c8f88'],['LOCKED','lockedZoneBottom','lockedZoneTop','#d6ab62'],['CONSUMED','recentLifecycleZoneBottom','recentLifecycleZoneTop','#9e7fdc']];
 const levels=snapshot?.strategy==='WT_SETUPS'?[]:[['SL',snapshot?.sl,'#f06f78'],['T1',snapshot?.t1,'#63a6ff']];
 const {y,width}=view,x=(i:number)=>view.x(view.start+i);
 const first=candles[0].start,last=candles.at(-1)!.end;
 const currentPlan=snapshot?.trade_plan as Data|undefined;
 const allPlans=new Map(plans.map(plan=>[String(plan.id),plan]));
 if(currentPlan)allPlans.set(String(currentPlan.id),currentPlan);
 const segments=tradeSegments([...allPlans.values()],first,candles.at(-1)!.start);
 const timeX=(time:number)=>{let i=candles.findIndex(b=>b.start<=time&&time<b.end);if(i<0)i=time<=first?0:candles.length-1;return x(i);};
 const zoneLabels=spreadLabels(zones.flatMap(([label,bottom,top,color])=>typeof m[bottom]==='number'&&typeof m[top]==='number'?[{label,color,anchor:y(m[top] as number)}]:[]),P+18,H-P-8);
 const priceLabels=spreadLabels(levels.flatMap(([label,value,color])=>typeof value==='number'?[{label:String(label),value,color:String(color),anchor:y(value)}]:[]),P+18,H-P-8);
 const clusters=clusterEvents(events,candles,x,P,W-R);
 const eventCount=clusters.reduce((n,g)=>n+g.items.length,0);
 return <><div className="chart-controls"><span>OHLCV · {zoneLabel} · {candles.length} свечей</span><button onClick={view.reset}>Сбросить вид</button></div>
 <div className="candle-chart-scroll"><svg ref={view.ref} {...view.props} className="candle-chart interactive-chart" viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Свечной график, структурные зоны, SL, T1 и события" data-visible-bars={candles.length} data-price-low={view.range.low} data-price-high={view.range.high}>

 <defs><clipPath id={view.id}><rect x={P} y={P/2} width={W-P-R} height={H-P}/></clipPath></defs>
 {[0,.25,.5,.75,1].map(t=>{const price=view.range.low+t*(view.range.high-view.range.low);return <g key={t}><line x1={P} x2={W-R} y1={y(price)} y2={y(price)} stroke="#233044" strokeDasharray="3 5"/><text x={W-R+8} y={y(price)+4}>{format(price,8)}</text></g>;})}
 <g clipPath={`url(#${view.id})`}>{zones.map(([label,bottom,top,color])=>typeof m[bottom]==='number'&&typeof m[top]==='number'?<g key={label}><rect x={P} y={y(m[top] as number)} width={W-P-R} height={Math.max(1,y(m[bottom] as number)-y(m[top] as number))} fill={color} opacity="0.13"/><line x1={P} x2={W-R} y1={y(m[top] as number)} y2={y(m[top] as number)} stroke={color} opacity=".5"/></g>:null)}
 {candles.map((bar,i)=>{const color=bar.close>=bar.open?'#39c8ac':'#ee7386';return <g key={bar.start}><title>{timestamp(bar.start)}{'\n'}O {format(bar.open,8)} H {format(bar.high,8)} L {format(bar.low,8)} C {format(bar.close,8)}{'\n'}Volume {format(bar.volume)} · {bar.confirmed===false?'realtime':'confirmed'}</title><line x1={x(i)} x2={x(i)} y1={y(bar.high)} y2={y(bar.low)} stroke={color}/><rect x={x(i)-Math.max(1,width*.65)/2} width={Math.max(1,width*.65)} y={y(Math.max(bar.open,bar.close))} height={Math.max(1,Math.abs(y(bar.open)-y(bar.close)))} fill={color} opacity={bar.confirmed===false?.6:1}/></g>;})}
 {levels.map(([label,value,color])=>typeof value==='number'?<g key={String(label)}><line x1={P} x2={W-R} y1={y(value)} y2={y(value)} stroke={String(color)} strokeDasharray="7 4"/></g>:null)}
 {segments.map(segment=><g key={segment.id} data-wt-level={segment.name} data-start={segment.start} data-end={segment.end}><title>{segment.name.toUpperCase()} {format(segment.price,8)} · {timestamp(segment.start)} → {timestamp(segment.end)}{segment.hit?' · HIT':''}</title><line x1={timeX(segment.start)} x2={timeX(segment.end)} y1={y(segment.price)} y2={y(segment.price)} stroke={segment.color} strokeDasharray="6 3"/><circle cx={timeX(segment.end)} cy={y(segment.price)} r={segment.hit?3:1.5} fill={segment.color}/><text x={timeX(segment.end)-4} y={y(segment.price)-4} textAnchor="end" style={{fill:segment.color,fontSize:10}}>{segment.name.toUpperCase()}</text></g>)}
 {view.ruler}</g>
 {zoneLabels.map(({label,color,anchor,position})=><g key={label} className="price-annotation"><line x1={P+4} x2={P+18} y1={anchor} y2={position} stroke={color}/><rect x={P+18} y={position-9} width={78} height={18} rx={3}/><text x={P+24} y={position+3} style={{fill:color}}>{label}</text></g>)}
 {priceLabels.map(({label,value,color,anchor,position})=><g key={label} className="price-annotation"><line x1={W-R-4} x2={W-R-20} y1={anchor} y2={position} stroke={color}/><rect x={W-R-180} y={position-9} width={160} height={18} rx={3}/><text x={W-R-26} y={position+3} textAnchor="end" style={{fill:color}}>{label} {format(value,8)}</text></g>)}
 {clusters.map((group,i)=><g key={i} className="chart-event-badge" role="button" tabIndex={0} aria-label={`Группа ${i+1}: ${group.items.length} событий`} onPointerDown={e=>e.stopPropagation()} onClick={()=>setEventsOpen(true)} onKeyDown={e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();e.stopPropagation();setEventsOpen(true);}}}>
  <title>{group.items.map(({event,time})=>`${eventName(event)} · ${timestamp(time)}`).join('\n')}</title>
  <line x1={group.x} x2={x(group.items[0].barIndex)} y1={32} y2={Math.max(P+12,Math.min(H-P,y(candles[group.items[0].barIndex].high)-4))} stroke="#eac470" opacity=".4" strokeDasharray="2 4"/>
  <rect x={group.x-26} y={10} width={52} height={22} rx={4}/><text x={group.x} y={25} textAnchor="middle">{i+1} · {group.items.length}</text>
 </g>)}
 <text x={P} y={H-5}>{timestamp(first)}</text><text x={W-R} y={H-5} textAnchor="end">{timestamp(last)}</text>
 </svg></div>{eventCount>0&&<details className="chart-events" open={eventsOpen} onToggle={e=>setEventsOpen(e.currentTarget.open)}><summary>События на графике ({eventCount}) · номера групп над свечами</summary><div className="chart-event-list">{clusters.map((group,i)=><section key={i}><h3>Группа {i+1} · {group.items.length}</h3>{group.items.map(({event,time},j)=><div key={j}><time>{timestamp(time)}</time><span>{eventName(event)}</span></div>)}</section>)}</div></details>}<p className="subtle chart-note">Колесо над графиком — масштаб X; над правой осью — масштаб Y. Перетаскивание — сдвиг; Shift + перетаскивание — линейка %. Двойной клик / Home — сброс, Esc — убрать линейку.</p></>;
}
export function MetricChart({items,metric,label}:{items:Snapshot[];metric:string;label:string}){
 const {timestamp}=useDisplayTime();
 const valid=items.filter(x=>typeof valueOf(x,metric)==='number'&&Number.isFinite(valueOf(x,metric))&&x.event_time).sort((a,b)=>(a.event_time??0)-(b.event_time??0));
 const values=valid.map(x=>valueOf(x,metric) as number),view=useChartView(values.map(v=>({low:v,high:v})),Math.max(100,valid.length),metric==='price'?8:2);
 if(!valid.length)return <div className="metric-chart"><h3>{label}</h3><div className="subtle">История недоступна</div></div>;
 const shown=valid.slice(view.start,view.end),begin=shown[0].event_time!,end=shown.at(-1)!.event_time!;
 const points=shown.map((item,i)=>`${view.x(view.start+i)},${view.y(valueOf(item,metric) as number)}`).join(' ');
 return <div className="metric-chart"><h3>{label}<span>{formatField(metric,values.at(-1))}</span></h3><svg ref={view.ref} {...view.props} className="interactive-chart" viewBox={`0 0 ${W} ${H}`} role="img" aria-label={`${label}, ${timestamp(begin)} — ${timestamp(end)}`}><defs><clipPath id={view.id}><rect x={P} y={P} width={W-P-R} height={H-2*P}/></clipPath></defs>{[0,.5,1].map(t=>{const value=view.range.low+t*(view.range.high-view.range.low);return <g key={t}><line x1={P} x2={W-R} y1={view.y(value)} y2={view.y(value)} stroke="#233044"/><text x={W-R+8} y={view.y(value)+4}>{formatField(metric,value)}</text></g>;})}<g clipPath={`url(#${view.id})`}><polyline points={points} fill="none" stroke="#64b5dc" strokeWidth="2"/>{shown.length===1&&<circle cx={view.x(view.start)} cy={view.y(values[view.start])} r="4" fill="#64b5dc"/>}{view.ruler}</g></svg><small>{timestamp(begin)} → {timestamp(end)}</small></div>;
}
