import {createContext,useContext,type ReactNode} from 'react';
import {useQuery} from '@tanstack/react-query';
import {api} from './api';
import {DEFAULT_TIMEZONE_OFFSET,displayTimestamp,timezoneLabel} from './displayTime';
export interface RuntimeSettings {
  universe_min_turnover24h_usdt: number;
  snapshot_interval_sec: number;
  timezone_offset_minutes: number;
  telegram_bot_token?: string;
  telegram_chat_id?: string;
  telegram_topic_id?: string;
  broke_pb_position_usdt?: number;
  campaign_enabled?: boolean;
  campaign_signal_mode?: 'REALTIME' | 'BAR_CLOSE';
  campaign_exit_policy?: 'CONTEXT_30M' | 'STRUCTURAL';
  campaign_execution_mode?: 'PAPER' | 'DEMO' | 'LIVE';
  campaign_risk_usdt?: number | null;
  campaign_portfolio_risk_usdt?: number | null;
  campaign_fee_rate?: number;
  campaign_slippage_bps?: number;
  campaign_spread_bps?: number;
  campaign_be_buffer_bps?: number;
  campaign_bias_max_age_sec?: number;
  campaign_price_max_age_sec?: number;
  campaign_tp1_fraction?: number;
  campaign_max_tranches?: 1 | 3;
  broke_pb_telegram_bot_token?: string;
  broke_pb_telegram_chat_id?: string;
  broke_pb_telegram_topic_id?: string;
  broke_pb_pm_telegram_enabled?: boolean;
  broke_pb_pm_telegram_bot_token?: string;
  broke_pb_pm_telegram_chat_id?: string;
  broke_pb_pm_telegram_topic_id?: string;
  broke_execution_gate_enabled?:boolean;
  broke_execution_notional_usdt?:number;
  broke_execution_max_spread_bps?:number;
  broke_execution_max_slippage_bps?:number;
  broke_execution_max_age_sec?:number;
  broke_execution_max_symbols?:number;
  broke_fee_rate?:number;
  broke_slippage_bps?:number;
  broke_spread_bps?:number;
  broke_late_watch_enabled?:boolean;
  broke_late_watch_window_bars?:number;
}
const TimezoneContext=createContext(DEFAULT_TIMEZONE_OFFSET);
export function TimezoneProvider({children}:{children:ReactNode}){
 const settings=useQuery({queryKey:['settings'],queryFn:()=>api<RuntimeSettings>('/api/settings'),refetchInterval:30000});
 return <TimezoneContext.Provider value={settings.data?.timezone_offset_minutes??DEFAULT_TIMEZONE_OFFSET}>{children}</TimezoneContext.Provider>;
}
export function useDisplayTime(){
 const offset=useContext(TimezoneContext);
 return {offset,label:timezoneLabel(offset),timestamp:(value:unknown)=>displayTimestamp(value,offset)};
}
