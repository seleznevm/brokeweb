from types import SimpleNamespace
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select,func
from backend.models.repository import Repository
from backend.api.main import create_app
from backend.models.schema import WTEvent
from backend.statistics.models import Evaluation
from backend.statistics.outcomes import evaluate,HORIZON,POLICY
from backend.statistics.capture import candidates,capture
from backend.statistics.worker import save,observe
def bar(start=0,high=101,low=99.5):
    return dict(start=start,end=start+60000,open=100,close=100,high=high,low=low,confirmed=True)
def plan(side='LONG',start=0):return dict(entry=100,sl=99 if side=='LONG' else 101,direction=side,event_time=start)
@pytest.mark.parametrize('side,high,low,status',[('LONG',102,99.5,'WIN'),('LONG',101,99,'LOSS'),('SHORT',100.5,98,'WIN'),('SHORT',101,99,'LOSS'),('LONG',102,99,'AMBIGUOUS')])
def test_directional_first_hit(side,high,low,status):
    assert evaluate(plan(side),[bar(high=high,low=low)],60000)['status']==status
def test_boundary_before_signal_never_win():
    assert evaluate(plan(start=30000),[bar(high=103)],60000)['status']=='AMBIGUOUS'
def test_gap_before_win():
    assert evaluate(plan(),[bar(60000,high=103)],120000)['status']=='DATA_GAP'
def test_win_remains_win_before_later_loss():
    assert evaluate(plan(),[bar(high=102),bar(60000,low=98)],120000)['status']=='WIN'
def test_expiry_requires_full_coverage():
    bars=[bar(t) for t in range(0,HORIZON,60000)]
    assert evaluate(plan(),bars,HORIZON)['status']=='EXPIRED'
    assert evaluate(plan(),bars[:-1],HORIZON)['status']=='DATA_GAP'
def test_incomplete_minute_not_used():
    b=bar(high=103);b['confirmed']=False
    assert evaluate(plan(),[b],30000)['status']=='OPEN'
def test_invalid_plan():
    assert evaluate({**plan(),'sl':100},[],0)['status']=='INVALID'
def test_partial_last_minute_touch_ambiguous():
    bars=[bar(t) for t in range(0,HORIZON+60000,60000)]
    bars[-1]['high']=103
    assert evaluate(plan(start=30000),bars,HORIZON+60000)['status']=='AMBIGUOUS'
def wt(source='engine',**extra):
    p=dict(strategy='WT_SETUPS',signals=['T1','T3','T1+T3'],signal_setups=['T1','T3'],direction='LONG',entry=100,price=100,sl=99,event_time=60000,plan_bar_start=0,setup_generation_id='g',parameter_hash='v',**extra)
    return SimpleNamespace(id=1,exchange='BYBIT',symbol='TESTUSDT',timeframe='30',received_at=61000,signal_source=source,dedupe_key='key',payload=p)
def test_sources_and_families_separate():
    engine=candidates(wt(),True);tv=candidates(wt('tradingview'),True)
    assert {x.family for x in engine}=={'T1','T3'}
    assert {x.id for x in engine}.isdisjoint(x.id for x in tv)
    assert all(x.plan['time_basis']=='received_at_only' for x in tv)
def test_duplicate_and_replay():
    assert not candidates(wt(duplicate_of=1),True)
    assert {x.id for x in candidates(wt(replay=True),True)}.isdisjoint(x.id for x in candidates(wt(),True))
def test_update_same_plan_dedup():
    a=wt();b=wt();b.id=2;b.payload['event_time']=120000
    assert [x.id for x in candidates(a,True)]==[x.id for x in candidates(b,True)]
def test_ready_confirmation_is_not_new_setup():
    r=wt();r.payload['signals'].append('READY TO ENTER')
    assert not candidates(r,True)
    r.payload['metrics']={'fireLongAlert':True}
    assert len(candidates(r,True))==2
    assert not candidates(wt('tradingview',raw_body='WT READY TO ENTER LONG'),True)
@pytest.fixture
def repo(tmp_path):
    r=Repository('sqlite:///'+str(tmp_path/'stats.db'));r.initialize();return r
def test_capture_restart_and_immutable_outcome(repo):
    r=wt()
    with repo.session.begin() as s:
        s.add(WTEvent(dedupe_key='one',received_at=60000,exchange=r.exchange,symbol=r.symbol,timeframe=r.timeframe,signal_source=r.signal_source,payload=r.payload))
    assert capture(repo)['wt']==2
    assert capture(repo)['wt']==0
    with repo.session() as s:row=s.scalar(select(Evaluation))
    save(repo,row.id,{'status':'WIN'},120000)
    save(repo,row.id,{'status':'LOSS'},180000)
    with repo.session() as s:assert s.get(Evaluation,row.id).status=='WIN'
def test_api_aggregation_not_paginated_and_source_filters(repo):
    for src in ('engine','tradingview'):
        r=wt(src)
        with repo.session.begin() as s:
            for item in candidates(r,True):
                item.status='WIN' if src=='engine' else 'LOSS';s.add(item)
    client=TestClient(create_app(repo))
    url='/api/statistics?strategy=WT_SETUPS&start=0&end=200000&limit=1'
    data=client.get(url).json()
    assert data['total']==4 and len(data['items'])==1 and data['winrate']==50
    assert len(data['breakdowns'])==4
    assert client.get(url+'&source=engine').json()['winrate']==100
    assert client.get(url+'&start=200000').status_code==422
    exported=client.get(url+'&export=csv').text
    assert len(exported.splitlines())==5
    assert client.get(url+'&mode=replay').json()['winrate'] is None
@pytest.mark.asyncio
async def test_observer_fetches_pending_symbol_without_universe(repo):
    class Adapter:
        calls=0
        async def backfill(self,*args,**kw):
            from backend.marketdata.models import Bar
            self.calls+=1
            return [Bar('BYBIT','TESTUSDT','1',60000,120000,100,102,99.5,100,1,confirmed=True)]
    adapter=Adapter();row=candidates(wt(),True)[0]
    assert (await observe(repo,row,{'BYBIT':adapter},120000))['status']=='WIN'
    assert adapter.calls==1
    assert (await observe(repo,row,{'BYBIT':adapter},120000))['status']=='WIN'
    assert adapter.calls==1
