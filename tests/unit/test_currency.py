import pytest
from backend.engine.currency import CurrencyRates
from backend.engine.values import is_na
from backend.engine.runtime import PineEngine


def rates():
    return {'USDT|USD':[dict(available_at=100,expires_at=200,rate=.9996),
                        dict(available_at=300,expires_at=400,rate=None)]}


def test_currency_has_no_future_fill_or_unbounded_forward_fill():
    feed=CurrencyRates(rates())
    assert is_na(feed.at('USDT','USD',99))
    assert feed.at('USDT','USD',100)==.9996
    assert feed.at('USDT','USD',199)==.9996
    assert all(is_na(feed.at('USDT','USD',t)) for t in (200,250,300,400))
    assert is_na(feed.at('EUR','USD',100))
    assert feed.at('USD','USD',100)==1


@pytest.mark.parametrize('rate',[0,-1,float('inf'),float('nan'),True])
def test_bad_currency_rate_rejected(rate):
    rows=rates();rows['USDT|USD'][0]['rate']=rate
    with pytest.raises(ValueError):CurrencyRates(rows)


def test_currency_overlapping_intervals_rejected():
    rows=rates();rows['USDT|USD'][1]['available_at']=199
    with pytest.raises(ValueError):CurrencyRates(rows)


def test_checkpoint_cannot_restore_with_different_currency_inputs():
    engine=PineEngine('TESTUSDT','30',currency_rates=rates())
    state=engine.export_state()
    with pytest.raises(ValueError,match='currency inputs mismatch'):
        PineEngine('TESTUSDT','30').restore_state(state)
    PineEngine('TESTUSDT','30',currency_rates=rates()).restore_state(state)


def test_historical_rate_is_taken_at_open_not_at_next_days_close():
    engine=PineEngine('TESTUSDT','30',currency_rates={'USDT|USD':[
        dict(available_at=0,expires_at=1800000,rate=.999),
        dict(available_at=1800000,expires_at=3600000,rate=1.001)]})
    b=dict(start=0,end=1800000,open=10,high=11,low=9,close=10,volume=100)
    snapshot=engine.update(b)
    assert snapshot['metrics']['quoteToUsdRequested']==.999
    assert snapshot['metrics']['quoteToUsdFallback'] is False
