from types import SimpleNamespace
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, func
from backend.models.repository import Repository
from backend.api.main import create_app
from backend.models.schema import Signal
from backend.statistics.models import Evaluation
from backend.statistics.outcomes import evaluate, HORIZON, POLICY
from backend.statistics.capture import candidates, capture
from backend.statistics.worker import save, observe

def bar(start=0, high=101, low=99.5):
    return dict(start=start, end=start+60000, open=100, close=100, high=high, low=low, confirmed=True)

def plan(side='LONG', start=0):
    return dict(entry=100, sl=99 if side=='LONG' else 101, direction=side, event_time=start)

@pytest.mark.parametrize('side,high,low,status', [('LONG',102,99.5,'WIN'), ('LONG',101,99,'LOSS'), ('SHORT',100.5,98,'WIN'), ('SHORT',101,99,'LOSS'), ('LONG',102,99,'AMBIGUOUS')])
def test_directional_first_hit(side, high, low, status):
    assert evaluate(plan(side), [bar(high=high, low=low)], 60000)['status'] == status

def test_boundary_before_signal_never_win():
    assert evaluate(plan(start=30000), [bar(high=103)], 60000)['status'] == 'AMBIGUOUS'

def test_gap_before_win():
    assert evaluate(plan(), [bar(60000, high=103)], 120000)['status'] == 'DATA_GAP'

def test_win_remains_win_before_later_loss():
    assert evaluate(plan(), [bar(high=102), bar(60000, low=98)], 120000)['status'] == 'WIN'

def test_expiry_requires_full_coverage():
    bars = [bar(t) for t in range(0, HORIZON, 60000)]
    assert evaluate(plan(), bars, HORIZON)['status'] == 'EXPIRED'
    assert evaluate(plan(), bars[:-1], HORIZON)['status'] == 'DATA_GAP'

def test_incomplete_minute_not_used():
    b = bar(high=103); b['confirmed'] = False
    assert evaluate(plan(), [b], 30000)['status'] == 'OPEN'

def test_invalid_plan():
    assert evaluate({**plan(), 'sl': 100}, [], 0)['status'] == 'INVALID'

def test_partial_last_minute_touch_ambiguous():
    bars = [bar(t) for t in range(0, HORIZON+60000, 60000)]
    bars[-1]['high'] = 103
    assert evaluate(plan(start=30000), bars, HORIZON+60000)['status'] == 'AMBIGUOUS'

def broke_signal(name='LONG WATCH ENTRY', **extra):
    p = dict(strategy='BROKE_SETUPS', direction='LONG', entry=100, price=100, sl=99, tp1=102, event_time=60000, setup_generation_id='g', parameter_hash='v', **extra)
    return SimpleNamespace(id=1, exchange='BYBIT', symbol='TESTUSDT', timeframe='30', received_at=61000, event_time=60000, name=name, parameter_set_id='v', dedupe_key='key', payload=p)

def test_broke_candidates():
    sig = candidates(broke_signal())
    assert len(sig) == 1
    assert sig[0].family == 'WE'
    assert sig[0].strategy == 'BROKE'
    assert sig[0].status == 'PENDING'

@pytest.fixture
def repo(tmp_path):
    r = Repository('sqlite:///' + str(tmp_path / 'stats.db'))
    r.initialize()
    return r

def test_capture_restart_and_immutable_outcome(repo):
    with repo.session.begin() as s:
        s.add(Signal(dedupe_key='one', symbol='TESTUSDT', timeframe='30', event_time=60000, name='LONG WATCH ENTRY', parameter_set_id='v', payload=broke_signal().payload))
    assert capture(repo)['broke'] == 1
    assert capture(repo)['broke'] == 0
    with repo.session() as s: row = s.scalar(select(Evaluation))
    save(repo, row.id, {'status': 'WIN'}, 120000)
    save(repo, row.id, {'status': 'LOSS'}, 180000)
    with repo.session() as s: assert s.get(Evaluation, row.id).status == 'WIN'

def test_api_aggregation_and_filters(repo):
    with repo.session.begin() as s:
        for idx, fam in enumerate(['LONG WATCH ENTRY', 'PINE READY LONG']):
            r = broke_signal(fam)
            r.id = idx + 1
            for item in candidates(r):
                item.status = 'WIN' if idx == 0 else 'LOSS'
                s.add(item)
    client = TestClient(create_app(repo))
    url = '/api/statistics?strategy=BROKE&start=0&end=200000&limit=1'
    data = client.get(url).json()
    assert data['total'] == 2 and len(data['items']) == 1 and data['winrate'] == 50
    assert len(data['breakdowns']) == 2
    assert client.get(url + '&source=engine').json()['winrate'] == 50
    assert client.get(url + '&start=200000').status_code == 422
    exported = client.get(url + '&export=csv').text
    assert len(exported.splitlines()) == 3
    assert client.get(url + '&mode=replay').json()['winrate'] is None

@pytest.mark.asyncio
async def test_observer_fetches_pending_symbol_without_universe(repo):
    class Adapter:
        calls = 0
        async def backfill(self, *args, **kw):
            from backend.marketdata.models import Bar
            self.calls += 1
            return [Bar('BYBIT', 'TESTUSDT', '1', 60000, 120000, 100, 102, 99.5, 100, 1, confirmed=True)]
    adapter = Adapter()
    row = candidates(broke_signal())[0]
    assert (await observe(repo, row, {'BYBIT': adapter}, 120000))['status'] == 'WIN'
    assert adapter.calls == 1
    assert (await observe(repo, row, {'BYBIT': adapter}, 120000))['status'] == 'WIN'
    assert adapter.calls == 1
