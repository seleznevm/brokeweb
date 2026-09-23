import {clamp} from './chartMath';
import type {Bar,Data} from './types';

// Keep labels inside the plot and separate even identical/nearby price levels.
// There are at most six zone labels and two trade-plan labels per side.
export function spreadLabels<T extends {anchor:number}>(labels:T[],top:number,bottom:number,gap=20):(T&{position:number})[]{
 const ordered=labels.filter(v=>Number.isFinite(v.anchor)&&v.anchor>=top-gap&&v.anchor<=bottom+gap).sort((a,b)=>a.anchor-b.anchor);
 const space=ordered.length>1?Math.min(gap,(bottom-top)/(ordered.length-1)):gap;
 const result=ordered.map((v,i)=>({...v,position:Math.max(clamp(v.anchor,top,bottom),top+i*space)}));
 for(let i=1;i<result.length;i++)result[i].position=Math.max(result[i].position,result[i-1].position+space);
 if(result.length&&result.at(-1)!.position>bottom){
  result[result.length-1].position=bottom;
  for(let i=result.length-2;i>=0;i--)result[i].position=Math.min(result[i].position,result[i+1].position-space);
 }
 return result;
}

export const eventName=(event:Data)=>String(event.event??event.kind??event.type??event.event_type??event.signal??'EVENT');
export interface ChartEvent {event:Data;time:number;barIndex:number}
export interface EventCluster {x:number;items:ChartEvent[]}
// Merge events on the same candle and neighbouring markers at the current zoom.
// A numbered badge links to every full event, rather than clipping long names.
export function clusterEvents(events:Data[],candles:Bar[],x:(index:number)=>number,left:number,right:number,gap=58):EventCluster[]{
 const placed=events.flatMap(event=>{
  const anchor=Number(event.bar_start??event.event_time??event.timestamp);
  const barIndex=candles.findIndex(b=>b.start<=anchor&&anchor<b.end);
  if(barIndex<0)return [];
  const cx=x(barIndex);
  if(cx<left||cx>right)return [];
  const time=Number(event.event_time??event.timestamp??anchor);
  return [{x:clamp(cx,left+26,right-26),item:{event,time,barIndex}}];
 }).sort((a,b)=>a.x-b.x||a.item.time-b.item.time);
 const groups:EventCluster[]=[];
 for(const point of placed){
  const prev=groups.at(-1);
  if(prev&&point.x-prev.x<gap)prev.items.push(point.item);
  else groups.push({x:point.x,items:[point.item]});
 }
 return groups;
}
