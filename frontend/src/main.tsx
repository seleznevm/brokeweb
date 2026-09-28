import { Component, StrictMode, type ReactNode } from 'react';
import { createRoot } from 'react-dom/client';
import { BrowserRouter, Navigate, NavLink, Route, Routes } from 'react-router-dom';
import { QueryClient, QueryClientProvider, useQuery } from '@tanstack/react-query';
import { api, useLiveSetups } from './api';
import { Badge } from './common';
import {Statistics} from './Statistics';
import { Setups } from './Setups';
import { WTSetups } from './WTSetups';
import { Detail } from './Detail';
import { Alerts } from './Alerts';
import { Settings } from './Settings';
import { Status } from './Status';
import { AIAgent } from './AIAgent';
import type { Data } from './types';
import './style.css';
import {TimezoneProvider,useDisplayTime} from './Timezone';
const client=new QueryClient({defaultOptions:{queries:{retry:1,staleTime:5000,refetchOnWindowFocus:true}}});
class ErrorBoundary extends Component<{children:ReactNode},{error:Error|null}>{state:{error:Error|null}={error:null};static getDerivedStateFromError(error:Error){return {error};}render(){return this.state.error?<div className="fatal"><h1>Не удалось отобразить страницу</h1><p>{this.state.error.message}</p><button onClick={()=>location.reload()}>Перезагрузить</button></div>:this.props.children;}}
function App(){
 const {label}=useDisplayTime();
 const live=useLiveSetups();const health=useQuery({queryKey:['health'],queryFn:()=>api<Data>('/api/health'),refetchInterval:10000});const parity=useQuery({queryKey:['parity'],queryFn:()=>api<Data>('/api/parity'),refetchInterval:30000});
 return <div className="app"><header><NavLink to="/setups" className="brand"><span className="brand-icon">▥</span>BROKE<span>WEB</span><small>Scalping SMA / 1.15.2</small></NavLink><nav aria-label="Основная навигация">{[['/setups','BROKE_SETUPS'],['/wt-setups','WT_SETUPS'],['/statistics','Statistics'],['/alerts','Alerts'],['/ai-agent','AI Agent'],['/settings','Settings'],['/health','Data health'],['/parity','Parity']].map(([path,label])=><NavLink key={path} to={path}>{label}</NavLink>)}</nav><div className="connection"><Badge tone={live==='LIVE'?'good':'bad'}><span className="dot"/>{live}</Badge><NavLink to="/health" title="Подробное состояние данных">DATA {String(health.data?.status??'UNKNOWN').toUpperCase()}</NavLink></div></header>
 {parity.data?.status!=='VERIFIED'&&parity.data?.status!=='PASS'&&<div className="global-warning"><span>◇</span><strong>PARITY {String(parity.data?.status??'UNVERIFIED')}</strong><span>Совпадение с TradingView ещё не подтверждено.</span><NavLink to="/parity">Подробности →</NavLink></div>}
 <main><Routes><Route path="/" element={<Navigate replace to="/setups"/>}/><Route path="/setups" element={<Setups/>}/><Route path="/wt-setups" element={<WTSetups/>}/><Route path="/setups/:exchange/:symbol/:timeframe" element={<Detail/>}/><Route path="/statistics" element={<Statistics/>}/><Route path="/alerts" element={<Alerts/>}/><Route path="/ai-agent" element={<AIAgent/>}/><Route path="/settings" element={<Settings/>}/><Route path="/health" element={<Status kind="health"/>}/><Route path="/parity" element={<Status kind="parity"/>}/><Route path="*" element={<div className="empty">Страница не найдена. <NavLink to="/setups">Перейти к setups</NavLink></div>}/></Routes></main><footer><span>SCALPING SMA 1.15.2 · BYBIT V5</span><span>{label} · Backend source of truth · н/д = unavailable</span></footer></div>;
}
createRoot(document.getElementById('root')!).render(<StrictMode><ErrorBoundary><QueryClientProvider client={client}><TimezoneProvider><BrowserRouter><App/></BrowserRouter></TimezoneProvider></QueryClientProvider></ErrorBoundary></StrictMode>);
