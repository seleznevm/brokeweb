import {describe,it,expect} from 'vitest';
import {boundedWindow,zoomWindow,percentage} from '../src/chartMath';
describe('chart viewport',()=>{
 it('zooms around the cursor without moving its candle',()=>{
  const before={offset:50,span:100},after=zoomWindow(1000,before,.25,.5);
  expect(1000-before.offset-before.span+before.span*.25).toBe(1000-after.offset-after.span+after.span*.25);
 });
 it('bounds panning and zoom at both history ends',()=>{expect(boundedWindow(100,-30,30)).toEqual({offset:0,span:30});expect(boundedWindow(100,1000,30)).toEqual({offset:70,span:30});expect(zoomWindow(100,{offset:0,span:100},.5,100)).toEqual({offset:0,span:100});expect(boundedWindow(1,100,0)).toEqual({offset:0,span:1});});
 it('measures signed change and leaves zero-origin percentages undefined',()=>{expect(percentage(100,105)).toBe(5);expect(percentage(100,95)).toBe(-5);expect(percentage(0,5)).toBeNull();});
});
