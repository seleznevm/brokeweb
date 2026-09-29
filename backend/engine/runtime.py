"""Canonical standalone Scalping_SMA engine; mathematical parity is UNVERIFIED.

The pinned Pine AST is executed directly, in its original statement order. This
avoids replacing any gate/FSM/zone/Research formula with a Python approximation.
TradingView reference comparisons are still mandatory for acceptance.
"""
from __future__ import annotations
import hashlib
import os
import time
from .syntax import SOURCE,load_program
from .interpreter import Execution,tf_seconds
from .contexts import ContextProvider
from .parameters import validate_parameters,parameter_hash,required_history
from .values import encode,truth,is_na
from .currency import CurrencyRates

ENGINE_VERSION='1.15.2-interpreter.3'
PINE_HASH=hashlib.sha256(SOURCE.read_bytes()).hexdigest()
FIELDS={
 'action':'actionText','fsm':'setupFsmState','setup_state':'setupState','candidate_path':'candidateEntryPath','trigger_path':'entryPath','risk_path':'riskPath','fsm_path':'setupFsmPath',
 'formation':'formationQuality','execution':'executionQuality','geometry':'geometryQuality','context':'contextQuality','level':'activeLevelQuality','approach':'activeApproachScore','exhaustion':'exhaustionRisk','mae':'maeRisk','avg_setup':'avgSetup','continuation':'continuationScore','btc_shock':'btcShockScore','sl':'displayedTradePlanSL','t1':'displayedTradePlanT1','rr':'displayedTradePlanRR',
 'in_play':'inPlay','active_plan_health':'activePlanHealthText','add_on_gate':'addOnAllowed','target_freshness':'targetConsumedUnresolved','fresh_trigger':'gateFreshReadyTrigger','trigger_zone':'activeLevel','distance':'activeDistanceAtr','family':'setupFamily',
}
SIGNALS={
 'newLongWatch':'L WATCH','newShortWatch':'S WATCH','watchEntryLongSignal':'LONG WATCH ENTRY','watchEntryShortSignal':'SHORT WATCH ENTRY','maturedEntryLongSignal':'MATURED PRE-BREAK ENTRY','newLongArmed':'LONG ARMED','newShortArmed':'SHORT ARMED','newLongEntry':'PINE READY LONG','newShortEntry':'PINE READY SHORT','newLongBreakout':'BREAKOUT','newShortBreakdown':'BREAKDOWN','setupQualityBronzeSignal':'BRONZE','setupQualityStrongSignal':'STRONG','newLongReversalRisk':'LONG REVERSAL RISK','newShortReversalRisk':'SHORT REVERSAL RISK','activePlanDegradedSignal':'ACTIVE PLAN DEGRADED','activePlanExitSignal':'ACTIVE PLAN EXIT','addOnAllowedSignal':'ADD-ON','longT1Hit':'LONG TP HIT','shortT1Hit':'SHORT TP HIT','longArmedLost':'LONG ARMED LOST','shortArmedLost':'SHORT ARMED LOST','newBounceLong':'LONG BOUNCE WATCH','newBounceShort':'SHORT BOUNCE WATCH','avgSetup70Signal':'AVG SETUP >= 70','execution65Signal':'EXECUTION QUALITY >= 65',
}
class PineEngine:
    def __init__(self,symbol,timeframe,tick_size=0.01,parameters=None,currency_rates=None):
        self.symbol=symbol;self.timeframe=str(timeframe);self.tick_size=float(tick_size);self.parameters=validate_parameters(parameters or {});self.parameter_set_id=parameter_hash(self.parameters)
        self.runtime=Execution(load_program(),self.parameters,f'BYBIT:{symbol}.P',self.timeframe,self.tick_size,max(2600,required_history(self.parameters,self.timeframe)))
        self.contexts={};self.chart_bars=[];self.provider=ContextProvider(self);self.runtime.request_provider=self.provider;self.snapshot=None;self.first_bar_start=None
        self.currency_rates=CurrencyRates(currency_rates)
    def update(self,bar,contexts=None,realtime=False):
        bar=bar.to_dict() if hasattr(bar,'to_dict') else dict(bar)
        bar['received_at'] = bar.get('received_at') or bar['end']
        if bar['end']<=bar['start']:raise ValueError('Invalid candle interval')
        if bar['low']>min(bar['open'],bar['close']) or bar['high']<max(bar['open'],bar['close']) or bar['volume']<0:raise ValueError('Invalid OHLCV candle')
        if self.runtime.last_start is not None and bar['start']<=self.runtime.last_start:
            raise ValueError('Confirmed candle already committed; corrections require checkpoint replay')
        if self.first_bar_start is None:self.first_bar_start=bar['start']
        self.contexts=contexts or {};start=time.perf_counter();self.runtime.begin(bar,realtime)
        self.runtime.execute(self.runtime.program.statements)
        m=self.runtime.scopes[0];direction=m.get('direction',0);generation=m.get('lockedStartBar')
        snapshot={field:m.get(pine) for field,pine in FIELDS.items()}
        health='DEGRADED' if self.runtime.missing else 'HEALTHY'
        if self.runtime.count<max(self.parameters['emaSlowLen']*10,self.parameters['atrLen']):health='RECOVERING'
        snapshot.update(exchange='BYBIT',symbol=self.symbol,timeframe=self.timeframe,price=bar['close'],bar_start=bar['start'],candle_start=bar['start'],event_time=min(bar.get('received_at',bar['end']),bar['end']),received_at=bar.get('received_at',bar['end']),confirmed=bar.get('confirmed',True),
          bar=bar,setup_generation_id=f'{self.first_bar_start}:{int(generation)}:{int(direction)}' if not is_na(generation) and direction else 'none',direction='LONG' if direction==1 else 'SHORT' if direction==-1 else 'NONE',
          blockers=str(m.get('blockerText','')).strip().split(),gates={k:bool(v) for k,v in m.items() if k.startswith('gate') and isinstance(v,bool)},
          signals=[name for pine,name in SIGNALS.items() if truth(m.get(pine,False))],metrics={k:v for k,v in m.items() if k not in self.parameters},decision_panel=sorted(self.runtime.capture,key=lambda row:row.get('row',0)),
          data_health=health,missing_contexts=sorted(self.runtime.missing),parity_status='UNVERIFIED',engine_version=ENGINE_VERSION,pine_source_hash=PINE_HASH,parameter_set_id=self.parameter_set_id,git_sha=os.getenv('GIT_SHA','uncommitted'),data_source='Bybit V5 + Binance USD-M BTC',calculation_timestamp=int(time.time()*1000),history_start=self.first_bar_start,
          calculation_ms=(time.perf_counter()-start)*1000,alert_conditions=self.runtime.alertconditions,
          research={'completed':self.runtime.research_completed,'samples':m.get('researchActiveSignals',[]),'stats':m.get('researchStats',[]),'dropped':m.get('researchDropped',0)})
        metric_labels={'formation':'Formation Quality','execution':'Execution Quality','geometry':'Geometry Quality','context':'Context Quality','exhaustion':'Exhaustion Risk','mae':'MAE Risk','avg_setup':'AVG SETUP','continuation':'CONTINUATION','level':'Level Quality','approach':'Approach Quality'}
        snapshot['metric_colors']={field:next((row.get('color') for row in self.runtime.capture if row['label']==label),None) for field,label in metric_labels.items()}
        snapshot['metrics']['source_locals']={k:v for k,v in self.runtime.current.items() if not k.startswith('/g/') and '/ta/' not in k and '/expr/' not in k}
        snapshot['locked_zone']={'center':m.get('lockedLevel'),'top':m.get('lockedZoneTop'),'bottom':m.get('lockedZoneBottom'),'id':m.get('lockedZoneId')}
        snapshot['support_top']=m.get('rawSupportTop1') if not is_na(m.get('rawSupportTop1')) else (m.get('supportTop1') if not is_na(m.get('supportTop1')) else None)
        snapshot['support_bottom']=m.get('rawSupportBottom1') if not is_na(m.get('rawSupportBottom1')) else (m.get('supportBottom1') if not is_na(m.get('supportBottom1')) else None)
        snapshot['resistance_top']=m.get('rawResistanceTop1') if not is_na(m.get('rawResistanceTop1')) else (m.get('resistanceTop1') if not is_na(m.get('resistanceTop1')) else None)
        snapshot['resistance_bottom']=m.get('rawResistanceBottom1') if not is_na(m.get('rawResistanceBottom1')) else (m.get('resistanceBottom1') if not is_na(m.get('resistanceBottom1')) else None)
        snapshot['atr']=m.get('atr') if not is_na(m.get('atr')) else None
        snapshot['active_plan_health']='EXIT' if truth(m.get('activePlanExit')) else 'REDUCE' if truth(m.get('activePlanReduce')) else 'DEGRADED' if truth(m.get('activePlanNoAdd')) else 'HEALTHY' if truth(m.get('activePlanHealthy')) else None
        snapshot['hard_gates']=str(sum(truth(m.get(k)) for k in ('gateInPlay','gateLevel','gateStructure','gateApproach','gateDistance')))+'/5'
        snapshot['target_freshness']='n/a' if not direction else 'CONSUMED / UNRESOLVED' if truth(m.get('targetConsumedUnresolved')) else 'BROKEN/RETEST' if truth(m.get('lockedLiquidityConsumed')) else 'FRESH'
        snapshot['setup_age']=int((bar['start']-m['lockedStartTime'])/60000) if not is_na(m.get('lockedStartTime')) else None
        snapshot['last_signal']=snapshot['signals'][-1] if snapshot['signals'] else self.snapshot.get('last_signal') if self.snapshot else None
        snapshot['addon_gate']=m.get('addOnAllowed')
        snapshot['btc_regime']='BULL' if truth(m.get('btcBull')) else 'BEAR' if truth(m.get('btcBear')) else 'MIXED'
        snapshot=encode(snapshot)
        if bar.get('confirmed',True):
            self.runtime.commit();self.chart_bars.append(bar)
            self.chart_bars=self.chart_bars[-self.runtime.history_limit:]
        self.snapshot=snapshot
        return snapshot
    def export_state(self):
        # Execution/provider exports already encode their histories and UDTs.
        # Normalize the remaining native inputs and worker-added snapshot fields
        # here so storage does not need a second walk over the large histories.
        return {'version':ENGINE_VERSION,'pine_source_hash':PINE_HASH,'parameter_hash':parameter_hash(self.parameters),
                'currency_rates':encode(self.currency_rates.records),'first_bar_start':self.first_bar_start,
                'runtime':self.runtime.export_state(),'contexts':self.provider.export_state(),
                'chart_bars':encode(self.chart_bars),'snapshot':encode(self.snapshot)}
    def restore_state(self,payload):
        if payload['pine_source_hash']!=PINE_HASH or payload['version']!=ENGINE_VERSION or payload['parameter_hash']!=parameter_hash(self.parameters):raise ValueError('Checkpoint source/engine/parameter version mismatch; explicit replay required')
        if payload.get('currency_rates',{})!=self.currency_rates.records:raise ValueError('Checkpoint currency inputs mismatch; explicit replay required')
        self.runtime.restore_state(payload['runtime']);self.provider.restore_state(payload['contexts']);self.chart_bars=payload['chart_bars'];self.first_bar_start=payload['first_bar_start'];self.snapshot=payload['snapshot']
