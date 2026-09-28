"""WT 1.6.4 executes the supplied Pine on the BROKE worker's shared feeds."""
from __future__ import annotations
import hashlib
import json
import math
from functools import lru_cache
from itertools import combinations,chain
from pathlib import Path
from .syntax import Program
from .interpreter import Execution,qualified,tf_seconds
from .values import NA,is_na,truth,encode
from .context_bars import ContextBars
from .wt_lifecycle import apply_stop_guard

SOURCE=Path(__file__).resolve().parents[2]/'reference/WT_Setups_1.6.4.pine'
SOURCE_HASH=hashlib.sha256(SOURCE.read_bytes()).hexdigest()
COMBINATIONS=['+'.join(c) for n in range(1,5) for c in combinations(('T1','T2','T3','T4'),n)]
VERSION='wt-1.6.4-interpreter.1'

@lru_cache(maxsize=1)
def program():return Program(SOURCE.read_text(encoding='utf-8'))

@lru_cache(maxsize=1)
def input_schema():
    ex=Execution(program());ex.begin(dict(start=0,end=1800000,open=1,high=1,low=1,close=1,volume=1))
    specs=[]
    for st in program().statements:
        if st.kind!='assign':continue
        if st.meta['name'].startswith('grp'):ex.statement(st)
        if not st.expr or st.expr.kind!='call' or not str(qualified(st.expr.args[0])).startswith('input.'):continue
        args=[x for x in st.expr.args[1:] if x.kind!='kw'];kw={x.value:ex.eval(x.args[0]) for x in st.expr.args[1:] if x.kind=='kw'}
        kind=qualified(st.expr.args[0]).split('.')[-1]
        specs.append({'name':st.meta['name'],'type':kind,'default':'wt_broke_bridge' if kind=='source' else encode(ex.eval(args[0])),
            'title':ex.eval(args[1]) if len(args)>1 else kw.get('title'),**{k:encode(v) for k,v in kw.items() if k in ('group','tooltip','minval','maxval','options','step')}})
        if kind=='source':specs[-1].update(options=['wt_broke_bridge'],tooltip='Общий расчёт BROKE: направление, AVG, Execution, Level, MAE, Exhaustion, BTC gate и WATCH/PINE события.')
    return specs

def parameters(values=None):
    specs={x['name']:x for x in input_schema()};values=values or {}
    if set(values)-set(specs):raise ValueError('Unknown WT parameter')
    result={k:s['default'] for k,s in specs.items()}
    for k,v in values.items():
        s=specs[k];kind=s['type']
        valid=type(v) is bool if kind=='bool' else type(v) is int if kind=='int' else type(v) in (int,float) and math.isfinite(v) if kind=='float' else isinstance(v,str)
        if not valid:raise ValueError(f'{k}: invalid {kind}')
        if kind in ('int','float') and ('minval' in s and v<s['minval'] or 'maxval' in s and v>s['maxval']):raise ValueError(f'{k}: out of bounds')
        if 'options' in s and v not in s['options']:raise ValueError(f'{k}: invalid option')
        if kind=='source' and v!='wt_broke_bridge':raise ValueError('WT bridge uses the existing BROKE calculation')
        if kind=='timeframe':tf_seconds(v)
        if k=='btcSymbol' and v!='BINANCE:BTCUSDT':raise ValueError('Shared BTC feed is BINANCE:BTCUSDT')
        result[k]=v
    return result

def context_requirements(values,timeframes):
    p=parameters(values);own=set();btc=set()
    for tf in timeframes:
        profile=p['profileMode'] if p['profileMode']!='Auto' else {'5':'5m','15':'15m','30':'30m','60':'1H'}.get(tf,'Manual')
        h,m,b={'5m':('240','30','15'),'15m':('240','60','60'),'30m':('240','120','60'),'1H':('D','240','240')}.get(profile,(p['manualHtfTf'],p['manualMidTf'],p['manualBtcTf']))
        if any(tf_seconds(x)<tf_seconds(tf) for x in (h,m,b)):raise ValueError('WT context timeframes must be at least the chart timeframe')
        own.update((h,m));btc.add(b)
    return own,btc

class WTExecution(Execution):
    def begin(self,bar,realtime=False,outer=None):
        super().begin(bar,realtime,outer)
        self.special.update({'timeframe.isminutes':self.timeframe.isdigit(),'timeframe.multiplier':int(self.timeframe) if self.timeframe.isdigit() else 1})
        self.scopes[0].update(last_bar_time=(outer or {}).get('last_bar_time',bar['start']),last_bar_index=(outer or {}).get('last_bar_index',self.count))
    def builtin(self,name,a,kw,e):
        if name=='fill':return NA
        return super().builtin(name,a,kw,e)
    def ta(self,name,a,e):
        key=f'{self.callpath}/ta/{e.uid}'
        if name=='ta.stdev':
            self.remember(key+'/sample',a[0]);n=int(a[1])
            values=[v for v in [*self.histories.get(key+'/sample',()),a[0]] if not is_na(v)][-n:]
            if len(values)<n:return NA
            avg=sum(values)/n;return math.sqrt(sum((v-avg)**2 for v in values)/(n if len(a)<3 or a[2] else n-1))
        if name=='ta.dmi':
            n,smooth=map(int,a);h,l,c=(self.lookup(k) for k in ('high','low','close'))
            ph,pl,pc=(self.previous(key+'/'+k) for k in ('h','l','c'))
            for k,v in (('h',h),('l',l),('c',c)):self.remember(key+'/'+k,v)
            up=h-ph if not is_na(ph) else NA;down=pl-l if not is_na(pl) else NA
            plus=NA if is_na(up) else up if up>down and up>0 else 0
            minus=NA if is_na(down) else down if down>up and down>0 else 0
            tr=NA if is_na(pc) else max(h-l,abs(h-pc),abs(l-pc))
            def rma(tag,value,length):
                self.remember(key+'/'+tag+'in',value);old=self.previous(key+'/'+tag)
                vals=[v for v in [*self.histories.get(key+'/'+tag+'in',()),value] if not is_na(v)][-length:]
                out=old if is_na(value) else (sum(vals)/length if len(vals)==length else NA) if is_na(old) else (old*(length-1)+value)/length
                return self.remember(key+'/'+tag,out)
            trm=rma('tr',tr,n);pm=rma('plus',plus,n);mm=rma('minus',minus,n)
            p=100*pm/trm if not is_na(trm) and trm else NA;m=100*mm/trm if not is_na(trm) and trm else NA
            # Pine fixnan on DI retains the most recent finite DI in a flat run.
            p=self.previous(key+'/p') if is_na(p) else p;m=self.previous(key+'/m') if is_na(m) else m
            self.remember(key+'/p',p);self.remember(key+'/m',m)
            dx=NA if is_na(p) or is_na(m) else 100*abs(p-m)/(p+m if p+m else 1)
            return [p,m,rma('adx',dx,smooth)]
        return super().ta(name,a,e)

class ConfirmedTrendContexts:
    """Previous closed HTF expression with lookahead_on, without future OHLC."""
    def __init__(self,engine):self.engine=engine;self.streams={}
    def __call__(self,parent,call,name,nodes):
        symbol=parent.eval(nodes[0]);tf=str(parent.eval(nodes[1]));expr=nodes[2]
        if name!='request.security' or qualified(expr.args[0])!='f_trendStateConfirmed':raise ValueError('Unsupported WT context expression')
        symbol=symbol if symbol.endswith('.P') else symbol+'.P'
        key=f'{call.uid}|{symbol}|{tf}'
        if key not in self.streams:self.streams[key]=WTExecution(parent.program,parent.parameters,symbol,tf,parent.tick_size,500)
        ex=self.streams[key]
        bars=self.engine.contexts.get(f'{symbol}|{tf}',())
        # Even on a closed chart candle, the previous HTF candle is selected at chart OPEN.
        at=parent.bar['start'];candidate=bars.after_until(ex.last_start,at) if isinstance(bars,ContextBars) else bars
        for bar in candidate:
            if ex.last_start is not None and bar['start']<=ex.last_start:continue
            if not bar.get('confirmed',True) or bar['end']>at:continue
            ex.begin(bar);ex.eval(expr);ex.commit()
        if not ex.count:
            parent.missing.add(f'{symbol}|{tf}');return NA
        span=tf_seconds(tf)*1000
        if ex.last_start+2*span<at or ex.count<22:parent.missing.add(f'{symbol}|{tf}')
        # The expression reads [1]/[2]; current OHLC are deliberately irrelevant.
        ex.begin(dict(start=at//span*span,end=(at//span+1)*span,open=0,high=0,low=0,close=0,volume=0))
        return ex.eval(expr)
    def export_state(self):return {k:v.export_state() for k,v in self.streams.items()}
    def restore_state(self,state):
        for k,v in state.items():
            _,symbol,tf=k.split('|');ex=WTExecution(program(),self.engine.parameters,symbol,tf,self.engine.tick_size,500);ex.restore_state(v);self.streams[k]=ex

def bridge(snapshot):
    """Pack the existing BROKE fields exactly as WT's single source expects."""
    fields=('avg_setup','execution','level','mae','exhaustion')
    if not snapshot or any(type(snapshot.get(k)) not in (int,float) for k in fields):return NA
    side={'LONG':1,'SHORT':-1}.get(snapshot.get('direction'),0)
    signals=snapshot.get('signals',[])
    event=1 if any(s in signals for s in ('LONG WATCH ENTRY','PINE READY LONG')) else -1 if any(s in signals for s in ('SHORT WATCH ENTRY','PINE READY SHORT')) else 0
    packed=side+1
    for key in fields:packed=packed*101+min(100,max(0,math.floor(snapshot[key]+.5)))
    btc_ok=snapshot.get('metrics',{}).get('gateBtc',snapshot.get('metrics',{}).get('gateBtcShock',False))
    packed=(packed*2+int(truth(btc_ok)))*3+event+1
    return 1_000_000_000_000+packed

class WTEngine:
    def __init__(self,symbol,timeframe,tick_size,values=None):
        self.symbol=symbol;self.timeframe=timeframe;self.tick_size=tick_size;self.parameters=parameters(values)
        self.parameter_hash=hashlib.sha256(json.dumps(self.parameters,sort_keys=True).encode()).hexdigest()
        self.runtime=WTExecution(program(),self.parameters,f'BYBIT:{symbol}.P',timeframe,tick_size,600)
        self.contexts={};self.provider=ConfirmedTrendContexts(self);self.runtime.request_provider=self.provider;self.snapshot=None;self.first_bar=None;self.stopped_plans={}
    def update(self,bar,contexts,real=False,broke=None,plan_history=()):
        if self.runtime.last_start is not None and bar['start']<=self.runtime.last_start:raise ValueError('WT candle already committed')
        if self.first_bar is None:self.first_bar=bar['start']
        self.contexts=contexts
        chart=contexts.get(f'BYBIT:{self.symbol}.P|{self.timeframe}',())
        last_time=max(bar['start'],chart[-1]['start'] if chart else bar['start'])
        self.runtime.begin(bar,real,{'wt_broke_bridge':bridge(broke),'last_bar_time':last_time,'last_bar_index':self.runtime.count+(last_time-bar['start'])//(tf_seconds(self.timeframe)*1000)})
        self.runtime.execute(program().statements);m=self.runtime.scopes[0]
        side='LONG' if truth(m.get('fireLongAlert')) else 'SHORT' if truth(m.get('fireShortAlert')) else None
        setups=[f'T{i}' for i in range(1,5) if side and truth(m.get(f'alertT{i}{side.title()}'))]
        active=[f'T{i}' for i in range(1,5) if truth(m.get(f'lastSetupT{i}'))]
        if truth(m.get('readyToEnterEvent')) and not setups:setups=list(active)
        signals=['+'.join(c) for n in range(1,len(setups)+1) for c in combinations(setups,n)]
        if truth(m.get('readyToEnterEvent')):signals.append('READY TO ENTER')
        fields={'action':'entryAction','entry_quality':'entryQuality','score_long':'longScore','score_short':'shortScore','score':'lastSignalScore',
            'entry':'lastEntry','sl':'lastSL','managed_sl':'managedSL','tp1':'lastTP1','tp2':'lastTP2','tp3':'lastTP3','tp4':'lastTP4','liquidity_target':'lastLiqTP',
            'position_usdt':'lastPositionUsdt','risk_usdt':'riskUsdt','rr_liquidity':'_rrToLiq','move_r':'_moveR','signal_age':'_signalAge',
            'volume_ratio':'volRatio','adx':'adx','compression':'squeezeNow','htf_state':'htfState','mid_state':'midState','btc_state':'btcState','be_active':'beActive','profile':'activeProfile'}
        result={k:m.get(v) for k,v in fields.items()}
        result.update(strategy='WT_SETUPS',signal_source='engine',exchange='BYBIT',symbol=self.symbol,timeframe=self.timeframe,
            direction=side or ('LONG' if m.get('lastDir')==1 else 'SHORT' if m.get('lastDir')==-1 else 'NONE'),
            setups=active,setup_combination='+'.join(active),signals=signals,signal_setups=setups,price=bar['close'],bar_start=bar['start'],
            event_time=min(bar.get('received_at',bar['end']),bar['end']),confirmed=bar.get('confirmed',True),
            data_health='RECOVERING' if self.runtime.count<100 else 'DEGRADED' if self.runtime.missing else 'HEALTHY',
            replay=not real,missing_contexts=sorted(self.runtime.missing),parameter_hash=self.parameter_hash,pine_source_hash=SOURCE_HASH,engine_version=VERSION,
            setup_generation_id=f"wt:{self.first_bar}:{m.get('lastSignalBar')}:{m.get('lastDir')}",
            broke_enabled=self.parameters['useBrokeCorrelation'],broke_valid=m.get('brokeDataValid'),broke_agree=m.get('_brokeDirAgree'),
            broke_quality_pass=m.get('_brokeSetupQualityPass'),parity_status='UNVERIFIED',metrics={k:v for k,v in m.items() if k not in self.parameters and k not in {'wt_broke_bridge','open','high','low','close','volume','hl2','hlc3','ohlc4'}})
        if self.parameters['useBrokeCorrelation'] and (is_na(bridge(broke)) or (broke or {}).get('data_health') not in ('HEALTHY','FULL_REALTIME','KLINE_REALTIME')):result['data_health']='DEGRADED'
        age=result.get('signal_age')
        result['plan_bar_start']=bar['start']-int(age)*tf_seconds(self.timeframe)*1000 if not is_na(age) else None
        generation=result['setup_generation_id']
        history=chart.after_until(result['plan_bar_start'],bar['start']-1) if isinstance(chart,ContextBars) else (b for b in chart if b['start']<bar['start'])
        # BROKE's checkpoint retains chart candles older than incremental REST
        # backfill. Reuse them to detect earlier stop touches after an upgrade.
        result=apply_stop_guard(encode(result),chain(history,(b for b in plan_history if b['start']<bar['start']),[bar]),self.stopped_plans.get(generation))
        if result.get('stop_hit'):
            self.stopped_plans[generation]={k:result.get(k) for k in ('setup_generation_id','parameter_hash','stop_hit','stop_hit_bar_start')}
            while len(self.stopped_plans)>64:self.stopped_plans.pop(next(iter(self.stopped_plans)))
        self.snapshot=result
        if bar.get('confirmed',True):self.runtime.commit()
        return self.snapshot
    def export_state(self):return {'version':VERSION,'source_hash':SOURCE_HASH,'parameters':self.parameter_hash,'first_bar':self.first_bar,'runtime':self.runtime.export_state(),'contexts':self.provider.export_state(),'stopped_plans':dict(self.stopped_plans)}
    def restore_state(self,state):
        if (state.get('version'),state.get('source_hash'),state.get('parameters'))!=(VERSION,SOURCE_HASH,self.parameter_hash):raise ValueError('WT checkpoint version mismatch')
        self.first_bar=state['first_bar'];self.runtime.restore_state(state['runtime']);self.provider.restore_state(state['contexts'])
        self.stopped_plans=dict(state.get('stopped_plans',{}))
