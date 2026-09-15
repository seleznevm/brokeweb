import type { Data, Snapshot } from './types';
export const columns=[
 ['symbol','Symbol','Exchange instrument'],['timeframe','TF','Pine chart timeframe'],['price','Price','Latest source close'],['action','ACTION','Current engine action'],['fsm','Family / State','Persistent setup state'],['direction','Direction','Source direction'],
 ['avg_setup','AVG SETUP','Pine AVG SETUP including intrabar history'],['continuation','CONTINUATION','Informational meta-score, not a hard gate'],['formation','Formation','WATCH / ARMED quality'],['execution','Execution','Entry readiness'],['geometry','Geometry','R:R, SL and target quality'],['exhaustion','Exhaustion','Reversal risk'],['mae','MAE','Path-aware adverse excursion risk'],['level','Level Quality','Structural level quality'],['approach','Approach','Pine approach score / 9'],['context','Context','Activity, liquidity, BTC and whipsaw'],['btc_regime','BTC regime','External BTC context'],['btc_shock','BTC Shock','Engine BTC shock score'],['candidate_path','Candidate Path','Forming route'],['trigger_path','Trigger Path','Actual current route'],['hard_gates','Hard Gates','InPlay, Level, Structure, Approach, Distance'],['fresh_trigger','Fresh Trigger','Current trigger freshness'],['trigger_zone','Trigger Zone','Locked structural zone'],['distance','Distance','ATR distance to nearest zone edge'],['sl','SL','Displayed trade plan stop'],['t1','T1','Displayed first target'],['rr','R:R','Displayed risk reward'],['active_plan_health','Active Plan Health','Current active plan health'],['addon_gate','Add-on Gate','Fresh accepted trigger and quality gates'],['target_freshness','Target Freshness','Consumed / fresh target state'],['last_signal','Last signal','Latest emitted engine signal'],['setup_age','Setup age','Age from engine'],['event_time','Updated at','Engine event time UTC'],['data_health','Data health','Source completeness and freshness']
] as const;
const aliases:Record<string,string>={formation:'formationQuality',execution:'executionQuality',geometry:'geometryQuality',exhaustion:'exhaustionRisk',mae:'maeRisk',level:'activeLevelQuality',approach:'activeApproachScore',context:'contextQuality',avg_setup:'avgSetup',continuation:'continuationScore',btc_shock:'btcShockScore',candidate_path:'candidateEntryPath',trigger_path:'entryPath',distance:'activeDistanceAtr',sl:'displayedTradePlanSL',t1:'displayedTradePlanT1',rr:'displayedTradePlanRR',fresh_trigger:'gateFreshReadyTrigger',addon_gate:'addOnAllowed',setup_age:'lockedSetupAge',fsm:'setupState'};
export function valueOf(item:Snapshot,key:string):unknown {
 if(key==='fsm')return item.setup_state??item.metrics?.setupState??item.fsm;
 if(key==='last_signal')return item.last_signal??item.signals?.at(-1);
 return item[key]??item.metrics?.[aliases[key]??key];
}
export function format(value:unknown):string {
 if(value===null||value===undefined||typeof value==='number'&&!Number.isFinite(value))return 'н/д';
 if(typeof value==='boolean')return value?'Да':'Нет';
 if(typeof value==='number')return new Intl.NumberFormat('en-US',{maximumFractionDigits:8}).format(value);
 if(Array.isArray(value))return value.length?value.map(format).join(' · '):'—';
 if(typeof value==='object')return Object.entries(value as Data).map(([key,v])=>`${key}: ${format(v)}`).join(' · ');
 return String(value);
}
export function timestamp(value:unknown):string {if(!value)return 'н/д';const date=new Date(typeof value==='number'?value:String(value));return Number.isNaN(date.valueOf())?'н/д':date.toLocaleString('ru-RU',{timeZone:'UTC',hour12:false})+' UTC';}
export type Sort={key:string;direction:'asc'|'desc'};
export function sortItems(items:Snapshot[],sort:Sort[]):Snapshot[]{return [...items].sort((a,b)=>{for(const s of sort){const x=valueOf(a,s.key),y=valueOf(b,s.key);if(x==null&&y==null)continue;if(x==null)return 1;if(y==null)return -1;const d=typeof x==='number'&&typeof y==='number'?x-y:format(x).localeCompare(format(y),undefined,{numeric:true});if(d)return s.direction==='asc'?d:-d;}return 0;});}
export function scalar(raw:string):unknown {const trimmed=raw.trim();if(trimmed==='true')return true;if(trimmed==='false')return false;if(trimmed==='null')return null;if(trimmed!==''&&Number.isFinite(Number(trimmed)))return Number(trimmed);return raw;}
export function conditionValue(op:string,raw:string):unknown {if(['IN','NOT IN','BETWEEN'].includes(op)){try{const parsed=JSON.parse(raw);if(Array.isArray(parsed))return parsed;}catch{/* Comma separated input is also supported. */}return raw.split(',').map(x=>scalar(x.trim()));}return scalar(raw);}
