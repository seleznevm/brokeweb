export type Window = {offset:number;span:number};
export const clamp=(v:number,lo:number,hi:number)=>Math.max(lo,Math.min(hi,v));
export function boundedWindow(total:number,offset:number,span:number):Window {
 const size=clamp(span,Math.min(5,total||1),Math.max(1,total));
 return {span:size,offset:clamp(offset,0,Math.max(0,total-size))};
}
export function zoomWindow(total:number,view:Window,anchor:number,factor:number):Window {
 const next=boundedWindow(total,view.offset,view.span*factor);
 return boundedWindow(total,view.offset+(view.span-next.span)*(1-clamp(anchor,0,1)),next.span);
}
export function percentage(from:number,to:number):number|null{return from===0?null:(to-from)/Math.abs(from)*100;}
