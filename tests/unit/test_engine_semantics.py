"""Source-derived engine regressions, NOT external TradingView parity fixtures."""
from copy import deepcopy
from types import SimpleNamespace
import math
import pytest
from backend.engine.syntax import Program, expression, load_program
from backend.engine.interpreter import Execution
from backend.engine.contexts import ContextProvider
from backend.engine.values import is_na
from backend.engine import pine_compat as primitives


def candle(index=0, *, tf=60000, close=10.0, confirmed=True, received_at=None):
    value={'start':index*tf,'end':(index+1)*tf,'open':10.0,'high':max(12.0,close),'low':min(8.0,close),'close':close,'volume':5.0,'confirmed':confirmed}
    if received_at is not None:value['received_at']=received_at
    return value


def run_bar(ex, bar, realtime=False, outer=None, commit=True):
    ex.begin(bar,realtime,outer)
    ex.execute(ex.program.statements)
    result=dict(ex.scopes[0])
    if commit:ex.commit()
    return result


@pytest.mark.parametrize('text,expected', [
    ('1 + 2 * 3',7), ('(1 + 2) * 3',9),
    ('false ? 1 : true ? 2 : 3',2),
    ('true or false and false',True),
    ('not false and true',True),
    ('-5 % 3',-2), ('5 / 2',2.5),
    ('na == na',False), ('na != na',False),
    ('false and unknownVariable',False), ('true or unknownVariable',True),
])
def test_expression_precedence_and_v6_lazy_boolean(text,expected):
    ex=Execution(Program('float nothing = 0'))
    ex.begin(candle())
    assert ex.eval(expression(text,10001))==expected


def test_var_rolls_back_while_varip_survives_and_isnew_resets_samples():
    ex=Execution(Program('''var int normal = 0
varip int ticks = 0
if barstate.isnew
    ticks := 0
normal += 1
ticks += 1
float previousNormal = normal[1]
'''))
    first=run_bar(ex,candle())
    assert first['normal']==1 and first['ticks']==1
    tick1=run_bar(ex,candle(1,confirmed=False),True,commit=False)
    tick2=run_bar(ex,candle(1,confirmed=False),True,commit=False)
    assert tick1['normal']==tick2['normal']==2
    assert (tick1['ticks'],tick2['ticks'])==(1,2)
    closing=run_bar(ex,candle(1),True)
    assert closing['ticks']==3 and closing['normal']==2
    assert closing['previousNormal']==1
    next_bar=run_bar(ex,candle(2,confirmed=False),True,commit=False)
    assert next_bar['ticks']==1 and next_bar['normal']==3


def test_function_callsite_histories_do_not_alias_global_or_other_callsite():
    ex=Execution(Program('''f_delta(float value) =>
    float local = value
    local - local[1]
float first = f_delta(close)
float second = f_delta(close * 2)
float local = 99
'''))
    a=run_bar(ex,candle(close=10))
    b=run_bar(ex,candle(1,close=13))
    assert is_na(a['first']) and is_na(a['second'])
    assert b['first']==3 and b['second']==6
    assert b['local']==99


def test_function_var_initializes_once_per_written_call_and_persists():
    ex=Execution(Program('''f_count(float increment) =>
    var float total = 0.0
    total += increment
    total
float first = f_count(1)
float second = f_count(10)
'''))
    assert run_bar(ex,candle())['second']==10
    output=run_bar(ex,candle(1))
    assert (output['first'],output['second'])==(2,20)
    realtime=run_bar(ex,candle(2,confirmed=False),True,commit=False)
    realtime_again=run_bar(ex,candle(2,confirmed=False),True,commit=False)
    assert realtime['first']==realtime_again['first']==3


def test_persistent_array_mutations_rollback_and_checkpoint_restore():
    source='''var array<float> prices = array.new_float()
array.push(prices, close)
int samples = array.size(prices)
'''
    ex=Execution(Program(source))
    run_bar(ex,candle(close=10))
    a=run_bar(ex,candle(1,close=11,confirmed=False),True,commit=False)
    b=run_bar(ex,candle(1,close=12,confirmed=False),True,commit=False)
    assert a['prices']==[10,11] and b['prices']==[10,12]
    assert ex.persistent['/g/prices']==[10]
    run_bar(ex,candle(1,close=13),True)
    checkpoint=ex.export_state()
    restored=Execution(Program(source));restored.restore_state(checkpoint)
    output=run_bar(restored,candle(2,close=14))
    assert output['prices']==[10,13,14] and output['samples']==3


@pytest.mark.parametrize('function', ['ema','rma','sma','sum','highest','lowest'])
def test_interpreter_rolling_functions_match_independent_primitives_with_na(function):
    pine_name='math.sum' if function=='sum' else 'ta.'+function
    ex=Execution(Program(f'float result = {pine_name}(sample, 2)'))
    history=[]
    for index,value in enumerate([1.0,math.nan,3.0,7.0,math.nan,9.0]):
        history.append(value)
        actual=run_bar(ex,candle(index),outer={'sample':value})['result']
        expected=getattr(primitives,function)(history,2)
        assert is_na(actual) if is_na(expected) else actual==pytest.approx(expected)


def test_interpreter_pivot_confirmation_matches_primitive_plateau():
    ex=Execution(Program('float result = ta.pivothigh(high, 1, 1)'))
    highs=[]
    for index,value in enumerate([10,12,12,11,13,10]):
        highs.append(value)
        bar=candle(index);bar.update(open=value,close=value,high=value,low=value-1)
        actual=run_bar(ex,bar)['result']
        expected=primitives.pivothigh(highs,1,1)
        assert is_na(actual) if is_na(expected) else actual==expected


def provider_engine(program, streams, timeframe='1'):
    ex=Execution(program,timeframe=timeframe)
    engine=SimpleNamespace(contexts=streams,chart_bars=[],runtime=ex,parameters={},tick_size=.01)
    provider=ContextProvider(engine);ex.request_provider=provider
    return ex,provider,engine


def test_higher_tf_closed_history_has_no_lookahead_and_gaps_off():
    program=Program('float contextClose = request.security("BINANCE:BTCUSDT.P", "5", close)')
    htf=candle(0,tf=300000,close=21)
    ex,_,_=provider_engine(program,{'BINANCE:BTCUSDT.P|5':[htf]})
    assert is_na(run_bar(ex,candle(0))['contextClose'])
    assert is_na(run_bar(ex,candle(3))['contextClose'])
    assert run_bar(ex,candle(4))['contextClose']==21
    assert run_bar(ex,candle(5))['contextClose']==21


def test_developing_htf_recalculates_from_confirmed_context_checkpoint():
    program=Program('float contextEma = request.security("BINANCE:BTCUSDT.P", "5", ta.ema(close, 3))')
    closed=candle(0,tf=300000,close=10)
    ex,provider,engine=provider_engine(program,{'BINANCE:BTCUSDT.P|5':[closed]})
    assert run_bar(ex,candle(4))['contextEma']==10
    live=candle(1,tf=300000,close=20,confirmed=False,received_at=310000)
    engine.contexts['BINANCE:BTCUSDT.P|5']=[closed,live]
    a=run_bar(ex,candle(5,confirmed=False,received_at=310000),True,commit=False)
    engine.contexts['BINANCE:BTCUSDT.P|5'][-1]={**live,'close':30,'high':30,'received_at':320000}
    b=run_bar(ex,candle(5,confirmed=False,received_at=320000),True,commit=False)
    assert a['contextEma']==15 and b['contextEma']==20
    context_execution=next(iter(provider.streams.values()))[0]
    assert context_execution.count==1


def test_missing_scalar_security_call_has_scalar_na_not_one_item_tuple():
    program=Program('float contextEma = request.security("BINANCE:BTCUSDT.P", "5", ta.ema(close, 3))')
    ex,_,_=provider_engine(program,{})
    assert is_na(run_bar(ex,candle())['contextEma'])


def test_lower_tf_arrays_include_only_completed_intrabar_and_no_future_close():
    program=Program('[opens, closes] = request.security_lower_tf("BYBIT:BTCUSDT.P", "30S", [open, close])')
    micro=[candle(0,tf=30000,close=11),candle(1,tf=30000,close=12)]
    ex,_,_=provider_engine(program,{'BYBIT:BTCUSDT.P|30S':micro})
    a=run_bar(ex,candle(0,confirmed=False,received_at=35000),True,commit=False)
    assert a['closes']==[11]
    b=run_bar(ex,candle(0,received_at=60000),True)
    assert b['closes']==[11,12]


def source_execution(parameters=None):
    ex=Execution(load_program(),parameters or {})
    ex.begin(candle())
    # Constants are source statements, not independently maintained test enums.
    ex.execute([s for s in ex.program.statements if s.kind=='assign' and s.meta['mode']=='const'])
    return ex


def source_call(ex,name,*args,line=20001):
    names=[]
    for i,value in enumerate(args):
        key=f'testArg{i}';ex.scopes[0][key]=value;names.append(key)
    return ex.eval(expression(f'{name}({", ".join(names)})',line))


def test_actual_source_level_quality_risk_ramp_and_path_weights():
    ex=source_execution({'levelTolAtr':.25})
    # 4 touches 35; width .25/(.25*2) gives 10; prominence 1.5 gives
    # 15; age 125 gives 7.5. Total 67.5, independent of current close.
    assert source_call(ex,'f_levelQuality',4,1.5,125,.25)==67.5
    assert source_call(ex,'f_riskScale',65,50,80)==.5
    for code in range(10):
        weights=source_call(ex,'f_pathMaeWeights',code)
        assert sum(weights)==pytest.approx(1)
    assert source_call(ex,'f_pathMaeAdjusted',6,100,80,100,120,30)==[60,30,35,100]


def empty_zone_arrays():return [[] for _ in range(12)]


def test_actual_source_zone_merges_fresh_only_and_retains_first_pivot_identity():
    ex=source_execution()
    arrays=empty_zone_arrays()
    assert source_call(ex,'f_upsertZone',*arrays,100,10,2,2,.5,.1,3)==0
    assert source_call(ex,'f_upsertZone',*arrays,100.5,12,3,2,.5,.1,3)==0
    centers,mins,maxs,paddings,touches,first,newest,prom,consumed,broken,failed,counts=arrays
    assert centers==[100.25] and touches==[2] and first==[10] and newest==[12]
    assert mins==[100] and maxs==[100.5] and paddings==[.2] and prom==[2.5]
    consumed[0]=15
    assert source_call(ex,'f_upsertZone',*arrays,100.4,16,2,2,.5,.1,3)==1
    assert first==[10,16] and touches==[2,1]


def test_actual_source_zone_sweep_then_confirmed_break_and_failed_sweep():
    ex=source_execution({'zoneBreakBufferAtr':.1})
    arrays=[[100],[100],[.2],[5],[-1],[-1],[-1],[0]]
    ex.scopes[0].update(bar_index=10,high=101,low=99,close=100.6)
    result=source_call(ex,'f_updateZoneLifecycle',*arrays,True,1,2,3)
    assert result[:3]==[True,False,False] and arrays[4]==[10] and arrays[7]==[1]
    ex.scopes[0]['bar_index']=11
    result=source_call(ex,'f_updateZoneLifecycle',*arrays,True,1,2,3)
    assert result[:3]==[False,True,False] and arrays[5]==[11]
    other=[[100],[100],[.2],[6],[-1],[-1],[-1],[0]]
    ex.scopes[0].update(bar_index=12,high=101,close=99.7)
    result=source_call(ex,'f_updateZoneLifecycle',*other,True,1,2,3)
    assert result[:3]==[True,False,True] and other[6]==[12]
    live=[[100],[100],[.2],[7],[-1],[-1],[-1],[0]]
    ex.special['barstate.isconfirmed']=False
    assert source_call(ex,'f_updateZoneLifecycle',*live,True,1,2,3)[:3]==[False,False,False]
    assert live[4]==[-1]


def test_actual_source_micro_quality_uses_all_intrabar_arrays():
    ex=source_execution()
    result=source_call(ex,'f_microIntrabarEngine',[True,False],[False,True],[10,11],[12,13],[9,10],[11,12],[1,3])
    # Long occupancy 50%; range4, net2, close position.75, 100% bullish.
    long_direction=.5*100*.4+.75*100*.35+100*.25
    short_direction=.25*100*.35
    assert result[:2]==pytest.approx([50*.55+long_direction*.3+100*.15,50*.55+short_direction*.3])
    assert result[2:]==[50,50,2,False,True]


def fsm_execution():
    program=load_program()
    selected=[s for s in program.statements if s.kind=='assign' and s.meta['mode']=='const' or 3914<=s.line<=3981]
    # Isolate the original AST nodes exactly; no hand-written transition engine.
    subset=Program('float placeholder = 0');subset.statements=selected;subset.functions=program.functions;subset.types=program.types
    return Execution(subset)


def fsm_inputs(**changes):
    values=dict(direction=1,lockedStartBar=0,riskPath='PRE-BREAK',entryPath='WAIT',candidateEntryPath='COMPRESSION',entryPathScore=0,candidateEntryPathScore=60,pathMemoryScore=40,validWatch=False,activeApproach=False,armed=False,gateTrigger=False,generationPostBreakPhase=False,openSkyModeActive=False,entryReady=False)
    values.update(changes);return values


def test_actual_source_fsm_monotonic_progression_degradation_and_generation_reset():
    ex=fsm_execution()
    states=[]
    for i,changes in enumerate([{}, {'validWatch':True}, {'validWatch':True,'activeApproach':True}, {'armed':True}, {'armed':True,'gateTrigger':True}, {'generationPostBreakPhase':True}, {'entryReady':True}, {}]):
        output=run_bar(ex,candle(i),outer=fsm_inputs(**changes));states.append(output['setupFsmState'])
    assert states==[1,2,3,4,5,6,7,8]
    assert run_bar(ex,candle(8),outer=fsm_inputs())['setupFsmState']==8
    reset=run_bar(ex,candle(9),outer=fsm_inputs(lockedStartBar=9))
    assert reset['setupFsmState']==1
    assert run_bar(ex,candle(10),outer=fsm_inputs(direction=0))['setupFsmState']==0


def test_actual_source_fsm_transition_requires_confirmed_bar():
    ex=fsm_execution()
    run_bar(ex,candle(),outer=fsm_inputs(validWatch=True))
    live=run_bar(ex,candle(1,confirmed=False),True,outer=fsm_inputs(armed=True,gateTrigger=True,entryReady=True),commit=False)
    assert live['setupFsmState']==2
    closed=run_bar(ex,candle(1),True,outer=fsm_inputs(armed=True,gateTrigger=True,entryReady=True))
    assert closed['setupFsmState']==7 and closed['fsmNewReadyEvent'] is True


def source_slice_execution(start,end):
    program=load_program()
    subset=Program('float placeholder = 0')
    subset.statements=[s for s in program.statements if s.kind=='assign' and s.meta['mode']=='const' or start<=s.line<=end]
    subset.functions=program.functions;subset.types=program.types
    return Execution(subset)


def avg_inputs(score,**changes):
    values=dict(direction=1,lockedStartBar=0,formationQuality=score,executionQuality=score,geometryQuality=score,contextQuality=score,exhaustionSafety=score,maeSafety=score,maeRisk=20,setupFsmState=2,gateStructure=True,gateTrigger=True,targetConsumedUnresolved=False,gateBtcShock=True,avgSetupBarsHistory=1)
    values.update(changes);return values


def test_actual_source_avg_intrabar_sampling_and_threshold_latch_through_close():
    ex=source_slice_execution(4021,4128)
    run_bar(ex,candle(),outer=avg_inputs(50))
    first=run_bar(ex,candle(1,confirmed=False),True,outer=avg_inputs(60),commit=False)
    assert first['avgSetup']==60 and first['avgSetupIntrabarSamples']==1
    assert first['avgSetup70Signal'] is False
    crossed=run_bar(ex,candle(1,confirmed=False),True,outer=avg_inputs(80),commit=False)
    assert crossed['avgSetup']==70 and crossed['avgSetupIntrabarSamples']==2
    assert crossed['avgSetup70Signal'] is True and crossed['execution65Signal'] is True
    fell=run_bar(ex,candle(1,confirmed=False),True,outer=avg_inputs(40),commit=False)
    assert fell['avgSetup']==60 and fell['avgSetup70AboveNow'] is False
    assert fell['avgSetup70Signal'] is True
    closed=run_bar(ex,candle(1),True,outer=avg_inputs(60))
    assert closed['avgSetupIntrabarSamples']==4 and closed['avgSetup70Signal'] is True
    nextbar=run_bar(ex,candle(2,confirmed=False),True,outer=avg_inputs(60),commit=False)
    assert nextbar['avgSetupIntrabarSamples']==1 and nextbar['avgSetup70Signal'] is False


def test_actual_source_avg_history_excludes_previous_generation_and_live_risk_blocks_cross():
    ex=source_slice_execution(4021,4128)
    run_bar(ex,candle(),outer=avg_inputs(40,avgSetupBarsHistory=3))
    same=run_bar(ex,candle(1),outer=avg_inputs(60,avgSetupBarsHistory=3))
    assert same['avgSetup']==50 and same['avgSetupHistoryCount']==2
    new=run_bar(ex,candle(2,confirmed=False),True,outer=avg_inputs(90,lockedStartBar=2,avgSetupBarsHistory=3,maeRisk=65),commit=False)
    assert new['avgSetup']==90 and new['avgSetupHistoryCount']==1
    assert new['avgSetupRiskOverride'] is True and new['avgSetup70Signal'] is False
    assert new['execution65Signal'] is True


def test_actual_source_hourly_notional_uses_hlc3_not_exchange_turnover():
    program=load_program()
    ex=Execution(program)
    ex.begin(candle())
    bar=ex.bar
    bar.update(high=18,low=6,close=12,volume=4,turnover=999)
    ex.begin(bar)
    assert source_call(ex,'f_quoteNotionalVolume')==48
    ex.special['syminfo.volumetype']='quote'
    assert source_call(ex,'f_quoteNotionalVolume')==4
    ex.special['syminfo.volumetype']='tick'
    assert is_na(source_call(ex,'f_quoteNotionalVolume'))


def test_actual_source_frozen_trade_plan_and_no_target_hit_on_entry_candle():
    ex=source_slice_execution(4500,4595)
    def inputs(**changes):
        values=dict(lockedDirection=1,lockedStartBar=0,newLongEntry=False,newShortEntry=False,estimatedSL=8,targetT1=15,rrToT1=2.5,expectedMovePct=50,stopDistanceAtr=1,entryPath='BREAKOUT',entryPathScore=80,targetAhead=True,alertBarOk=True,releaseSetupNextBar=False)
        values.update(changes);return values
    entry_bar=candle(0);entry_bar['high']=20
    entry=run_bar(ex,entry_bar,outer=inputs(newLongEntry=True))
    assert entry['tradePlanActive'] is True and entry['longT1Hit'] is False
    later=run_bar(ex,candle(1),outer=inputs(estimatedSL=9,targetT1=30,rrToT1=20))
    assert (later['tradePlanSL'],later['tradePlanT1'],later['tradePlanRR'])==(8,15,2.5)
    hit=run_bar(ex,candle(2,close=16),outer=inputs())
    assert hit['longT1Hit'] is True and hit['longTargetActive'] is False
    changed=run_bar(ex,candle(3),outer=inputs(lockedStartBar=3))
    assert changed['tradePlanActive'] is False and is_na(changed['tradePlanSL'])


def test_actual_source_research_horizon_same_bar_ambiguity_and_frozen_atr():
    ex=source_execution()
    active=[];stats=[ex.eval(expression('ResearchStats.new()',22000))]
    source_call(ex,'f_researchAdd',active,0,1,10,2,12,8,10)
    ex.scopes[0].update(high=14,low=6,bar_index=0)
    assert source_call(ex,'f_researchUpdate',active,stats,1,1,2)==0
    assert active[0].fields['mfe']==0 and is_na(active[0].fields['t1Bar'])
    ex.scopes[0].update(high=13,low=7,bar_index=1)
    assert source_call(ex,'f_researchUpdate',active,stats,1,1,2)==1
    result=stats[0].fields
    assert active==[] and result['n']==1
    assert result['sumMfeAtr']==1.5 and result['sumMaeAtr']==1.5
    assert (result['t1Hit'],result['slHit'],result['ambiguous'],result['t1First'],result['slFirst'],result['clean'])==(1,1,1,0,0,1)


@pytest.mark.parametrize('op,expected', [('==',True),('!=',False),('>',False),('<',False),('>=',True),('<=',True)])
def test_pine_float_comparison_rounds_operands_to_nine_places(op,expected):
    from backend.engine.values import binary
    assert binary(op,.7000000000000001,.7) is expected
    assert binary(op,-.7000000000000001,-.7) is expected
    assert binary('>',.700000001,.7)
    assert binary('!=',.000000001,0.)
    assert binary('+',.7,.0000000001)==.7+.0000000001


def test_round_target_at_exact_decimal_boundary_from_ethfi_reference():
    # Reduced original source lines 2992/2994. External observation:
    # ETHFIUSDT, 30m, 2026-09-10 15:30 UTC, CSV Forecast T1 = 0.8.
    program=Program('''float roundAnchorLong = 0.7
float roundStepLong = 0.1
float roundAbove = math.ceil(roundAnchorLong / roundStepLong) * roundStepLong
roundAbove := roundAbove <= roundAnchorLong ? roundAbove + roundStepLong : roundAbove
''')
    result=run_bar(Execution(program),candle())
    assert result['roundAbove']==.8
