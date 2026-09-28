import asyncio
import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select,func
from backend.api.main import create_app
from backend.models.repository import Repository,now_ms
from backend.models.schema import WTCurrent,WTEvent,Current,MarketBar,Delivery,Rule
from backend.alerts.notifier import deliver_one
from backend.wt import parse_message,receive,save_engine,cleanup,RETENTION_MS

MESSAGE='WT LONG | TESTUSDT.P | TF=30 | T1 T3 | Score=85 | EQ=72.5 | Action=WAIT RETEST | Entry=10.12345 | SL=9.5 | TP1=10.6 | Pos=1500 USDT | Risk=25 USDT | LIQ=11.5'
READY='WT READY TO ENTER SHORT | TESTUSDT.P | TF=30 | T2 T4 | EQ=80.0 | Price=10 | PlanEntry=10.1 | dEntry=+0.10R | SL=11 | TP1=9.3 | LIQ=8 | RRliq=2 | Pos=250 USDT | Risk=25 USDT | Broke=OFF'

@pytest.fixture
def setup(tmp_path,monkeypatch):
    monkeypatch.setenv('WT_TRADINGVIEW_WEBHOOK_KEY','wt-secret');monkeypatch.setenv('WT_TRADINGVIEW_TELEGRAM_ENABLED','true')
    repo=Repository(f'sqlite:///{tmp_path}/wt.db');repo.initialize()
    return repo,TestClient(create_app(repo))

def test_parse_all_combinations_and_ready_distinguishes_tp_targets():
    p=parse_message(MESSAGE,'BYBIT')
    assert p['signals']==['T1','T3','T1+T3'] and p['entry']==10.12345
    assert p['bar_start'] is None and p['confirmed'] is False
    ready=parse_message(READY,'BYBIT')
    assert ready['signals']==['T2','T4','T2+T4','READY TO ENTER'] and ready['action']=='ENTER NOW'
    assert ready['price']==10 and ready['entry']==10.1
    with pytest.raises(ValueError):parse_message(MESSAGE.replace('T1 T3','TP1 TP3'),'BYBIT')
    with pytest.raises(ValueError):parse_message(MESSAGE.replace('TF=30','TF=5'),'BYBIT')

def test_auth_atomic_storage_and_retry_dedupe(setup):
    repo,client=setup;path='/api/webhooks/tradingview/wt'
    assert client.post(path,content=MESSAGE).status_code==401
    assert client.post(path+'?key=bad',content=MESSAGE).status_code==401
    url=path+'?key=wt-secret'
    first=client.post(url,content=MESSAGE);assert first.status_code==201 and first.json()['telegram']=='queued'
    assert client.post(url,content=MESSAGE).json()['status']=='duplicate'
    assert client.post(url,content=READY).json()['status']=='stored'
    assert client.post(url,content='BROKE LONG').status_code==422
    assert client.post(url,content='x'*65537).status_code==413
    assert client.get('/api/wt/setups?signal_source=tradingview').json()['items'][0]['direction']=='SHORT'
    assert client.get('/api/wt/setups').json()['items']==[]
    with repo.session() as s:
        assert s.scalar(select(func.count()).select_from(WTEvent))==3
        assert s.scalar(select(func.count()).select_from(Delivery))==2
        assert s.scalar(select(func.count()).select_from(MarketBar))==0
        assert s.scalar(select(func.count()).select_from(Current))==0

def test_explicit_event_id_dedupes_across_restart(setup):
    repo,client=setup;url='/api/webhooks/tradingview/wt?key=wt-secret'
    body={'message':MESSAGE,'event_id':'tv-1','bar_start':now_ms()//1800000*1800000}
    first=client.post(url,json=body).json()
    reopened=TestClient(create_app(Repository(str(repo.engine.url))))
    assert reopened.post(url,json=body).json()['id']==first['id']
    assert reopened.post(url,json={**body,'message':READY}).status_code==422

def test_telegram_uses_wt_reference_without_requiring_a_broke_setup(setup):
    repo,client=setup;receive(repo,MESSAGE,'BYBIT');sent=[]
    async def handler(request):sent.append(request.read());return httpx.Response(200,json={'ok':True})
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as transport:
            assert await deliver_one(repo,transport,'fake','fake')
            assert not await deliver_one(repo,transport,'fake','fake')
    asyncio.run(run())
    assert len(sent)==1 and b'T1+T3' in sent[0] and b'TP1' in sent[0]
    with repo.session() as s:assert s.scalar(select(Delivery)).status=='sent'

def test_rules_are_scoped_and_preserve_source_specific_state(setup,monkeypatch):
    repo,client=setup;monkeypatch.setenv('WT_TRADINGVIEW_TELEGRAM_ENABLED','false')
    conditions={'field':'signals','op':'contains','value':'T1+T3'}
    for strategy in ('BROKE_SETUPS','WT_SETUPS'):
        assert client.post('/api/alerts/rules',json={'name':strategy,'strategy':strategy,'conditions':conditions,'mode':'realtime','cooldown_seconds':0}).status_code==201
    receive(repo,MESSAGE,'BYBIT')
    preview=client.post('/api/alerts/rules/test',json={'conditions':conditions,'strategy':'WT_SETUPS'}).json()
    assert preview['total']==1
    with repo.session() as s:
        deliveries=s.scalars(select(Delivery)).all()
        assert len(deliveries)==1 and deliveries[0].payload['rule_name']=='WT_SETUPS'
    exported=client.get('/api/alerts/rules/export').json()
    assert {r['strategy'] for r in exported['rules']}=={'BROKE_SETUPS','WT_SETUPS'}

def test_wt_configuration_is_not_a_health_service(setup):
    repo,client=setup
    saved=client.put('/api/wt/parameters',json={'values':{'useBrokeCorrelation':True}})
    assert saved.status_code==200 and saved.json()['values']['useBrokeCorrelation']
    assert 'wt-settings' not in client.get('/api/health').json()['services']
    assert client.put('/api/wt/parameters',json={'values':{'riskUsdt':-1}}).status_code==422

def test_retention_removes_only_old_wt_events_and_webhook_copies(setup):
    repo,client=setup;receive(repo,MESSAGE,'BYBIT');now=now_ms()
    with repo.session.begin() as s:
        event=s.scalar(select(WTEvent));event.received_at=now-RETENTION_MS-1
        s.scalar(select(WTCurrent)).updated_at=event.received_at
        s.scalar(select(Delivery)).created_at=event.received_at
    assert cleanup(repo,now)==1
    with repo.session() as s:
        for model in (WTCurrent,WTEvent,Delivery):assert s.scalar(select(func.count()).select_from(model))==0
