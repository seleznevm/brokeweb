import { describe, it, expect } from 'vitest';
import { conditionValue, format, sortItems, valueOf } from '../src/model';
import type { Snapshot } from '../src/types';
const row=(symbol:string,quality:number|null,risk:number):Snapshot=>({exchange:'BYBIT',symbol,timeframe:'15',avg_setup:quality,mae:risk});
describe('screener source values',()=>{
 it('sorts multiple metrics and always places unavailable last',()=>{const items=[row('B',70,30),row('C',null,0),row('A',70,10),row('D',80,40)];expect(sortItems(items,[{key:'avg_setup',direction:'desc'},{key:'mae',direction:'asc'}]).map(x=>x.symbol)).toEqual(['D','A','B','C']);expect(sortItems(items,[{key:'avg_setup',direction:'asc'}]).at(-1)?.symbol).toBe('C');});
 it('preserves zero, false and null without synthetic calculation',()=>{expect(valueOf({...row('A',0,0),metrics:{avgSetup:90}},'avg_setup')).toBe(0);expect(format(null)).toBe('н/д');expect(format(false)).toBe('Нет');expect(format(NaN)).toBe('н/д');});
 it('reads source metric aliases',()=>expect(valueOf({...row('A',null,0),metrics:{avgSetup:63.5}},'avg_setup')).toBe(63.5));
});
describe('alert condition values',()=>{
 it('supports numeric ranges, string membership and booleans',()=>{expect(conditionValue('BETWEEN','60, 80')).toEqual([60,80]);expect(conditionValue('IN','LONG, SHORT')).toEqual(['LONG','SHORT']);expect(conditionValue('IN','["WATCH", "READY"]')).toEqual(['WATCH','READY']);expect(conditionValue('==','false')).toBe(false);expect(conditionValue('crosses_above','70')).toBe(70);});
});
