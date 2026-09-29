import pytest

from tools.pine_reference.alignment import diagnose, HOURLY_METRICS
from tools.pine_reference.context_probe import context_source, FIELDS
from tools.pine_reference.exporter import generate
from backend.engine.syntax import Program, SOURCE


def records(step=30):
    refs, actual = [], []
    for minute in range(0, 120, step):
        current = 1 if minute < 60-step else 2 if minute < 120-step else 3
        final = 2 if minute < 60 else 3
        refs.append({'time': str(minute*60), 'PARITY_volume_24h': final*100*.9998,
                     **{'PARITY_'+k: final for k in HOURLY_METRICS}})
        actual.append({'bar_start': minute*60000, 'PARITY_volume_24h': current*100,
                       **{'PARITY_'+k: current for k in HOURLY_METRICS}})
    return refs, actual


@pytest.mark.parametrize('step,early', [(30, 2), (5, 22)])
def test_noncausal_alignment_is_explanatory_only(step, early):
    refs, actual = records(step)
    report = diagnose(refs, actual, step)
    stats = report['hourly_alignment']['return_1h']
    assert stats['before_hour_close']['hour_final_only_matches'] == early
    assert stats['at_hour_close']['same_timestamp_matches'] == 2
    assert report['volume_ratio']['constant_days'] == 1
    assert report['volume_ratio']['daily'][0]['median_ratio'] == pytest.approx(.9998)
    assert report['status'] == 'DIAGNOSTIC_ONLY'
    assert report['parity_status'] == 'UNVERIFIED'
    assert (refs, actual) == records(step)


def test_constant_series_does_not_prove_a_shift():
    refs, actual = records()
    for row in actual:
        row.update({'PARITY_'+k: 2 for k in HOURLY_METRICS})
    report = diagnose(refs[:2], actual, 30)
    stats = report['hourly_alignment']['return_1h']['before_hour_close']
    assert stats['hour_final_matches'] == 1
    assert stats['discriminating_samples'] == stats['hour_final_only_matches'] == 0


def test_missing_hour_final_cannot_be_replaced_with_next_available_bar():
    refs, actual = records()
    actual.pop(1)
    report = diagnose(refs, actual, 30)
    assert report['missing_python_rows'] == 1
    assert report['hourly_alignment']['return_1h']['before_hour_close']['unavailable'] == 1
    assert report['volume_ratio']['unavailable'] == 2


def test_na_and_zero_volume_are_not_evidence():
    refs, actual = records()
    for row in actual:
        row['PARITY_volume_24h'] = 0
        row['PARITY_return_1h'] = None
    report = diagnose(refs, actual, 30)
    assert report['volume_ratio']['days'] == 0
    assert report['volume_ratio']['unavailable'] == 4
    assert report['hourly_alignment']['return_1h']['before_hour_close']['samples'] == 0


def test_varying_daily_ratio_is_not_hidden():
    refs, actual = records()
    refs[1]['PARITY_volume_24h'] *= 1.01
    report = diagnose(refs, actual, 30)
    assert report['volume_ratio']['constant_days'] == 0
    assert report['volume_ratio']['daily'][0]['spread'] > .009


def test_duplicate_python_timestamps_rejected():
    refs, actual = records()
    with pytest.raises(ValueError, match='Duplicate'):
        diagnose(refs, actual + [actual[0]], 30)


@pytest.mark.parametrize('step', [45, 60, 240])
def test_unsupported_timeframe_rejected(step):
    with pytest.raises(ValueError):
        diagnose([], [], step)


def test_probe_preserves_detector_and_does_not_feed_lookahead_into_it(tmp_path):
    original = SOURCE.read_bytes()
    manifest = generate(tmp_path)
    source = (tmp_path/'Scalping_SMA_1.15.2_parity_contexts.pine').read_text(encoding='utf-8')
    metrics = (tmp_path/'Scalping_SMA_1.15.2_parity_metrics.pine').read_text(encoding='utf-8')
    # Everything before the appended diagnostic requests is the same detector.
    base = metrics.split('var int parityHistoryStart = time')[0]
    base = base.replace('PARITY metrics', 'PARITY contexts').replace('SMA-P-MET', 'SMA-P-CTX')
    assert source == context_source(base)
    Program(source)  # Local syntax check, not a TradingView compiler claim.
    assert manifest['contexts']['plot_count'] == len(FIELDS)+6 <= 64
    assert source.count('lookahead = barmerge.lookahead_on') == 1
    assert 'PARITY_CTX_quote_usd_requested' in source
    assert SOURCE.read_bytes() == original
