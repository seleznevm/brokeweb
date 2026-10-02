import {useState} from 'react';
import {useDisplayTime} from './Timezone';
import {clusterEvents,eventColor,eventName,eventTime,indexAtTime,spreadLabels,timeLabelLanes} from './chartAnnotations';
import {useChartView,P} from './useChartView';
import {ChartCrosshair,TimeAxis} from './ChartAxes';
import {displayChartTime} from './displayTime';
import {format,formatField,valueOf} from './model';
import type {Bar,Data,Snapshot} from './types';
import {Empty} from './common';

export interface ChartPriceLevel {label:string;value:number;color:string}
export function CandleChart({bars,snapshot,events,priceLevels,fitHistory=false,compact=false}:{bars:Bar[];snapshot?:Snapshot;events:Data[];priceLevels?:ChartPriceLevel[];fitHistory?:boolean;compact?:boolean}){
 const W=compact?720:1100,H=compact?300:380;
 const {timestamp,label:zoneLabel,offset}=useDisplayTime();
 const [eventsOpen,setEventsOpen]=useState(false);
 const valid=bars.filter(b=>[b.start,b.end,b.open,b.high,b.low,b.close].every(Number.isFinite)&&b.end>b.start).sort((a,b)=>a.start-b.start);
 const m=snapshot?.metrics??{};
 const zones=[['R1','resistanceBottom1','resistanceTop1','#e97178'],['R2','resistanceBottom2','resistanceTop2','#ba656d'],['S1','supportBottom1','supportTop1','#39bfab'],['S2','supportBottom2','supportTop2','#3c8f88'],['LOCKED','lockedZoneBottom','lockedZoneTop','#d6ab62'],['CONSUMED','recentLifecycleZoneBottom','recentLifecycleZoneTop','#9e7fdc']];
 const levels:ChartPriceLevel[]=priceLevels??[
  {label:'SL',value:snapshot?.sl as number,color:'#f06f78'},
  {label:'T1',value:snapshot?.t1 as number,color:'#39c8ac'},
 ];
 const labelWidth=Math.max(154,...levels.filter(l=>Number.isFinite(l.value)).map(l=>(`${format(l.value,8)} ${l.label}`).length*6.7+18));
 const R=Math.min(W*.38,labelWidth+14);
 const view=useChartView(valid,fitHistory?Math.max(valid.length,1):100,8,levels.map(l=>l.value),{width:W,height:H,right:R});
 const candles=valid.slice(view.start,view.end);
 if(!candles.length)return <Empty>Свечи ещё не доступны. OHLCV не заменяются синтетическими данными.</Empty>;
 const {y,width}=view,x=(i:number)=>view.x(view.start+i),edge=W-R;
 // Half-index correction puts candle starts on their left edge, and its end on the right.
 const times=[...valid.map(b=>b.start),valid.at(-1)!.end];
 const zoneLabels=spreadLabels(zones.flatMap(([label,bottom,top,color])=>typeof m[bottom]==='number'&&typeof m[top]==='number'?[{label,color,anchor:y(m[top] as number)}]:[]),P+12,H-P-10);
 const priceLabels=spreadLabels(levels.filter(l=>Number.isFinite(l.value)).map(l=>({...l,anchor:y(l.value)})),P+12,H-P-10);
 const clusters=clusterEvents(events,candles,x,P,edge);
 const markers=events.flatMap(event=>{
  const time=eventTime(event),cx=view.x(indexAtTime(times,time)-.5);
  if(!Number.isFinite(cx)||time<candles[0].start||time>candles.at(-1)!.end||cx<P||cx>edge)return [];
  return [{event,time,x:cx,color:eventColor(event)}];
 }).sort((a,b)=>a.time-b.time);
 const eventCount=markers.length;
 const distinctTimes=markers.filter((m,i)=>i===0||m.time!==markers[i-1].time);
 const timeLabels=timeLabelLanes(distinctTimes,P,edge);
 const rows=timeLabels.length?Math.max(...timeLabels.map(l=>l.lane))+1:0;
 const svgHeight=H+rows*23+(rows?8:0);
 return <>
  <div className="chart-controls"><span>OHLCV · {zoneLabel} · {candles.length} свечей</span><button aria-label="Zoom in" onClick={()=>view.zoom(.7)}>+</button><button aria-label="Zoom out" onClick={()=>view.zoom(1.4)}>−</button><button onClick={view.fitAll}>Вся история</button><button onClick={view.reset}>Сбросить вид</button></div>
  <div className="candle-chart-scroll"><svg ref={view.ref} {...view.props} className="candle-chart interactive-chart" viewBox={`0 0 ${W} ${svgHeight}`} role="img" aria-label="Свечной график, структурные зоны, уровни и события" data-visible-bars={candles.length} data-price-low={view.range.low} data-price-high={view.range.high}>
   <defs><clipPath id={view.id}><rect x={P} y={P} width={edge-P} height={H-2*P}/></clipPath></defs>
   {[0,.25,.5,.75,1].map(t=>{const value=view.range.low+t*(view.range.high-view.range.low);return <g key={t}><line x1={P} x2={edge} y1={y(value)} y2={y(value)} stroke="#233044" strokeDasharray="3 5"/><text x={edge+8} y={y(value)+4}>{format(value,8)}</text></g>;})}
   <g clipPath={`url(#${view.id})`}>
    {zones.map(([label,bottom,top,color])=>typeof m[bottom]==='number'&&typeof m[top]==='number'?<g key={label}><rect x={P} y={y(m[top] as number)} width={edge-P} height={Math.max(1,y(m[bottom] as number)-y(m[top] as number))} fill={color} opacity="0.13"/><line x1={P} x2={edge} y1={y(m[top] as number)} y2={y(m[top] as number)} stroke={color} opacity=".5"/></g>:null)}
    {candles.map((bar,i)=>{const color=bar.close>=bar.open?'#39c8ac':'#ee7386';return <g key={bar.start}><title>{`${timestamp(bar.start)}\nO ${format(bar.open,8)} H ${format(bar.high,8)} L ${format(bar.low,8)} C ${format(bar.close,8)}\nVolume ${format(bar.volume)} · ${bar.confirmed===false?'realtime':'confirmed'}`}</title><line x1={x(i)} x2={x(i)} y1={y(bar.high)} y2={y(bar.low)} stroke={color}/><rect x={x(i)-Math.max(1,width*.65)/2} width={Math.max(1,width*.65)} y={y(Math.max(bar.open,bar.close))} height={Math.max(1,Math.abs(y(bar.open)-y(bar.close)))} fill={color} opacity={bar.confirmed===false?.6:1}/></g>;})}
    {levels.filter(l=>Number.isFinite(l.value)).map(l=><line key={l.label} x1={P} x2={edge} y1={y(l.value)} y2={y(l.value)} stroke={l.color} strokeDasharray="7 4"/>)}
    {markers.map((m,i)=><line key={i} className="chart-event-line" x1={m.x} x2={m.x} y1={P} y2={H-P} stroke={m.color} strokeDasharray="3 4"><title>{`${eventName(m.event)} · ${timestamp(m.time)}`}</title></line>)}
    {view.ruler}
   </g>
   <TimeAxis width={W} height={H} right={R} times={times} start={view.start} end={view.end} x={view.x} offset={offset} timeShift={.5}/>
   {zoneLabels.map(({label,color,anchor,position})=><g key={label} className="price-annotation"><line x1={P+4} x2={P+18} y1={anchor} y2={position} stroke={color}/><rect x={P+18} y={position-9} width={78} height={18} rx={3}/><text x={P+24} y={position+3} style={{fill:color}}>{label}</text></g>)}
   {priceLabels.map(({label,value,color,anchor,position})=><g key={label} className="price-annotation price-scale-label"><line x1={edge} x2={edge+6} y1={anchor} y2={position} stroke={color}/><rect x={edge+6} y={position-10} width={R-12} height={20} rx={3}/><text x={edge+12} y={position+4} style={{fill:color}}>{format(value,8)} {label}</text></g>)}
   {timeLabels.map((m,i)=>{
    const group=markers.filter(other=>other.time===m.time),cy=H+12+m.lane*23;
    return <g key={i} className="chart-event-time" pointerEvents="none"><title>{`${group.map(other=>eventName(other.event)).join(' · ')} · ${timestamp(m.time)}`}</title>
     <path d={`M ${m.x} ${H-P} V ${cy-15} H ${m.labelX}`} stroke={m.color} fill="none" opacity=".5"/>
     <rect x={m.labelX-64} y={cy-13} width={128} height={21} rx={3}/>
     {group.map((other,j)=><rect key={j} x={m.labelX-64+j*128/group.length} y={cy-13} width={128/group.length} height={2} fill={other.color}/>)}
     <text x={m.labelX} y={cy+2} textAnchor="middle" style={{fill:m.color}}>{displayChartTime(m.time,offset)}</text>
    </g>;
   })}
   {clusters.map((group,i)=><g key={i} className="chart-event-badge" role="button" tabIndex={0} aria-label={`Группа ${i+1}: ${group.items.length} событий`} onPointerDown={e=>e.stopPropagation()} onClick={()=>setEventsOpen(true)} onKeyDown={e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();e.stopPropagation();setEventsOpen(true);}}}>
    <title>{group.items.map(({event,time})=>`${eventName(event)} · ${timestamp(time)}`).join('\n')}</title>
    <rect x={group.x-26} y={9} width={52} height={22} rx={4}/><text x={group.x} y={24} textAnchor="middle">{i+1} · {group.items.length}</text>
   </g>)}
   <ChartCrosshair cursor={view.cursor} width={W} height={H} right={R} times={times} offset={offset} formatValue={v=>format(v,8)} timeShift={.5}/>
  </svg></div>
  {eventCount>0&&<details className="chart-events" open={eventsOpen} onToggle={e=>setEventsOpen(e.currentTarget.open)}><summary>События на графике ({eventCount}) · номера групп над свечами</summary><div className="chart-event-list">{markers.map((m,i)=><div key={i}><time>{timestamp(m.time)}</time><span style={{color:m.color}}>{eventName(m.event)}</span></div>)}</div></details>}
  <p className="subtle chart-note">Наведение — перекрестие с ценой и временем. Колесо над графиком — масштаб X; над правой осью — масштаб Y. Перетаскивание — сдвиг; Shift + перетаскивание — линейка %. Двойной клик / Home — сброс, Esc — убрать линейку.</p>
 </>;
}

export function MetricChart({items,metric,label}:{items:Snapshot[];metric:string;label:string}){
 const W=720,H=300,R=130;
 const {timestamp,offset}=useDisplayTime();
 const valid=items.filter(item=>typeof valueOf(item,metric)==='number'&&Number.isFinite(valueOf(item,metric))&&Number.isFinite(item.event_time)).sort((a,b)=>(a.event_time??0)-(b.event_time??0));
 const values=valid.map(item=>valueOf(item,metric) as number),view=useChartView(values.map(v=>({low:v,high:v})),Math.max(100,valid.length),metric==='price'?8:2,[],{width:W,height:H,right:R});
 if(!valid.length)return <div className="metric-chart"><h3>{label}</h3><div className="subtle">История недоступна</div></div>;
 const shown=valid.slice(view.start,view.end),times=valid.map(item=>item.event_time!),begin=shown[0].event_time!,end=shown.at(-1)!.event_time!;
 const points=shown.map((item,i)=>`${view.x(view.start+i)},${view.y(valueOf(item,metric) as number)}`).join(' ');
 return <div className="metric-chart"><h3>{label}<span>{formatField(metric,values.at(-1))}</span></h3>
  <svg ref={view.ref} {...view.props} className="interactive-chart" viewBox={`0 0 ${W} ${H}`} role="img" aria-label={`${label}, ${timestamp(begin)} — ${timestamp(end)}`}>
   <defs><clipPath id={view.id}><rect x={P} y={P} width={W-P-R} height={H-2*P}/></clipPath></defs>
   {[0,.5,1].map(t=>{const value=view.range.low+t*(view.range.high-view.range.low);return <g key={t}><line x1={P} x2={W-R} y1={view.y(value)} y2={view.y(value)} stroke="#233044"/><text x={W-R+8} y={view.y(value)+4}>{formatField(metric,value)}</text></g>;})}
   <g clipPath={`url(#${view.id})`}><polyline points={points} fill="none" stroke="#64b5dc" strokeWidth="2"/>{shown.length===1&&<circle cx={view.x(view.start)} cy={view.y(values[view.start])} r="4" fill="#64b5dc"/>}{view.ruler}</g>
   <TimeAxis width={W} height={H} right={R} times={times} start={view.start} end={view.end} x={view.x} offset={offset}/>
   <ChartCrosshair cursor={view.cursor} width={W} height={H} right={R} times={times} offset={offset} formatValue={v=>formatField(metric,v)}/>
  </svg>
 </div>;
}
