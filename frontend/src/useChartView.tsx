import {useEffect,useId,useRef,useState} from 'react';
import type {PointerEvent as ReactPointerEvent} from 'react';
import {boundedWindow,clamp,percentage,zoomWindow} from './chartMath';
import {format} from './model';
export const W=1100,H=380,P=34,R=100;
type Point={x:number;y:number};
type Range={low:number;high:number};
export function useChartView(values:{low:number;high:number}[],initial=100,digits=8,fitPrices:number[]=[],layout:{width?:number;height?:number;right?:number}={}){
 const W=layout.width??1100,H=layout.height??380,R=layout.right??100,PW=W-P-R,PH=H-2*P;
 const ref=useRef<SVGSVGElement>(null),id=useId().replace(/:/g,'');
 const [view,setView]=useState({offset:0,span:initial}),[vertical,setVertical]=useState<Range|null>(null),[measure,setMeasure]=useState<{a:Point;b:Point}|null>(null);
 const [cursor,setCursor]=useState<Point|null>(null);
 const drag=useRef<{point:Point;view:typeof view;range:Range;mode:string}|null>(null);
 const total=values.length,v=boundedWindow(total,view.offset,view.span),left=total-v.offset-v.span;
 const start=Math.max(0,Math.floor(left)),end=Math.min(total,Math.ceil(total-v.offset));
 const shown=values.slice(start,end),extras=fitPrices.filter(Number.isFinite),lo=shown.length?Math.min(...shown.map(v=>v.low),...extras):0,hi=shown.length?Math.max(...shown.map(v=>v.high),...extras):1,pad=(hi-lo)*.08||Math.abs(hi)*.01||1;
 const range=vertical??{low:lo-pad,high:hi+pad},height=range.high-range.low;
 const x=(index:number)=>P+(index-left+.5)/v.span*PW;
 const y=(value:number)=>H-P-(value-range.low)/height*PH;
 const point=(clientX:number,clientY:number):Point=>{
  const svg=ref.current!,matrix=svg.getScreenCTM();
  if(!matrix)return {x:P,y:P};
  const p=new DOMPoint(clientX,clientY).matrixTransform(matrix.inverse());return {x:p.x,y:p.y};
 };
 const dataPoint=(p:Point):Point=>({x:left+clamp((p.x-P)/PW,0,1)*v.span-.5,y:range.high-clamp((p.y-P)/PH,0,1)*height});
 useEffect(()=>{
  const svg=ref.current;if(!svg)return;
  function wheel(e:WheelEvent){
   e.preventDefault();const p=point(e.clientX,e.clientY),factor=Math.exp(clamp(e.deltaY*(e.deltaMode===1?16:e.deltaMode===2?H:1),-300,300)*.002);
   setMeasure(null);
   if(p.x>=W-R){
    const anchor=range.high-clamp((p.y-P)/PH,0,1)*height;
    const scale=clamp(height*factor,Math.max(Math.abs(anchor)*1e-10,1e-12),Math.max(Math.abs(anchor),1)*1e6)/height;
    setVertical({low:anchor-(anchor-range.low)*scale,high:anchor+(range.high-anchor)*scale});
   }else setView(zoomWindow(total,v,(p.x-P)/PW,factor));
  }
  svg.addEventListener('wheel',wheel,{passive:false});return ()=>svg.removeEventListener('wheel',wheel);
 });
 const reset=()=>{setView({offset:0,span:initial});setVertical(null);setMeasure(null);setCursor(null);};
 const zoom=(factor:number)=>{setView(zoomWindow(total,v,.5,factor));setMeasure(null);};
 const fitAll=()=>{setView({offset:0,span:total});setVertical(null);setMeasure(null);};
 const onPointerDown=(e:ReactPointerEvent<SVGSVGElement>)=>{
  if(e.button!==0)return;e.preventDefault();e.currentTarget.focus();e.currentTarget.setPointerCapture(e.pointerId);
  const p=point(e.clientX,e.clientY),mode=e.shiftKey?'measure':p.x>=W-R?'y':p.y>=H-P?'x':'pan';
  drag.current={point:p,view:v,range,mode};setMeasure(mode==='measure'?{a:dataPoint(p),b:dataPoint(p)}:null);
 };
 const onPointerMove=(e:ReactPointerEvent<SVGSVGElement>)=>{
  const p=point(e.clientX,e.clientY);
  setCursor(p.x>=P&&p.x<=W-R&&p.y>=P&&p.y<=H-P?p:null);
  const d=drag.current;if(!d)return;const dx=p.x-d.point.x,dy=p.y-d.point.y;
  if(d.mode==='measure'){setMeasure(old=>old?{...old,b:dataPoint(p)}:null);return;}
  if(d.mode==='x')setView(zoomWindow(total,d.view,.5,Math.exp(clamp(dx,-500,500)*.003)));
  else if(d.mode==='y'){
   const mid=(d.range.low+d.range.high)/2,half=(d.range.high-d.range.low)/2*Math.exp(clamp(dy,-500,500)*.003);
   setVertical({low:mid-half,high:mid+half});
  }else{
   setView(boundedWindow(total,d.view.offset+dx/PW*d.view.span,d.view.span));
   const shift=dy/PH*(d.range.high-d.range.low);setVertical({low:d.range.low+shift,high:d.range.high+shift});
  }
 };
 const stop=(e:ReactPointerEvent<SVGSVGElement>)=>{drag.current=null;if(e.currentTarget.hasPointerCapture(e.pointerId))e.currentTarget.releasePointerCapture(e.pointerId);};
 const ruler=measure&&<g className="chart-ruler" pointerEvents="none"><rect x={Math.min(x(measure.a.x),x(measure.b.x))} y={Math.min(y(measure.a.y),y(measure.b.y))} width={Math.abs(x(measure.a.x)-x(measure.b.x))} height={Math.abs(y(measure.a.y)-y(measure.b.y))} fill="#64b5dc" opacity=".15"/><line x1={x(measure.a.x)} y1={y(measure.a.y)} x2={x(measure.b.x)} y2={y(measure.b.y)} stroke="#a5ddff" strokeDasharray="5 3"/><text x={clamp(x(measure.b.x),P+100,W-R-100)} y={clamp(y(measure.b.y)-12,P+15,H-P-5)} textAnchor="middle">{format(percentage(measure.a.y,measure.b.y),2)}% · Δ {format(measure.b.y-measure.a.y,digits)} · {Math.round(Math.abs(measure.b.x-measure.a.x))} интервалов</text></g>;
 return {ref,id,start,end,x,y,range,ruler,reset,zoom,fitAll,width:PW/v.span,cursor:cursor?{...cursor,index:dataPoint(cursor).x,value:dataPoint(cursor).y}:null,props:{onPointerDown,onPointerMove,onPointerLeave:()=>setCursor(null),onPointerUp:stop,onPointerCancel:(e:ReactPointerEvent<SVGSVGElement>)=>{stop(e);setCursor(null);},onLostPointerCapture:()=>{drag.current=null;},onDoubleClick:reset,onKeyDown:(e:React.KeyboardEvent)=>{if(e.key==='Escape'){setMeasure(null);setCursor(null);}if(e.key==='Home')reset();},tabIndex:0}};
}
