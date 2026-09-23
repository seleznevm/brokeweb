import {createContext,useContext,type ReactNode} from 'react';
import {useQuery} from '@tanstack/react-query';
import {api} from './api';
import {DEFAULT_TIMEZONE_OFFSET,displayTimestamp,timezoneLabel} from './displayTime';
export interface RuntimeSettings {snapshot_interval_sec:number;timezone_offset_minutes:number}
const TimezoneContext=createContext(DEFAULT_TIMEZONE_OFFSET);
export function TimezoneProvider({children}:{children:ReactNode}){
 const settings=useQuery({queryKey:['settings'],queryFn:()=>api<RuntimeSettings>('/api/settings'),refetchInterval:30000});
 return <TimezoneContext.Provider value={settings.data?.timezone_offset_minutes??DEFAULT_TIMEZONE_OFFSET}>{children}</TimezoneContext.Provider>;
}
export function useDisplayTime(){
 const offset=useContext(TimezoneContext);
 return {offset,label:timezoneLabel(offset),timestamp:(value:unknown)=>displayTimestamp(value,offset)};
}
