import {describe,it,expect} from 'vitest';
import {clusterEvents,spreadLabels} from '../src/chartAnnotations';
import {displayTimestamp,parseDisplayDate,timezoneLabel} from '../src/displayTime';

describe('chart annotations',()=>{
 it('separates identical and nearby zones at both plot edges',()=>{
  for(const anchor of [42,160,333]){
   const placed=spreadLabels(Array.from({length:6},(_,i)=>({anchor:anchor+i*.1,id:i})),52,338);
   expect(placed).toHaveLength(6);
   expect(placed[0].position).toBeGreaterThanOrEqual(52);
   expect(placed.at(-1)!.position).toBeLessThanOrEqual(338);
   for(let i=1;i<placed.length;i++)expect(placed[i].position-placed[i-1].position).toBeGreaterThanOrEqual(20);
  }
 });
 it('omits offscreen labels instead of claiming a visible price level',()=>{
  expect(spreadLabels([{anchor:-100},{anchor:500},{anchor:NaN}],52,338)).toEqual([]);
 });
 it('clusters same-bar and nearby events without dropping names or timestamps',()=>{
  const candles=Array.from({length:100},(_,i)=>({start:i*100,end:(i+1)*100,open:1,high:2,low:0,close:1}));
  const events=Array.from({length:200},(_,i)=>({bar_start:Math.floor(i/2)*100,event_time:Math.floor(i/2)*100+i%2,event:'SIGNAL '+i}));
  const groups=clusterEvents(events,candles,i=>35+i*9,34,1000);
  expect(groups.flatMap(g=>g.items)).toHaveLength(200);
  for(let i=1;i<groups.length;i++)expect(groups[i].x-groups[i-1].x).toBeGreaterThanOrEqual(58);
  expect(groups.flatMap(g=>g.items).map(v=>v.event.event)).toEqual(events.map(e=>e.event));
  expect(groups.flatMap(g=>g.items).map(v=>v.time)).toEqual(events.map(e=>e.event_time));
 });
 it('skips events without a matching visible candle and merges clamped right-edge badges',()=>{
  const bars=[{start:100,end:200,open:1,high:2,low:0,close:1},{start:200,end:300,open:1,high:2,low:0,close:1}];
  const groups=clusterEvents([{bar_start:0},{bar_start:100},{bar_start:200},{bar_start:300}],bars,i=>980+i*19,34,1000);
  expect(groups).toHaveLength(1);expect(groups[0].x).toBe(974);expect(groups[0].items).toHaveLength(2);
 });
});
describe('display timezone',()=>{
 const instant=Date.UTC(2026,8,22,20,15);
 it('defaults to UTC+7 with date rollover and never browser local time',()=>{
  expect(displayTimestamp(instant)).toBe('23.09.2026, 03:15:00 UTC+07:00');
  expect(displayTimestamp(instant,0)).toBe('22.09.2026, 20:15:00 UTC');
  expect(displayTimestamp(instant,-330)).toBe('22.09.2026, 14:45:00 UTC-05:30');
  expect(displayTimestamp(0)).toContain('07:00:00');
  expect(displayTimestamp(null)).toBe('н/д');
 });
 it('converts a custom wall-clock range to the same UTC instant',()=>{
  expect(parseDisplayDate('2026-09-23T03:15',420)).toBe(instant);
  expect(parseDisplayDate('2026-09-23T02:00',345)).toBe(instant);
  expect(parseDisplayDate('2026-09-22T14:45',-330)).toBe(instant);
  expect(timezoneLabel(345)).toBe('UTC+05:45');
  expect(parseDisplayDate('2026-02-30T12:00',420)).toBeNaN();
  expect(parseDisplayDate('',420)).toBeNaN();
 });
});
