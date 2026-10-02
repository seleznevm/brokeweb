export const DEFAULT_TIMEZONE_OFFSET=420;
export function displayChartTime(value:number,offset=DEFAULT_TIMEZONE_OFFSET):string {
 const d=new Date(value+offset*60000);
 if(!Number.isFinite(d.valueOf()))return 'н/д';
 const pad=(n:number)=>String(n).padStart(2,'0');
 return `${pad(d.getUTCDate())}.${pad(d.getUTCMonth()+1)}.${d.getUTCFullYear()} ${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}`;
}
export const timezoneOffsets=Array.from({length:105},(_,i)=>-720+i*15);
export function timezoneLabel(offset:number){
 const n=Math.abs(offset);
 return offset===0?'UTC':`UTC${offset<0?'-':'+'}${String(Math.floor(n/60)).padStart(2,'0')}:${String(n%60).padStart(2,'0')}`;
}
export function displayTimestamp(value:unknown,offset=DEFAULT_TIMEZONE_OFFSET):string {
 if(value===null||value===undefined||value==='')return 'н/д';
 const date=new Date(typeof value==='number'?value:String(value));
 if(!Number.isFinite(date.valueOf()))return 'н/д';
 const shifted=new Date(date.valueOf()+offset*60000);
 return shifted.toLocaleString('ru-RU',{timeZone:'UTC',hour12:false})+' '+timezoneLabel(offset);
}
// datetime-local has no timezone. Interpret its wall clock in the saved offset,
// never in the browser's timezone. Market timestamps remain UTC milliseconds.
export function parseDisplayDate(value:string,offset:number):number {
 if(!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/.test(value))return NaN;
 const parsed=Date.parse(value+':00Z');
 if(!Number.isFinite(parsed)||new Date(parsed).toISOString().slice(0,16)!==value)return NaN;
 return parsed-offset*60000;
}
