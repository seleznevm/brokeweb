import json
import logging
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select,func
from backend.api.main import create_app,WebhookAccessFilter
from backend.models.repository import Repository,now_ms
from backend.models.schema import TradingViewAlert,Signal,Rule,RuleVersion,CampaignOrder,LevelCampaign,Delivery
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


def structured_message(**patch):
    return {'schema':'scalping_sma.alert.v1','script':'1.18.8','alert':'RT | ARMED',
            'event_id':'BYBIT:HBARUSDT.P_5_1790728500000_ARMED_SHORT',
            'event':'ARMED','mode':'REALTIME','side':'SHORT','symbol':'BYBIT:HBARUSDT.P',
            'tf':'5','close':0.10134,'avg':53.5246978099,'formation':75.52,
            'execution':60.98,'geometry':58.71,'context':51.67,'exhaustion':7.72,
            'mae':0,'sl':0.10295,'t1':0.09985,**patch}


def test_versioned_pine_json_keeps_precision_and_source_bar(setup):
    repo,client=setup
    response=client.post('/api/webhooks/tradingview?key=test-secret',json=structured_message())
    assert response.status_code==201 and response.json()['parse_status']=='parsed'
    row=client.get('/api/tradingview/alerts').json()['items'][0]
    assert (row['symbol'],row['exchange'],row['timeframe'],row['name'])==('HBARUSDT','BYBIT','5','SHORT ARMED')
    assert row['bar_start']==1790728500000 and row['time_basis']=='source_event_id'
    assert row['measurements']['avg_setup']==53.5246978099
    assert row['measurements']['mae']==0


@pytest.mark.parametrize('side',['SUPPORT','RESISTANCE'])
def test_zone_cross_is_parsed_without_inventing_trade_direction(side):
    row=parse_body(json.dumps(structured_message(event='ZONE CROSS',side=side,avg=None,sl=None,t1=None)))
    assert row['name']=='ZONE CROSS' and row['side']==side and row['direction'] is None
    assert 'avg_setup' not in row['measurements'] and 'sl' not in row['measurements']
    # An ID belonging to ARMED SHORT cannot supply time for a different event.
    assert 'bar_start' not in row


def test_incomplete_campaign_reference_never_executes_or_sends(setup):
    repo,client=setup
    response=client.post('/api/webhooks/tradingview?key=test-secret',json=structured_message(
        event='CAMPAIGN_ENTRY_1',side='LONG',event_id='BYBIT:HBARUSDT.P_5_1790728500000_CAMPAIGN_ENTRY_1_LONG'))
    assert response.status_code==201
    assert 'execution' not in response.json()
    with repo.session() as s:
        for model in (CampaignOrder,LevelCampaign,Delivery):
            assert s.scalar(select(func.count()).select_from(model))==0


@pytest.mark.parametrize('patch',[{'symbol':'HBARUSDT'}, {'tf':'1'}, {'side':'NONE'}, {'event':None}, {'mode':'invalid'}])
def test_invalid_versioned_pine_json_is_rejected(setup,patch):
    _,client=setup
    assert client.post('/api/webhooks/tradingview?key=test-secret',json=structured_message(**patch)).status_code==422


def test_reparse_repairs_metadata_only_and_is_idempotent(setup):
    from tools.reparse_tradingview import reparse_alerts
    repo,client=setup
    raw=json.dumps(structured_message(event='CAMPAIGN_ENTRY_1'))
    with repo.session.begin() as s:
        s.add(TradingViewAlert(received_at=now_ms(),raw_body=raw,body_sha256='original-hash',
            payload={'parse_status':'unparsed','time_basis':'received_at_only','measurements':{}}))
    assert reparse_alerts(repo)=={'examined':1,'reparsed':1}
    assert client.get('/api/tradingview/alerts').json()['items'][0]['parse_status']=='unparsed'
    assert reparse_alerts(repo,apply=True)=={'examined':1,'reparsed':1}
    assert reparse_alerts(repo,apply=True)=={}
    row=client.get('/api/tradingview/alerts').json()['items'][0]
    assert row['raw_body']==raw and row['body_sha256']=='original-hash'
    assert row['name']=='CAMPAIGN_ENTRY_1'
    with repo.session() as s:
        assert s.scalar(select(func.count()).select_from(CampaignOrder))==0


def test_native_realtime_prefix_preserves_signal_mapping():
    assert parse_body('REALTIME | '+MESSAGE)['name']=='LONG WATCH ENTRY'


def test_structured_execution_70_does_not_match_old_threshold_65():
    row=parse_body(json.dumps(structured_message(event='EXECUTION_70')))
    assert row['name']=='EXECUTION_70'


def test_older_structured_confirmed_mode_is_supported():
    row=parse_body(json.dumps(structured_message(mode='CONFIRMED',script='1.18.1')))
    assert row['parse_status']=='parsed' and row['mode']=='BAR_CLOSE'
    assert row['source_mode']=='CONFIRMED'


def test_open_without_timeframe_has_explicit_reason():
    row=parse_body('OPEN LONG | BYBIT:AVAXUSDT.P | C=11.390 | SL=11.350 | TP=11.457 | RR=1.68')
    assert row['parse_status']=='unparsed'
    assert 'Missing timeframe' in row['parse_reason']
    assert 'timeframe' not in row

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
