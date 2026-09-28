import asyncio
import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from backend.api.main import create_app
from backend.alerts.notifier import deliver_one
from backend.models.repository import Repository,now_ms
from backend.models.schema import Delivery,Snapshot,Signal,RuleVersion
from backend.models.schema import Current

@pytest.fixture
def repo(tmp_path):
    value=Repository(f'sqlite:///{tmp_path}/test.db'); value.initialize(); return value

@pytest.fixture
def client(repo):
    with TestClient(create_app(repo)) as client: yield client

def snapshot(repo,**kwargs):
    return {'exchange':'BYBIT','symbol':'XYZUSDT','timeframe':'15','event_time':1700000000000,'bar_start':1699999200000,'confirmed':False,'setup_generation_id':'g1','action':'WATCH','fsm':'WATCH','direction':'LONG','price':100,'avg_setup':72,'formation':70,'signals':[],'metrics':{'raw':17},'parameter_set_id':repo.parameters()['id'],'data_health':'HEALTHY',**kwargs}


def test_compressed_checkpoint_exact_restore_and_legacy_upgrade(repo):
    import json
    state={'history':[{'x':.12345678901234567,'na':None,'b':True,'s':'Зона'}]*500,
           'varip':{'samples':17,'sum':.1+.2},'schema_version':1}
    repo.save_snapshot(snapshot(repo),state)
    with repo.session() as session:
        row=session.get(Current,('BYBIT','XYZUSDT','15'))
        assert row.checkpoint is None
        assert len(row.checkpoint_blob)<len(json.dumps(state).encode())/4
    restored=Repository(str(repo.engine.url))
    assert restored.load_checkpoint('BYBIT','XYZUSDT','15')==state
    repo.save_snapshot(snapshot(repo,price=101))
    assert repo.load_checkpoint('BYBIT','XYZUSDT','15')==state
    # Simulate a checkpoint written before migration 0003.
    with repo.session.begin() as session:
        row=session.get(Current,('BYBIT','XYZUSDT','15'));row.checkpoint_blob=None;row.checkpoint=state
    assert repo.load_checkpoint('BYBIT','XYZUSDT','15')==state
    repo.save_snapshot(snapshot(repo),state)
    with repo.session() as session:
        row=session.get(Current,('BYBIT','XYZUSDT','15'))
        assert row.checkpoint is None and row.checkpoint_blob is not None


def test_save_snapshot_uses_supplied_parameter_version_without_query(repo,monkeypatch):
    state=snapshot(repo)
    def unexpected_query():raise AssertionError('Known parameter version must not be fetched again')
    monkeypatch.setattr(repo,'parameters',unexpected_query)
    repo.save_snapshot(state,{'varip':{'samples':17}})
    assert repo.load_checkpoint('BYBIT','XYZUSDT','15')=={'varip':{'samples':17}}


def test_prepared_checkpoint_bytes_survive_save_rollback_and_subsequent_writes(repo,monkeypatch):
    from backend.models.checkpoints import PackedCheckpoint,pack_checkpoint
    state={'varip':{'sum':.1+.2,'count':17},'history':[None,-0.0,2**60,'Зона']}
    prepared=PackedCheckpoint.from_state(state)
    repo.save_snapshot(snapshot(repo),prepared)
    with repo.session() as session:
        assert session.get(Current,('BYBIT','XYZUSDT','15')).checkpoint_blob==pack_checkpoint(state)
    assert repo.load_checkpoint('BYBIT','XYZUSDT','15')==state
    def fail(*args):raise RuntimeError('rollback test')
    with monkeypatch.context() as patch:
        patch.setattr(repo,'_save_research',fail)
        with pytest.raises(RuntimeError,match='rollback test'):
            repo.save_snapshot(snapshot(repo,price=999),PackedCheckpoint.from_state({'next':1}))
    with repo.session() as session:
        row=session.get(Current,('BYBIT','XYZUSDT','15'))
        assert row.payload['price']==100 and row.checkpoint_blob==prepared.blob
    repo.save_snapshot(snapshot(repo,price=101))
    assert repo.load_checkpoint('BYBIT','XYZUSDT','15')==state
    # Callers providing ordinary dictionaries keep the old normalization path.
    repo.save_snapshot(snapshot(repo),{True:float('inf')})
    assert repo.load_checkpoint('BYBIT','XYZUSDT','15')=={'True':None}


def test_metrics_exposes_checkpoint_cost_throughput_and_market_lag(repo,client):
    repo.heartbeat('engine',{'status':'RECOVERING','checkpoint_export_latency_ms':123.5,'checkpoint_pack_latency_ms':42.5,
        'market_data_lag_ms':95000,'calculations_per_second':2.5})
    response=client.get('/metrics')
    assert response.status_code==200
    for metric,value in [('checkpoint_export_latency_ms',123.5),('checkpoint_pack_latency_ms',42.5),('market_data_lag_ms',95000),('calculations_per_second',2.5)]:
        assert f'scalping_{metric}{{service="engine"}} {value}' in response.text


def test_intrabar_report_summary_does_not_replace_historical_parity(client,monkeypatch,tmp_path):
    import json
    intrabar=tmp_path/'intrabar.json';historical=tmp_path/'historical.json'
    historical.write_text(json.dumps({'status':'UNVERIFIED','observed_status':'PASS','kind':'historical'}))
    monkeypatch.setenv('PARITY_REPORT_PATH',str(historical));monkeypatch.setenv('INTRABAR_REPORT_PATH',str(intrabar))
    assert client.get('/api/parity/intrabar').json()['sessions']==0
    value={'status':'CAPTURE_IMPORTED','full_intrabar_status':'UNVERIFIED','sessions':2,
           'items':[{'symbol':'X'},{'symbol':'Y'}],'ta_diagnostic_status_counts':{'DIAGNOSTIC_MATCH':2}}
    intrabar.write_text(json.dumps(value))
    summary=client.get('/api/parity/intrabar').json()
    assert summary=={k:v for k,v in value.items() if k!='items'}
    assert client.get('/api/parity/intrabar?include_sessions=true').json()==value
    assert client.get('/api/parity').json()['kind']=='historical'


def test_corrupt_checkpoint_is_rejected_instead_of_silently_reset(repo):
    import zlib
    repo.save_snapshot(snapshot(repo),{'state':123})
    with repo.session.begin() as session:
        row=session.get(Current,('BYBIT','XYZUSDT','15'));row.checkpoint_blob=b'BWC1broken'
    with pytest.raises(zlib.error):repo.load_checkpoint('BYBIT','XYZUSDT','15')

def rule(client,**kwargs):
    data={'name':'Strong setup','conditions':{'field':'avg_setup','op':'>=','value':70},'mode':'realtime','frequency':'once_per_bar','cooldown_seconds':0,**kwargs}
    response=client.post('/api/alerts/rules',json=data); assert response.status_code==201,response.text
    return response.json()

def test_api_persistence_recovery_and_dedupe(repo,client,monkeypatch,tmp_path):
    monkeypatch.setenv("PARITY_REPORT_PATH",str(tmp_path/"missing-reference.json"))
    row=rule(client)
    repo.save_snapshot(snapshot(repo,signals=['BRONZE']),{'zones':[1],'varip':{'crossed':True}})
    repo.save_snapshot(snapshot(repo,signals=['BRONZE']))
    assert client.get('/api/setups').json()['total']==1
    assert client.get('/api/setups?avg_setup_min=80').json()['total']==0
    assert client.get('/api/setups/XYZUSDT/15').json()['metrics']=={'raw':17}
    assert len(client.get('/api/signals').json()['items'])==1
    assert len(client.get('/api/alerts/deliveries').json()['items'])==1
    assert client.post('/api/alerts/rules/test',json={'conditions':row['conditions']}).json()['total']==1
    restarted=Repository(str(repo.engine.url)); restarted.initialize()
    assert restarted.load_checkpoint('BYBIT','XYZUSDT','15')['zones']==[1]
    restarted.save_snapshot(snapshot(repo,signals=['BRONZE']))
    assert len(client.get('/api/alerts/deliveries').json()['items'])==1
    assert client.get('/api/setups/UNKNOWN/15').status_code==404
    assert client.get('/api/parity').json()['status']=='UNVERIFIED'

def test_rule_versions_parameters_validation(repo,client):
    first=repo.parameters(); new=client.put('/api/parameters',json={'values':{'manualSetupExpiryMinutes':120}})
    assert new.status_code==200 and new.json()['id']!=first['id']
    assert client.put('/api/parameters',json={'values':{'manualSetupExpiryMinutes':-1}}).status_code==422
    assert client.put('/api/parameters',json={'values':{'unknown':1}}).status_code==422
    row=rule(client); body={k:v for k,v in row.items() if k not in ('id','version')}; body['enabled']=False
    assert client.put('/api/alerts/rules/'+row['id'],json=body).json()['version']==2
    assert client.delete('/api/alerts/rules/'+row['id']).status_code==200
    with repo.session() as s: assert len(s.scalars(select(RuleVersion)).all())==3
    assert client.put('/api/settings',json={'snapshot_interval_sec':5}).json()['snapshot_interval_sec']==5


def test_alert_field_schema_and_multiselect_roundtrip(client):
    response=client.get('/api/alerts/fields')
    assert response.status_code==200
    fields={f['key']:f for f in response.json()['fields']}
    assert fields['direction']['options']==['LONG','SHORT','NONE']
    conditions={'op':'AND','conditions':[
        {'field':'timeframe','op':'IN','value':['30','5']},
        {'field':'signals','op':'contains_any','value':['BRONZE','STRONG']},
        {'field':'confirmed','op':'==','value':True}]}
    saved=rule(client,enabled=False,conditions=conditions)
    assert saved['conditions']==conditions
    assert client.post('/api/alerts/rules/test',json={'conditions':conditions}).status_code==200
    invalid={'field':'timeframe','op':'IN','value':[30,5]}
    assert client.post('/api/alerts/rules/test',json={'conditions':invalid}).status_code==422
    assert client.post('/api/alerts/rules',json={'name':'Bad type','conditions':invalid}).status_code==422

def test_confirmed_generation_cooldown_and_stale(repo,client):
    rule(client,mode='confirmed',frequency='once_per_generation',cooldown_seconds=100)
    repo.save_snapshot(snapshot(repo)); assert client.get('/api/alerts/deliveries').json()['items']==[]
    repo.save_snapshot(snapshot(repo,confirmed=True,data_health='STALE')); assert client.get('/api/alerts/deliveries').json()['items']==[]
    repo.save_snapshot(snapshot(repo,confirmed=True))
    repo.save_snapshot(snapshot(repo,confirmed=True,bar_start=1700000100000))
    repo.save_snapshot(snapshot(repo,confirmed=True,setup_generation_id='g2'))
    assert len(client.get('/api/alerts/deliveries').json()['items'])==1

def test_first_occurrence_and_cross_restart(repo,client):
    rule(client,frequency='first_occurrence',conditions={'field':'avg_setup','op':'crosses_above','value':70})
    repo.save_snapshot(snapshot(repo,avg_setup=60))
    repo=Repository(str(repo.engine.url)); repo.initialize()
    repo.save_snapshot(snapshot(repo,avg_setup=71))
    repo.save_snapshot(snapshot(repo,avg_setup=72))
    assert len(client.get('/api/alerts/deliveries').json()['items'])==1

def test_market_bars_and_frozen_research(repo,client):
    bar={'exchange':'BYBIT','symbol':'XYZUSDT','timeframe':'15','start':100,'end':200,'open':1.,'high':2.,'low':1.,'close':2.,'volume':4.,'confirmed':True}
    repo.save_bar(bar); repo.save_bar({**bar,'confirmed':False,'close':1.5})
    assert client.get('/api/setups/XYZUSDT/15/bars').json()['items'][0]['close']==2
    sample={'id':'s1','family':'BRONZE','entry':100,'mfe':1.4,'first_hit':'SL','completed':True}
    repo.save_snapshot(snapshot(repo,research={'samples':[sample]}))
    saved=client.get('/api/research').json()['items'][0]
    assert saved['first_hit']=='SL' and saved['mfe']==1.4

def test_telegram_mocked_transport_and_uncertain(repo,client,monkeypatch):
    # Eligibility is a time boundary, not a test of the host's wall clock.
    # WSL clock corrections can otherwise move now behind next_attempt.
    clock=now_ms()
    monkeypatch.setattr('backend.models.repository.now_ms',lambda:clock)
    monkeypatch.setattr('backend.alerts.notifier.now_ms',lambda:clock)
    rule(client); repo.save_snapshot(snapshot(repo))
    requests=[]
    async def send():
        def handler(request): requests.append(request); return httpx.Response(200,json={'ok':True,'result':{'message_id':1}})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as transport:
            assert await deliver_one(repo,transport,'TEST_TOKEN','TEST_CHAT')
            assert not await deliver_one(repo,transport,'TEST_TOKEN','TEST_CHAT')
    asyncio.run(send()); assert len(requests)==1
    assert client.get('/api/alerts/deliveries').json()['items'][0]['status']=='sent'
    clock+=1000
    repo.save_snapshot(snapshot(repo,bar_start=1700000900000))
    async def timeout():
        def handler(request): raise httpx.ReadTimeout('ambiguous',request=request)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as transport: await deliver_one(repo,transport,'TEST_TOKEN','TEST_CHAT')
    asyncio.run(timeout())
    assert client.get('/api/alerts/deliveries').json()['items'][0]['status']=='uncertain'

def test_no_send_when_disabled_after_enqueue(repo,client):
    row=rule(client); repo.save_snapshot(snapshot(repo))
    client.delete('/api/alerts/rules/'+row['id'])
    async def run():
        def handler(request): pytest.fail('Must not contact Telegram for deleted rule')
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as transport: await deliver_one(repo,transport,'TEST','TEST')
    asyncio.run(run())
    assert client.get('/api/alerts/deliveries').json()['items'][0]['status']=='suppressed'

def test_confirmed_crossing_uses_previous_close_not_intrabar(repo,client):
    rule(client,mode='confirmed',conditions={'field':'avg_setup','op':'crosses_above','value':70})
    repo.save_snapshot(snapshot(repo,confirmed=True,avg_setup=65))
    repo.save_snapshot(snapshot(repo,confirmed=False,avg_setup=75,bar_start=1700000100000))
    repo.save_snapshot(snapshot(repo,confirmed=True,avg_setup=76,bar_start=1700000100000))
    assert len(client.get('/api/alerts/deliveries').json()['items'])==1

def test_research_identity_survives_generation_and_preserves_frozen_metrics(repo,client):
    sample={'__pine_record__':'ResearchSignal','fields':{'signalBar':42,'bucket':17,'direction':1,'entry':100,'atr0':2,'mfe':0,'mae':0}}
    repo.save_snapshot(snapshot(repo,formation=77,research={'samples':[sample]}))
    updated={'__pine_record__':'ResearchSignal','fields':{**sample['fields'],'mfe':4,'t1Bar':43}}
    repo.save_snapshot(snapshot(repo,formation=11,setup_generation_id='g2',research={'samples':[],'completed':[updated]}))
    records=client.get('/api/research').json()['items']
    assert len(records)==1 and records[0]['completed']
    assert records[0]['frozen_snapshot']['formation']==77
    assert records[0]['raw']==updated

def test_cooldown_persists_and_repeat_releases_after_time(repo,client,monkeypatch):
    clock=now_ms(); monkeypatch.setattr('backend.models.repository.now_ms',lambda:clock)
    rule(client,frequency='repeat',cooldown_seconds=10)
    repo.save_snapshot(snapshot(repo))
    repo=Repository(str(repo.engine.url)); repo.initialize()
    clock+=9000; repo.save_snapshot(snapshot(repo))
    assert len(client.get('/api/alerts/deliveries').json()['items'])==1
    clock+=1001; repo.save_snapshot(snapshot(repo))
    assert len(client.get('/api/alerts/deliveries').json()['items'])==2

def test_replay_never_enqueues_even_if_health_was_marked_healthy(repo,client):
    rule(client)
    repo.save_snapshot(snapshot(repo,replay=True))
    assert client.get('/api/alerts/deliveries').json()['items']==[]

def test_bars_overlay_current_open_bar_and_events_obey_time_range(repo,client):
    live={'exchange':'BYBIT','symbol':'XYZUSDT','timeframe':'15','start':100,'end':200,'open':1.,'high':3.,'low':1.,'close':2.,'volume':4.,'confirmed':False}
    repo.save_bar({**live,'high':2.,'close':1.5})
    repo.save_snapshot(snapshot(repo,bar=live,event_time=150))
    assert client.get('/api/setups/XYZUSDT/15/bars').json()['items'][0]['close']==2.
    assert client.get('/api/setups/XYZUSDT/15/events?since=151').json()['items']==[]

def test_batch_reconcile_replaces_confirmed_corrections_but_never_with_open_data(repo,client):
    bar={'exchange':'BYBIT','symbol':'XYZUSDT','timeframe':'15','start':100,'end':200,'open':1.,'high':2.,'low':1.,'close':2.,'volume':4.,'confirmed':True,'turnover':6.}
    repo.save_bars([bar])
    repo.save_bars([{**bar,'confirmed':False,'close':1.5}])
    assert client.get('/api/setups/XYZUSDT/15/bars').json()['items'][0]['close']==2.
    repo.save_bars([{**bar,'high':3.,'close':2.5}])
    assert client.get('/api/setups/XYZUSDT/15/bars').json()['items'][0]['close']==2.5

def test_intrabar_latch_does_not_duplicate_event_snapshots(repo,client):
    repo.save_snapshot(snapshot(repo,signals=['AVG SETUP >= 70']))
    repo.save_snapshot(snapshot(repo,signals=['AVG SETUP >= 70']))
    with repo.session() as session:
        assert len(session.scalars(select(Snapshot)).all())==1
        assert len(session.scalars(select(Signal)).all())==1


def test_parity_prefers_full_reference_when_present(client,monkeypatch,tmp_path):
    import json
    monkeypatch.delenv('PARITY_REPORT_PATH',raising=False)
    monkeypatch.chdir(tmp_path)
    reports=tmp_path/'reports';reports.mkdir()
    (reports/'parity.json').write_text(json.dumps({'status':'UNVERIFIED','observed_status':'PASS'}))
    assert client.get('/api/parity').json()['status']=='UNVERIFIED'
    (reports/'full-parity.json').write_text(json.dumps({'status':'FAIL','observed_status':'FAIL'}))
    assert client.get('/api/parity').json()['status']=='FAIL'
    (reports/'context-parity.json').write_text(json.dumps({'status':'UNVERIFIED','scope':'Historical metrics; signal reference absent'}))
    assert client.get('/api/parity').json()['scope']=='Historical metrics; signal reference absent'
    monkeypatch.setenv('PARITY_REPORT_PATH',str(reports/'full-parity.json'))
    assert client.get('/api/parity').json()['status']=='FAIL'


def test_display_timezone_defaults_persists_and_preserves_other_settings(repo,client):
    assert client.get('/api/settings').json()['timezone_offset_minutes']==420
    # Existing deployments only stored the snapshot interval.
    from backend.models.schema import ServiceHealth
    with repo.session.begin() as session:
        session.add(ServiceHealth(name='settings',updated_at=now_ms(),payload={'snapshot_interval_sec':9}))
    assert client.get('/api/settings').json()=={'snapshot_interval_sec':9,'timezone_offset_minutes':420,'universe_min_turnover24h_usdt':10000000}
    saved=client.put('/api/settings',json={'timezone_offset_minutes':345})
    assert saved.status_code==200
    assert saved.json()=={'snapshot_interval_sec':9,'timezone_offset_minutes':345,'universe_min_turnover24h_usdt':10000000}
    assert client.put('/api/settings',json={'snapshot_interval_sec':5}).json()=={'snapshot_interval_sec':5,'timezone_offset_minutes':345,'universe_min_turnover24h_usdt':10000000}
    assert Repository(str(repo.engine.url)).settings()['timezone_offset_minutes']==345
    for invalid in (-721,841,421,True,'420',None):
        assert client.put('/api/settings',json={'timezone_offset_minutes':invalid}).status_code==422
    assert client.get('/api/settings').json()['timezone_offset_minutes']==345
    # Display-only changes must not create a parameter version or shift raw data.
    before=repo.parameters()['id']
    original=snapshot(repo)
    repo.save_snapshot(original)
    assert client.put('/api/settings',json={'timezone_offset_minutes':-720}).status_code==200
    assert repo.parameters()['id']==before
    assert client.get('/api/setups/XYZUSDT/15').json()['event_time']==original['event_time']


def test_universe_turnover_setting_and_exclusion_preserve_checkpoint(repo,client):
    assert client.get('/api/settings').json()['universe_min_turnover24h_usdt']==10_000_000
    for value in (-1,True,'10000000',None):
        assert client.put('/api/settings',json={'universe_min_turnover24h_usdt':value}).status_code==422
    assert client.put('/api/settings',json={'universe_min_turnover24h_usdt':25_000_000}).status_code==200
    assert repo.settings()['universe_min_turnover24h_usdt']==25_000_000
    repo.save_snapshot(snapshot(repo),{'state':[1,2,3]})
    repo.exclude_from_universe(['XYZUSDT'])
    with repo.session() as s:
        row=s.get(Current,('BYBIT','XYZUSDT','15'))
        assert row.payload['universe_excluded'] and row.payload['action']=='WAIT SETUP'
        assert row.payload['data_health']=='STALE' and row.payload['signals']==[]
        assert repo.read_checkpoint(row)=={'state':[1,2,3]}
    assert client.get('/api/setups').json()['total']==0
    assert client.put('/api/settings',json={'universe_min_turnover24h_usdt':0}).status_code==200
