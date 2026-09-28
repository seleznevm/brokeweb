import copy
import math
import pytest
from backend.engine.wt import WTEngine,WTExecution,parameters,context_requirements,bridge,COMBINATIONS
from backend.engine.syntax import Program
from backend.engine.values import is_na
from backend.engine.context_bars import ContextBars

def candle(i,tf=30,close=None):
    c=100+i if close is None else close
    return dict(start=i*tf*60000,end=(i+1)*tf*60000,open=c-.4,high=c+1,low=c-1,close=c,volume=1000,confirmed=True)

def contexts():
    return {f'{symbol}|{tf}':ContextBars([candle(i,tf) for i in range(200)]) for symbol,tf in [('BYBIT:TESTUSDT.P',120),('BYBIT:TESTUSDT.P',240),('BINANCE:BTCUSDT.P',60)]}

def test_profiles_and_shared_context_requirements():
    assert parameters()['confirmOnClose'] is False
    assert parameters()['useBrokeCorrelation'] is False
    assert context_requirements({},['30'])==({'120','240'},{'60'})
    assert len(COMBINATIONS)==15 and 'T1+T3' in COMBINATIONS and 'T2+T4' in COMBINATIONS
    e=WTEngine('TESTUSDT','30',.01)
    s=e.update(candle(0),{})
    assert s['profile']=='30m' and s['metrics']['minScore']==75 and s['metrics']['entryQualityMin']==68
    assert s['data_health']=='RECOVERING' and s['parity_status']=='UNVERIFIED'

def test_confirmed_htf_has_no_future_leakage_and_advances_at_boundary():
    data=contexts();e=WTEngine('TESTUSDT','30',.01)
    # HTF bar 50 is not closed at chart bar 400's open. Its price must not leak.
    changed=list(data['BYBIT:TESTUSDT.P|240']);changed[50]=candle(50,240,1)
    data['BYBIT:TESTUSDT.P|240']=ContextBars(changed)
    first=e.update(candle(400),data)
    assert first['htf_state']==1 and first['mid_state']==1
    for i in range(401,408):assert e.update(candle(i),data)['htf_state']==1
    assert e.update(candle(408),data)['htf_state']==-1

def test_wilder_dmi_and_population_stdev():
    ex=WTExecution(Program('[p,m,a] = ta.dmi(14,14)\ns = ta.stdev(close,20)'))
    for i in range(50):
        ex.begin(candle(i));ex.execute(ex.program.statements);ex.commit()
    assert ex.scopes[0]['p']==pytest.approx(50)
    assert ex.scopes[0]['m']==0 and ex.scopes[0]['a']==100
    assert ex.scopes[0]['s']==pytest.approx(math.sqrt(33.25))

def test_intrabar_rollback_and_restart_preserve_exact_wt_result():
    data=contexts();one=WTEngine('TESTUSDT','30',.01)
    for i in range(400,520):one.update(candle(i,close=150+math.sin(i/5)*4+i/10),data)
    before=one.export_state();bar=candle(520,close=205);bar['confirmed']=False
    first=one.update(bar,data,True)
    two=WTEngine('TESTUSDT','30',.01);two.restore_state(one.export_state())
    bar['close']=206;bar['high']=207
    assert one.update(bar,data,True)==two.update(bar,data,True)
    direct=WTEngine('TESTUSDT','30',.01);direct.restore_state(before)
    assert one.update(bar,data,True)==direct.update(bar,data,True)
    bar['confirmed']=True
    assert one.update(bar,data,True)==two.update(bar,data,True)
    assert one.export_state()==two.export_state()
    assert first['metrics']['ema21'] is not None and 'bar' not in first

def test_bridge_decodes_broke_values_without_another_market_calculation():
    broke={'direction':'LONG','avg_setup':74.4,'execution':76.7,'level':67.1,'mae':22.4,'exhaustion':18.6,'signals':['LONG WATCH ENTRY'],'metrics':{'gateBtcShock':True},'data_health':'HEALTHY'}
    assert not is_na(bridge(broke))
    e=WTEngine('TESTUSDT','30',.01,{'useBrokeCorrelation':True})
    s=e.update(candle(400),contexts(),True,broke)
    m=s['metrics']
    assert [m[k] for k in ('brokeDir','brokeAvgSrc','brokeExecutionSrc','brokeLevelSrc','brokeMaeSrc','brokeExhaustionSrc','brokeEntryDir','brokeBtcOkSrc')]==[1,74,77,67,22,19,1,1]
    assert m['brokeDataValid'] is True
    bad=e.update(candle(401),contexts(),True,None)
    assert bad['data_health']=='DEGRADED' and bad['broke_valid'] is False

def test_wt_source_produces_real_setup_events():
    p={'profileMode':'Manual','manualMidTf':'120','minScoreManual':0,'requireLiquidityHardManual':False,'useBtcFilter':False}
    e=WTEngine('TESTUSDT','30',.01,p);found=[]
    for i in range(400,600):
        c=200+i*.15+math.sin(i/4)*3
        s=e.update(candle(i,close=c),contexts())
        if s['signals']:found.extend(s['signals'])
    assert 'T1' in found
    assert s['entry'] is not None and s['tp1'] is not None and s['sl'] is not None

def test_parameter_change_rejects_checkpoint():
    e=WTEngine('TESTUSDT','30',.01)
    with pytest.raises(ValueError):WTEngine('TESTUSDT','30',.01,{'confirmOnClose':True}).restore_state(e.export_state())
    with pytest.raises(ValueError):parameters({'riskUsdt':float('nan')})
