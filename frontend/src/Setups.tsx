import { useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { api } from './api';
import { columns, format, sortItems, timestamp, valueOf, type Sort } from './model';
import type { Envelope, Snapshot } from './types';
import { Badge, Empty, ErrorMessage, gateTone } from './common';
export function Setups(){
 const {data,error,isPending}=useQuery({queryKey:['setups'],queryFn:()=>api<Envelope<Snapshot>>('/api/setups?active_only=false&limit=10000'),refetchInterval:15000});
 const [search,setSearch]=useState(''),[filters,setFilters]=useState<Record<string,string>>({}),[sort,setSort]=useState<Sort[]>([{key:'avg_setup',direction:'desc'}]),[ranges,setRanges]=useState<Record<string,{min:string;max:string}>>({}),[showFilters,setShowFilters]=useState(false),[active,setActive]=useState(true);
 const items=useMemo(()=>data?.items??[],[data]);
 const visible=useMemo(()=>sortItems(items.filter(item=>{
  if(active&&(!item.action||item.action==='WAIT SETUP'))return false;
  if(search&&!`${item.exchange} ${item.symbol}`.toLowerCase().includes(search.toLowerCase()))return false;
  for(const [key,val]of Object.entries(filters)){if(val&&!format(valueOf(item,key)).toLowerCase().includes(val.toLowerCase()))return false;}
  for(const [key,range]of Object.entries(ranges)){if(!range.min&&!range.max)continue;const value=valueOf(item,key);if(typeof value!=='number')return false;if(range.min&&value<Number(range.min)||range.max&&value>Number(range.max))return false;}
  return true;
 }),sort),[items,search,filters,ranges,sort,active]);
 const setFilter=(key:string,value:string)=>setFilters(old=>({...old,[key]:value}));
 function changeSort(key:string,multi:boolean){setSort(old=>{const found=old.find(x=>x.key===key);const next:Sort={key,direction:found?.direction==='desc'?'asc':'desc'};return multi?[...old.filter(x=>x.key!==key),next]:[next];});}
 return <><div className="page-heading"><div><p className="eyebrow">BYBIT · USDT LINEAR PERPETUAL</p><h1>Active setups <span className="count">{visible.length}</span></h1><p>Scalping SMA 1.15.2 · текущие условия и состояние сигнала</p></div><button onClick={()=>{setFilters({});setRanges({});setSearch('');}}>Сбросить фильтры</button></div>
 <div className="toolbar"><label className="search"><span>⌕</span><input aria-label="Поиск инструмента" placeholder="Поиск инструмента…" value={search} onChange={e=>setSearch(e.target.value)}/></label>
 {['direction','timeframe','action','fsm'].map(key=><select aria-label={key} key={key} value={filters[key]??''} onChange={e=>setFilter(key,e.target.value)}><option value="">{key==='direction'?'Все направления':key==='timeframe'?'Все TF':key==='action'?'Все ACTION':'Все состояния'}</option>{[...new Set(items.map(x=>format(valueOf(x,key))))].filter(x=>x!=='н/д').sort().map(x=><option key={x}>{x}</option>)}</select>)}
 <button className={showFilters?'selected':''} onClick={()=>setShowFilters(!showFilters)}>Фильтры и диапазоны</button><label className="inline"><input type="checkbox" checked={active} onChange={e=>setActive(e.target.checked)}/> ACTION ≠ WAIT SETUP</label></div>
 {showFilters&&<div className="filter-panel">{['candidate_path','trigger_path','last_signal','blockers','active_plan_health','btc_regime'].map(key=><label key={key}>{key}<input value={filters[key]??''} onChange={e=>setFilter(key,e.target.value)} placeholder="Любое значение"/></label>)}{['avg_setup','continuation','formation','execution','geometry','exhaustion','mae','level','approach','context','btc_shock','rr'].map(key=><label key={key}>{columns.find(x=>x[0]===key)?.[1]}<span className="range"><input type="number" placeholder="min" aria-label={`${key} min`} value={ranges[key]?.min??''} onChange={e=>setRanges(old=>({...old,[key]:{max:old[key]?.max??'',min:e.target.value}}))}/><span>—</span><input type="number" placeholder="max" aria-label={`${key} max`} value={ranges[key]?.max??''} onChange={e=>setRanges(old=>({...old,[key]:{min:old[key]?.min??'',max:e.target.value}}))}/></span></label>)}</div>}
 <div className="table-meta"><span>{visible.length} показано / {items.length} получено</span><span>Shift + клик: сортировка по нескольким колонкам · н/д = значение недоступно</span></div><ErrorMessage error={error}/>
 <div className="table-scroll"><table className="screener"><thead><tr>{columns.map(([key,label,note])=><th key={key} title={note} aria-sort={sort[0]?.key===key?(sort[0].direction==='asc'?'ascending':'descending'):'none'}><button onClick={e=>changeSort(key,e.shiftKey)}>{label}{sort.some(x=>x.key===key)&&<span className="sort"> {sort.find(x=>x.key===key)?.direction==='desc'?'↓':'↑'}{sort.length>1?sort.findIndex(x=>x.key===key)+1:''}</span>}</button></th>)}</tr></thead><tbody>{visible.map(item=><tr key={`${item.exchange}/${item.symbol}/${item.timeframe}`}>{columns.map(([key])=>{const value=valueOf(item,key);const colors=item.metric_colors as Record<string,unknown>|undefined;const color=typeof colors?.[key]==='string'?String(colors[key]).match(/^#[0-9a-fA-F]{6}/)?.[0]:undefined;return <td key={key} title={format(value)} className={key==='symbol'?'sticky-symbol':''}>{key==='symbol'?<Link className="symbol" to={`/setups/${encodeURIComponent(item.exchange)}/${encodeURIComponent(item.symbol)}/${encodeURIComponent(item.timeframe)}`}>{item.symbol}<small>{item.exchange}</small></Link>:<span key={`${key}:${format(value)}`} className={`cell-value ${gateTone(item.gates?.[key])}`} style={color?{backgroundColor:color+'35',color}:undefined}>{key==='event_time'?timestamp(value):key==='direction'?<Badge tone={value==='LONG'?'good':value==='SHORT'?'bad':'neutral'}>{format(value)}</Badge>:format(value)}</span>}</td>;})}</tr>)}</tbody></table></div>
 {visible.length===0&&<Empty>{isPending?'Загрузка рыночных состояний…':items.length?'Нет setups, соответствующих фильтрам.':'Ожидание рыночных данных и прогрева engine. Проверьте Data health.'}</Empty>}
 </>;
}
