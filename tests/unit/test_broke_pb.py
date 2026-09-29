import pytest
from backend.models.repository import Repository, now_ms
from backend.models.schema import BrokePBPosition, Delivery
from backend.engine.broke_pb import process_broke_pb_snapshot
from sqlalchemy import select

@pytest.fixture
def repo(tmp_path):
    r = Repository(f"sqlite:///{tmp_path}/test_pb.db")
    r.initialize()
    return r

def test_broke_pb_long_entry_from_support_top(repo):
    now = now_ms()
    snapshot = {
        'exchange': 'BYBIT',
        'symbol': 'BTCUSDT',
        'timeframe': '30',
        'price': 60000.0,
        'direction': 'LONG',
        'support_top': 60050.0,
        'support_bottom': 59800.0,
        'resistance_bottom': 61200.0,
        'resistance_top': 61500.0,
        'atr': 200.0,
        'bar': {
            'open': 60100.0,
            'high': 60200.0,
            'low': 60020.0, # penetrated/touched support_top (60050)
            'close': 60040.0,
            'start': now - 1800000,
            'end': now
        },
        'avg_setup': 75.0,
        'mae': 15.0,
        'execution': 80.0
    }
    settings = {
        'broke_pb_position_usdt': 500.0,
        'broke_pb_telegram_bot_token': 'test:token',
        'broke_pb_telegram_chat_id': '-100123456789',
        'broke_pb_telegram_topic_id': '101'
    }

    with repo.session.begin() as s:
        process_broke_pb_snapshot(s, snapshot, settings, now)

    with repo.session() as s:
        pos = s.scalar(select(BrokePBPosition).where(BrokePBPosition.symbol == 'BTCUSDT'))
        assert pos is not None
        assert pos.direction == 'LONG'
        assert pos.status == 'OPEN'
        assert pos.nominal_usdt == 500.0
        assert pos.entry_price == 60050.0 # exact support_top
        assert pos.sl < pos.entry_price
        assert pos.tp1 > pos.entry_price
        assert pos.runner > pos.tp1

        # Check delivery queued for PB setup
        deliveries = s.scalars(select(Delivery).where(Delivery.rule_id == 'broke-pb')).all()
        assert len(deliveries) >= 1
        assert 'BROKE-PB LONG SETUP' in deliveries[0].payload['text']
        assert deliveries[0].payload['telegram_chat_id'] == '-100123456789'
        assert deliveries[0].payload['telegram_topic_id'] == '101'

def test_broke_pb_short_entry_from_resistance_bottom(repo):
    now = now_ms()
    snapshot = {
        'exchange': 'BYBIT',
        'symbol': 'ETHUSDT',
        'timeframe': '30',
        'price': 3000.0,
        'direction': 'SHORT',
        'support_top': 2900.0,
        'support_bottom': 2880.0,
        'resistance_bottom': 2990.0,
        'resistance_top': 3020.0,
        'atr': 20.0,
        'bar': {
            'open': 2980.0,
            'high': 2995.0, # penetrated/touched resistance_bottom (2990)
            'low': 2975.0,
            'close': 2985.0,
            'start': now - 1800000,
            'end': now
        },
        'avg_setup': 70.0,
        'mae': 10.0,
        'execution': 75.0
    }
    settings = {
        'broke_pb_position_usdt': 750.0,
        'broke_pb_telegram_bot_token': 'test:token',
        'broke_pb_telegram_chat_id': '-100999999999',
        'broke_pb_telegram_topic_id': '202'
    }

    with repo.session.begin() as s:
        process_broke_pb_snapshot(s, snapshot, settings, now)

    with repo.session() as s:
        pos = s.scalar(select(BrokePBPosition).where(BrokePBPosition.symbol == 'ETHUSDT'))
        assert pos is not None
        assert pos.direction == 'SHORT'
        assert pos.status == 'OPEN'
        assert pos.nominal_usdt == 750.0
        assert pos.entry_price == 2990.0 # exact resistance_bottom
        assert pos.sl > pos.entry_price
        assert pos.tp1 < pos.entry_price
        assert pos.runner < pos.tp1

def test_position_manager_tp1_and_runner(repo):
    now = now_ms()
    # Create existing open LONG position
    with repo.session.begin() as s:
        s.add(BrokePBPosition(
            symbol='SOLUSDT',
            direction='LONG',
            status='OPEN',
            nominal_usdt=500.0,
            entry_price=150.0,
            entry_time=now - 3600000,
            bar_start=now - 3600000,
            sl=145.0,
            tp1=155.0,
            runner=160.0,
            tp1_hit=False,
            runner_hit=False,
            payload={},
            updated_at=now - 3600000
        ))

    # Bar hits TP1 (high >= 155.0)
    snapshot_tp1 = {
        'exchange': 'BYBIT',
        'symbol': 'SOLUSDT',
        'timeframe': '30',
        'price': 154.0,
        'direction': 'LONG',
        'atr': 2.0,
        'bar': {'open': 151.0, 'high': 156.0, 'low': 150.5, 'close': 154.0, 'start': now - 1800000, 'end': now}
    }
    settings = {'broke_pb_pm_telegram_enabled': True, 'telegram_bot_token': 'tok', 'telegram_chat_id': 'chat'}

    with repo.session.begin() as s:
        process_broke_pb_snapshot(s, snapshot_tp1, settings, now)

    with repo.session() as s:
        pos = s.scalar(select(BrokePBPosition).where(BrokePBPosition.symbol == 'SOLUSDT'))
        assert pos.status == 'OPEN'
        assert pos.tp1_hit is True
        assert pos.runner_be is not None
        assert pos.runner_be > pos.entry_price

    # Next bar hits Runner target (high >= 160.0)
    now2 = now + 1800000
    snapshot_runner = {
        'exchange': 'BYBIT',
        'symbol': 'SOLUSDT',
        'timeframe': '30',
        'price': 161.0,
        'direction': 'LONG',
        'atr': 2.0,
        'bar': {'open': 156.0, 'high': 162.0, 'low': 155.0, 'close': 161.0, 'start': now, 'end': now2}
    }

    with repo.session.begin() as s:
        process_broke_pb_snapshot(s, snapshot_runner, settings, now2)

    with repo.session() as s:
        pos = s.scalar(select(BrokePBPosition).where(BrokePBPosition.symbol == 'SOLUSDT'))
        assert pos.status == 'CLOSED'
        assert pos.close_reason == 'RUNNER_TARGET'
        assert pos.pnl_usdt > 0

def test_position_manager_30m_bias_flip(repo):
    now = now_ms()
    with repo.session.begin() as s:
        s.add(BrokePBPosition(
            symbol='AVAXUSDT',
            direction='LONG',
            status='OPEN',
            nominal_usdt=500.0,
            entry_price=30.0,
            entry_time=now - 3600000,
            bar_start=now - 3600000,
            sl=28.0,
            tp1=32.0,
            runner=34.0,
            tp1_hit=False,
            runner_hit=False,
            payload={},
            updated_at=now - 3600000
        ))

    # 30m direction flips to SHORT
    snapshot_flip = {
        'exchange': 'BYBIT',
        'symbol': 'AVAXUSDT',
        'timeframe': '30',
        'price': 29.5,
        'direction': 'SHORT',
        'atr': 0.5,
        'bar': {'open': 30.1, 'high': 30.2, 'low': 29.4, 'close': 29.5, 'start': now - 1800000, 'end': now}
    }
    settings = {'broke_pb_pm_telegram_enabled': True, 'telegram_bot_token': 'tok', 'telegram_chat_id': 'chat'}

    with repo.session.begin() as s:
        process_broke_pb_snapshot(s, snapshot_flip, settings, now)

    with repo.session() as s:
        pos = s.scalar(select(BrokePBPosition).where(BrokePBPosition.symbol == 'AVAXUSDT'))
        assert pos.status == 'CLOSED'
        assert pos.close_reason == '30M_BIAS_FLIP'
