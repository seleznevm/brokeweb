from types import SimpleNamespace
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, func
from backend.models.repository import Repository
from backend.api.main import create_app
from backend.models.schema import Signal
from backend.statistics.models import Evaluation
from backend.statistics.outcomes import evaluate, HORIZON, POLICY, POLICIES
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


@pytest.mark.parametrize('policy', list(POLICIES))
@pytest.mark.parametrize('timeframe', ['5', '30', '1', '15', '60'])
@pytest.mark.parametrize('name', ['LONG WATCH ENTRY', 'SHORT WATCH ENTRY', 'PINE READY LONG',
                                'PINE READY SHORT', 'BRONZE', 'STRONG', 'MATURED PRE-BREAK ENTRY',
                                'LATE WATCH ENTRY LONG', 'LATE WATCH ENTRY SHORT'])
def test_only_we_and_ready_on_five_and_thirty_minutes(policy, timeframe, name):
    signal = broke_signal(name)
    signal.timeframe = timeframe
    accepted = name in ('LONG WATCH ENTRY', 'SHORT WATCH ENTRY', 'PINE READY LONG', 'PINE READY SHORT') and timeframe in ('5', '30')
    assert bool(candidates(signal, policy)) is accepted


@pytest.mark.parametrize('patch', [{'strategy': 'BROKE_PB'}, {'signal_source': 'tradingview'}])
def test_other_strategy_and_webhook_never_open_setup_trade(patch):
    signal = broke_signal()
    signal.payload.update(patch)
    for policy in POLICIES:
        assert candidates(signal, policy) == []

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


def test_live_capture_does_not_wait_for_history_and_overlap_is_idempotent(repo):
    from backend.statistics.models import CaptureCursor
    with repo.session.begin() as s:
        for idx in range(100):
            s.add(Signal(dedupe_key=f'backlog-{idx}',symbol='TESTUSDT',timeframe='30',
                event_time=60000+idx,name='LONG WATCH ENTRY',parameter_set_id='v',payload=broke_signal().payload))
    capture(repo)
    with repo.session() as s:
        assert s.get(CaptureCursor,'broke-outcomes-v2').last_id==25
        assert s.get(CaptureCursor,'broke-outcomes-live-v2').last_id==100
        assert s.scalar(select(Evaluation).where(Evaluation.plan['event_id'].as_integer()==100)) is not None
    with repo.session.begin() as s:
        s.add(Signal(dedupe_key='fresh',symbol='TESTUSDT',timeframe='30',event_time=70000,
            name='LONG WATCH ENTRY',parameter_set_id='v',payload=broke_signal().payload))
    capture(repo)
    with repo.session() as s:
        assert s.scalar(select(Evaluation).where(Evaluation.plan['event_id'].as_integer()==101)) is not None
        assert s.get(CaptureCursor,'broke-outcomes-v2').last_id==50
    for _ in range(4):capture(repo)
    with repo.session() as s:
        from backend.statistics.outcomes import POLICIES
        assert s.scalar(select(func.count()).select_from(Evaluation))==101*len(POLICIES)


def test_observer_reserves_capacity_for_recent_events_and_history(repo):
    from backend.statistics.worker import due_observations
    now=100*86400000
    with repo.session.begin() as s:
        for idx in range(40):
            row=candidates(broke_signal())[0]
            row.id=f'old-{idx}';row.event_time=60000;row.updated_at=0;s.add(row)
        current=candidates(broke_signal())[0]
        current.id='current';current.event_time=now-120000;current.updated_at=now-90000;s.add(current)
    rows=due_observations(repo,now)
    assert len(rows)==24 and any(r.id=='current' for r in rows)
    assert sum(r.id.startswith('old-') for r in rows)==23
    with repo.session.begin() as s:
        for idx in range(30):
            row=candidates(broke_signal())[0];row.id=f'fresh-{idx}'
            row.event_time=now-120000;row.updated_at=0;s.add(row)
    rows=due_observations(repo,now)
    assert len(rows)==24 and sum(r.id.startswith('old-') for r in rows)==12

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


def test_old_excluded_entries_are_preserved_but_not_counted_or_observed(repo):
    from backend.statistics.worker import due_observations
    with repo.session.begin() as s:
        for idx, (family, timeframe) in enumerate([
                ('WE', '5'), ('PINE READY', '30'), ('BRONZE', '30'), ('STRONG', '5'),
                ('MATURED', '30'), ('LATE WE', '5'), ('WE', '15'), ('PINE READY', '60')]):
            row = candidates(broke_signal())[0]
            row.id = f'old-cohort-{idx}';row.family = family;row.timeframe = timeframe
            s.add(row)
    client = TestClient(create_app(repo))
    url = f'/api/statistics?strategies=BROKE_SETUPS&policy={POLICY}&start=0&end=200000&limit=1'
    data = client.get(url).json()
    assert data['total'] == 2 and data['counts'] == {'PENDING': 2}
    assert {r['family'] for r in data['breakdowns']} == {'WE', 'PINE READY'}
    assert len(client.get(url + '&export=csv').text.splitlines()) == 3
    assert client.get(url + '&family=BRONZE').json()['total'] == 0
    assert client.get(url + '&timeframe=15').json()['total'] == 0
    assert {r.id for r in due_observations(repo, 300000)} == {'old-cohort-0', 'old-cohort-1'}
    with repo.session() as s:
        assert s.scalar(select(func.count()).select_from(Evaluation)) == 8

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
