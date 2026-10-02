import { useEffect, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import type { Envelope, Snapshot } from './types';

export async function api<T>(path:string, options?:RequestInit):Promise<T> {
  const response=await fetch(path,{...options,headers:{'Content-Type':'application/json',...options?.headers}});
  if(!response.ok){let detail:string;try{const data=await response.json();detail=typeof data.detail==='string'?data.detail:JSON.stringify(data.detail??data);}catch{detail=response.statusText;}throw new Error(`${response.status}: ${detail}`);}
  return response.status===204?undefined as T:response.json();
}
export const send=<T,>(path:string,method:string,body?:unknown)=>api<T>(path,{method,body:body===undefined?undefined:JSON.stringify(body)});
export type SetupSubscription={exchange:string;symbol:string;timeframe:string};
export function applySetupUpdate(cache:ReturnType<typeof useQueryClient>,snapshot:Snapshot,detail:boolean){
  if(detail){if(snapshot.snapshot_view!=='summary')cache.setQueryData(['setup',snapshot.exchange,snapshot.symbol,snapshot.timeframe],snapshot);return;}
  cache.setQueryData<Envelope<Snapshot>>(['setups'],old=>{
    // A frame can arrive before the HTTP response. Do not create a partial universe cache.
    if(!old)return old;
    const items=[...old.items];const i=items.findIndex(x=>x.exchange===snapshot.exchange&&x.symbol===snapshot.symbol&&x.timeframe===snapshot.timeframe);
    if(i<0)items.push(snapshot);else items[i]=snapshot;return {items,total:items.length};
  });
}
export function useLiveSetups(subscription?:SetupSubscription){
  const exchange=subscription?.exchange,symbol=subscription?.symbol,timeframe=subscription?.timeframe;
  const detail=exchange!==undefined&&symbol!==undefined&&timeframe!==undefined;
  const cache=useQueryClient();const [status,setStatus]=useState('CONNECTING');
  useEffect(()=>{let socket:WebSocket;let retry:ReturnType<typeof setTimeout>;let stopped=false;let attempts=0;
    function connect(){if(stopped)return;const query=new URLSearchParams(detail?{exchange:exchange!,symbol:symbol!,timeframe:timeframe!}:{compact:'true'});socket=new WebSocket(`${location.protocol==='https:'?'wss':'ws'}://${location.host}/ws/setups?${query}`);
      socket.onopen=()=>{attempts=0;setStatus('LIVE');void cache.invalidateQueries({queryKey:detail?['setup',exchange,symbol,timeframe]:['setups']});};
      socket.onmessage=event=>{try{const msg=JSON.parse(event.data);if(msg.type==='snapshot'&&msg.data){const snapshot=msg.data as Snapshot;
        applySetupUpdate(cache,snapshot,detail);
      }}catch{/* An invalid WS frame must not destroy the current screen. */}};
      socket.onerror=()=>socket.close();socket.onclose=()=>{if(!stopped){setStatus('RECONNECTING');retry=setTimeout(connect,Math.min(30000,1000*2**attempts++));}};
    }connect();return()=>{stopped=true;clearTimeout(retry);socket?.close();};
  },[cache,detail,exchange,symbol,timeframe]);return status;
}
