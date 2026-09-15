import type { ReactNode } from 'react';
import { format } from './model';
export function Empty({children='Данные ещё не поступили.'}:{children?:ReactNode}){return <div className="empty"><span className="empty-icon">◇</span><div>{children}</div></div>;}
export function ErrorMessage({error}:{error:unknown}){return error?<div role="alert" className="error">{error instanceof Error?error.message:format(error)}</div>:null;}
export function JsonView({data}:{data:unknown}){return <pre className="json">{JSON.stringify(data,null,2)}</pre>;}
export function Badge({children,tone='neutral'}:{children:ReactNode;tone?:string}){return <span className={`badge ${tone}`}>{children}</span>;}
export function Section({title,children,tools}:{title:string;children:ReactNode;tools?:ReactNode}){return <section className="panel"><div className="panel-heading"><h2>{title}</h2>{tools}</div>{children}</section>;}
export function gateTone(state:unknown){return state===1||state===true?'good':state===-1||state===false?'bad':'neutral';}
