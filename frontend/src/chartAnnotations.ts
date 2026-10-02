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
export function eventTime(event:Data):number {
 const raw=event.event_time??event.timestamp??event.bar_start;
 return raw==null?NaN:Number(raw);
}
// Event lines use the execution clock, not the start of its containing candle.
export function eventColor(event:Data):string {
 if(typeof event.color==='string')return event.color;
 const name=String(event.action??eventName(event)).toUpperCase().replace(/_/g,' ');
 if(/TP1|T1|TAKE PROFIT|TP HIT|\bBE\b/.test(name))return '#39c8ac';
 if(/RUNNER/.test(name))return '#b094ef';
 if(/\bSL\b|STOP|INVALID|EXIT|CLOSE|FLIP/.test(name))return '#f06f78';
 if(/REDUCE|EXHAUST|MAE/.test(name))return '#eac470';
 if(/OPEN|ADD|ENTRY|READY/.test(name))return '#64b5dc';
 return '#eac470';
}
export function timeAtIndex(times:number[],index:number):number {
 if(!times.length)return NaN;
 if(times.length===1)return times[0];
 const i=clamp(Math.floor(index),0,times.length-2);
 return times[i]+(times[i+1]-times[i])*(index-i);
}
export function indexAtTime(times:number[],time:number):number {
 if(!times.length||!Number.isFinite(time))return NaN;
 if(times.length===1)return 0;
 let low=0,high=times.length-1;
 while(low+1<high){const mid=Math.floor((low+high)/2);if(times[mid]<=time)low=mid;else high=mid;}
 return low+(time-times[low])/(times[high]-times[low]||1);
}
// Each distinct execution time gets a label; nearby labels occupy separate rows.
export function timeLabelLanes<T extends {x:number;time:number}>(items:T[],left:number,right:number,labelWidth=128):(T&{labelX:number;lane:number})[]{
 const ends:number[]=[];
 return [...items].sort((a,b)=>a.x-b.x).map(item=>{
  const labelX=clamp(item.x,left+labelWidth/2,right-labelWidth/2),start=labelX-labelWidth/2;
  let lane=ends.findIndex(end=>end+6<=start);
  if(lane<0)lane=ends.length;
  ends[lane]=labelX+labelWidth/2;
  return {...item,labelX,lane};
 });
}
export interface ChartEvent {event:Data;time:number;barIndex:number}
export interface EventCluster {x:number;items:ChartEvent[]}
// Merge events on the same candle and neighbouring markers at the current zoom.
// A numbered badge links to every full event, rather than clipping long names.
export function clusterEvents(events:Data[],candles:Bar[],x:(index:number)=>number,left:number,right:number,gap=58):EventCluster[]{
 const placed=events.flatMap(event=>{
  const anchor=eventTime(event);
  const barIndex=candles.findIndex((b,i)=>b.start<=anchor&&(anchor<b.end||(event.event_time!=null&&i===candles.length-1&&anchor===b.end)));
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
