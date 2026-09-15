"""Analytical fixtures, deliberately not labeled TradingView golden data."""
import math
import pytest
from backend.engine import pine_compat as p


def test_na_history_and_nz():
    assert p.na(p.NA) and p.na(None)
    assert not p.na(False)
    assert p.nz(p.NA, 7) == 7
    assert p.history([10, 20, 30], 1) == 20
    assert p.na(p.history([1], 1))
    with pytest.raises(ValueError):
        p.history([1], -1)


def test_sma_sum_ignore_na_but_need_full_valid_observation_count():
    assert p.sma([1, p.NA, 3, 5], 3) == 3
    assert p.sum([1, p.NA, 3, 5], 3) == 9
    assert p.na(p.sma([1, p.NA], 2))
    assert p.na(p.sum([1], 2))


def test_ema_seed_is_first_observation_and_missing_keeps_state():
    result = p.ema_series([p.NA, 2, 4, p.NA, 8], 3)
    assert p.na(result[0])
    assert result[1:] == [2, 3, 3, 5.5]
    assert p.ema([2, 4, 8], 3) == 5.5


def test_wilder_rma_seed_and_atr_gap_true_range():
    result = p.rma_series([p.NA, 2, 4, 6, 10, p.NA], 3)
    assert all(p.na(v) for v in result[:3])
    assert result[3:] == pytest.approx([4, 6, 6])
    # TR [2, 5, 3]: second bar gaps above previous close of 10.
    assert p.atr([11, 15, 16], [9, 14, 13], [10, 14, 15], 3) == pytest.approx(10 / 3)
    assert p.na(p.tr(11, 9, handle_na=False))
    assert p.na(p.atr([1], [0], [.5], 14))


def test_extrema_count_bars_not_non_na_and_recent_tie_offsets():
    assert p.highest([100, 2, p.NA, 3], 3) == 3
    assert p.lowest([0, 2, p.NA, 3], 3) == 2
    assert p.highest([3], 5) == 3
    assert p.highestbars([9, 1, 9, 8], 4) == -1
    assert p.lowestbars([1, 2, 1, 3], 4) == -1
    assert p.na(p.lowest([p.NA], 5))


def test_pivot_confirmation_delay_and_plateau_rightmost_tie():
    assert p.na(p.pivothigh([1, 4, 5, 4], 2, 2))
    assert p.pivothigh([1, 4, 5, 4, 2], 2, 2) == 5
    assert p.na(p.pivothigh([1, 5, 5], 1, 1))
    assert p.pivothigh([5, 5, 1], 1, 1) == 5
    assert p.pivotlow([1, 1, 5], 1, 1) == 1
    assert p.na(p.pivotlow([5, 1, 1], 1, 1))


def test_crosses_valuewhen_barssince_change():
    assert p.crossover([2, 3], [2, 2])
    assert not p.crossover([1, 2], [2, 2])
    assert p.crossunder([2, 1], [2, 2])
    assert not p.crossunder([p.NA, 1], [2, 2])
    assert p.na(p.barssince([False, False]))
    assert p.barssince([True, False, False]) == 2
    assert p.barssince([False, True]) == 0
    assert p.valuewhen([True, False, True], [10, 20, 30], 1) == 10
    assert p.na(p.valuewhen([False], [2]))
    assert p.change([3, 7]) == 4
    assert p.change([False, True]) is True
    assert p.na(p.change([3]))


def test_rounding_ties_up_tick_decimal_and_clamp_na():
    assert p.round(2.5) == 3
    assert p.round(-2.5) == -2
    assert p.round(1.005, 2) == 1.01
    assert p.round_to_mintick(.125, .05) == .15
    assert p.round_to_mintick(-.125, .05) == -.1
    assert p.hlc3(6, 3, 3) == 4
    assert p.clamp(200, 0, 100) == 100
    assert p.na(p.clamp(p.NA, 0, 100))


@pytest.mark.parametrize('tf,seconds,micro', [('1',60,'30S'),('5',300,'1'),('15',900,'5'),('60',3600,'5'),('240',14400,'30'),('D',86400,'60')])
def test_timeframe_profiles(tf, seconds, micro):
    assert p.timeframe_seconds(tf) == seconds
    assert p.micro_timeframe(tf) == micro


@pytest.mark.parametrize('length', [0, -1, 1.5, True])
def test_invalid_lengths_rejected(length):
    with pytest.raises(ValueError):
        p.sma([1,2,3], length)
