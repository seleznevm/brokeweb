export type Data = Record<string, unknown>;
export interface PanelRow { row?:number; label:string; value:unknown; note?:string; state?:number|null; color?:string|null }
export interface Snapshot extends Data {
  exchange:string; symbol:string; timeframe:string; price?:number|null; action?:string|null;
  metrics?:Data; gates?:Data; signals?:string[]; blockers?:string[]; event_time?:number;
  decision_panel?:PanelRow[];
}
export interface Bar {start:number; end:number; open:number; high:number; low:number; close:number; volume?:number; confirmed?:boolean}
export interface Envelope<T> {items:T[]; total?:number}
export interface Condition {op:string; field?:string; value?:unknown; conditions?:Condition[]}
export interface AlertRule {id?:string; name:string; enabled:boolean; conditions:Condition; mode:string; frequency:string; cooldown_seconds:number; template:string}
export interface Parameter {name:string;type:string;default:unknown;title:string;minval?:number;maxval?:number;options?:unknown[];group?:string;tooltip?:string}
export interface Parameters {id:string;values:Data;schema:Parameter[]}
