import json
import logging
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select,func
from backend.api.main import create_app,WebhookAccessFilter
from backend.models.repository import Repository,now_ms
from backend.models.schema import TradingViewAlert,Signal,Rule,RuleVersion
from backend.tradingview import cleanup,parse_body,RETENTION_MS

MESSAGE='WATCH ENTRY | EARLY | LONG | BYBIT:ETHFIUSDT.P | TF=30 | Close=0.731 | AVG=71.2 | F/E=72/65 | Ex/MAE=10/20 | Path=BREAKOUT | Fresh=Y | Add=N | SL=0.7 | TP=0.8 | RR=2.23'

@pytest.fixture
def setup(tmp_path,monkeypatch):
    monkeypatch.setenv('TRADINGVIEW_WEBHOOK_KEY','test-secret')
    repo=Repository(f'sqlite:///{tmp_path}/webhook.db');repo.initialize()
    return repo,TestClient(create_app(repo))

def test_native_text_and_json_are_durable_and_auth_required(setup,monkeypatch):
    repo,client=setup
    url='/api/webhooks/tradingview'
    assert client.post(url,content=MESSAGE).status_code==401
    assert client.post(url+'?key=wrong',content=MESSAGE).status_code==401
    response=client.post(url+'?key=test-secret',content=MESSAGE,headers={'Content-Type':'text/plain'})
    assert response.status_code==201
    bar=now_ms()//1800000*1800000
    assert client.post(url+'?key=test-secret',json={'message':MESSAGE,'bar_start':bar}).status_code==201
    # No false dedupe: identical text on different bars need not mean a retry.
    assert client.post(url+'?key=test-secret',content=MESSAGE).status_code==201
    reopened=Repository(str(repo.engine.url))
    with reopened.session() as session:
        rows=session.scalars(select(TradingViewAlert).order_by(TradingViewAlert.id)).all()
        assert len(rows)==3 and rows[0].raw_body==MESSAGE
        assert rows[0].symbol=='ETHFIUSDT' and rows[0].name=='LONG WATCH ENTRY'
        assert rows[0].bar_start is None and rows[1].bar_start==bar
        assert rows[0].payload['measurements']['avg_setup']==71.2
    assert client.get('/api/tradingview/alerts?symbol=ETHFIUSDT.P&limit=1&offset=1').json()['total']==3
    monkeypatch.delenv('TRADINGVIEW_WEBHOOK_KEY')
    assert client.post(url+'?key=test-secret',content=MESSAGE).status_code==503

def test_invalid_input_and_unknown_messages(setup):
    repo,client=setup;url='/api/webhooks/tradingview?key=test-secret'
    assert client.post(url,content=b'\xff').status_code==422
    assert client.post(url,content=' ').status_code==422
    assert client.post(url,content='x'*65537).status_code==413
    assert client.post(url,content=MESSAGE.replace('TF=30','TF=5')).status_code==422
    assert client.post(url,json={'message':MESSAGE,'bar_start':True}).status_code==422
    assert client.post(url,content='future unknown alert format').json()['parse_status']=='unparsed'
    with repo.session() as s:assert s.scalar(select(func.count()).select_from(TradingViewAlert))==1

@pytest.mark.parametrize(('event','side','name'),[
    ('REALTIME CROSS | AVG SETUP >= 70 | AVG=70.1','LONG','AVG SETUP >= 70'),
    ('REALTIME CROSS | EXECUTION QUALITY >= 65 | EXEC=65.1','SHORT','EXECUTION QUALITY >= 65'),
    ('CONFIRMED CLOSE | SETUP QUALITY BRONZE >= 65%','LONG','BRONZE'),
    ('CONFIRMED CLOSE | SETUP QUALITY STRONG >= 75%','LONG','STRONG'),
    ('PINE READY','SHORT','PINE READY SHORT'),('ARMED','SHORT','SHORT ARMED'),
    ('TP HIT','LONG','LONG TP HIT'),('ACTIVE PLAN EXIT / ABORT','SHORT','ACTIVE PLAN EXIT'),
    ('BOUNCE WATCH | OBSERVATION','SHORT','SHORT BOUNCE WATCH')])
def test_native_dispatch_mapping(event,side,name):
    parsed=parse_body(f'{event} | {side} | BYBIT:BTCUSDT.P | TF=30 | Close=10 | AVG=n/a')
    assert parsed['name']==name and parsed['measurements']=={'price':10}

def test_retention_boundary_does_not_touch_engine_signals(setup):
    repo,client=setup
    for _ in range(3):client.post('/api/webhooks/tradingview?key=test-secret',content=MESSAGE)
    now=now_ms();cutoff=now-RETENTION_MS
    with repo.session.begin() as session:
        rows=session.scalars(select(TradingViewAlert).order_by(TradingViewAlert.id)).all()
        rows[0].received_at=cutoff-1;rows[1].received_at=cutoff;rows[2].received_at=now
        session.add(Signal(dedupe_key='old-local',symbol='ETHFIUSDT',timeframe='30',event_time=cutoff-1,name='LONG WATCH ENTRY',parameter_set_id=repo.parameters()['id'],payload={}))
    assert cleanup(repo,now)==1
    assert cleanup(repo,now)==0
    with repo.session() as session:
        assert session.scalar(select(func.count()).select_from(TradingViewAlert))==2
        assert session.scalar(select(func.count()).select_from(Signal))==1

def test_parity_never_promotes_delivery_time_candidate_to_pass(setup):
    repo,client=setup;now=now_ms();bar=now//1800000*1800000
    with repo.session.begin() as session:
        for exchange in ('BYBIT','BINANCE'):
            session.add(Signal(dedupe_key=exchange,symbol='ETHFIUSDT',timeframe='30',event_time=now,name='LONG WATCH ENTRY',parameter_set_id=repo.parameters()['id'],payload={'exchange':exchange,'direction':'LONG','bar_start':bar,'avg_setup':71.24}))
    url='/api/webhooks/tradingview?key=test-secret'
    client.post(url,content=MESSAGE)
    client.post(url,json={'message':MESSAGE,'bar_start':bar})
    client.post(url,json={'message':MESSAGE,'bar_start':bar-1800000})
    report=client.get('/api/parity/tradingview').json()
    assert report['status']=='UNVERIFIED'
    assert report['counts']=={'TIME_CANDIDATE':1,'SIGNAL_MATCH':1,'SIGNAL_NOT_FOUND':1}
    assert len(report['items'][1]['candidates'])==1
    assert report['items'][1]['candidates'][0]['measurement_deltas']['avg_setup']==pytest.approx(.04)

def test_access_logs_remove_key():
    record=logging.LogRecord('uvicorn.access',20,'',0,'%s - "%s %s HTTP/%s" %d',('client','POST','/api/webhooks/tradingview?key=secret','1.1',201),None)
    assert WebhookAccessFilter().filter(record)
    assert 'secret' not in record.getMessage()

def test_rule_file_roundtrip_and_atomic_validation(setup):
    repo,client=setup
    rule={'name':'Тест','enabled':False,'conditions':{'field':'avg_setup','op':'>=','value':70},'template':'{symbol}'}
    assert client.post('/api/alerts/rules',json=rule).status_code==201
    exported=client.get('/api/alerts/rules/export').json()
    assert exported['format']=='brokeweb-alert-rules' and 'id' not in exported['rules'][0]
    assert client.post('/api/alerts/rules/import',json=exported).status_code==201
    items=client.get('/api/alerts/rules').json()['items']
    assert len(items)==2 and items[0]['id']!=items[1]['id']
    assert all(not item['enabled'] for item in items)
    broken=json.loads(json.dumps(exported));broken['rules'].append({**rule,'conditions':{'field':'avg_setup','op':'evil','value':2}})
    assert client.post('/api/alerts/rules/import',json=broken).status_code==422
    assert client.post('/api/alerts/rules/import',json={**exported,'version':2}).status_code==422
    with repo.session() as session:
        assert session.scalar(select(func.count()).select_from(Rule))==2
        assert session.scalar(select(func.count()).select_from(RuleVersion))==2
