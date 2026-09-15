import json
import pytest
from tools.pine_reference.replay import replay_fixture,write_replay,FixtureError
from backend.engine.runtime import PINE_HASH

def fixture():
    start=1700000100000
    return {'symbol':'TESTUSDT','timeframe':'15','tick_size':.01,'bars':[{'start':start+i*900000,'end':start+(i+1)*900000,'open':100.,'high':101.,'low':99.,'close':100.,'volume':100.} for i in range(3)],'contexts':{}}

def test_replay_outputs_source_codes_without_claiming_parity(tmp_path):
    data=fixture();data['bars'][0]['start']=1699999200000
    # Use correctly aligned chart boundaries for a deterministic offline fixture.
    for i,bar in enumerate(data['bars']):bar.update(start=1699999200000+i*900000,end=1699999200000+(i+1)*900000)
    path=tmp_path/'observations.jsonl';assert write_replay(data,path)==3
    rows=[json.loads(line) for line in path.read_text().splitlines()]
    assert all(row['pine_source_hash']==PINE_HASH and row['parity_status']=='UNVERIFIED' for row in rows)
    assert rows[-1]['PARITY_direction']==0
    assert rows[-1]['PARITY_action'] is not None
    assert rows[-1]['data_health']=='RECOVERING'

def test_replay_rejects_duplicate_closed_bar_and_internal_gap():
    data=fixture()
    for i,bar in enumerate(data['bars']):bar.update(start=1699999200000+i*900000,end=1699999200000+(i+1)*900000)
    data['bars'][1]=dict(data['bars'][0])
    with pytest.raises(FixtureError,match='committed'):list(replay_fixture(data))
    data['bars'].pop(1)
    with pytest.raises(FixtureError,match='gap'):list(replay_fixture(data))

def test_realtime_fixture_requires_observation_time():
    data=fixture();data['bars']=data['bars'][:1];data['bars'][0].update(start=1699999200000,end=1700000100000,confirmed=False)
    with pytest.raises(FixtureError,match='event_time'):list(replay_fixture(data))


def test_historical_rest_download_times_do_not_reorder_market_history():
    data=fixture()
    for i,bar in enumerate(data['bars']):
        bar.update(start=1699999200000+i*900000,end=1699999200000+(i+1)*900000,
                   received_at=1800000000000-i*1000)
    rows=list(replay_fixture(data))
    assert [r['replay_event_time'] for r in rows]==[b['end'] for b in data['bars']]
