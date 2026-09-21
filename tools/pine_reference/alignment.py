"""Explain historical HTF alignment differences without changing acceptance data."""
import argparse
from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
from statistics import median

from .compare import number
from .full import inspect
from .native import dump

HOUR = 3_600_000
DAY = 24 * HOUR
HOURLY_METRICS = ('return_1h', 'return_6h', 'return_24h', 'natr_1h')


def diagnose(reference, actual, timeframe):
    """Compare at original timestamps, then test an explicitly noncausal hypothesis.

    The second comparison is diagnostic only: project the final Python value of
    an hour onto earlier chart bars in that same hour. Never write shifted rows
    or use this comparison to assign parity PASS.
    """
    step = int(timeframe) * 60_000
    if step >= HOUR or HOUR % step:
        raise ValueError('Hourly diagnosis requires a chart timeframe dividing 60 minutes')
    index = {}
    for row in actual:
        start = row['bar_start']
        if isinstance(start, bool) or not isinstance(start, int) or start < 0 or start % step:
            raise ValueError('Invalid Python bar_start')
        if start in index:
            raise ValueError('Duplicate Python candle')
        index[start] = row
    stats = {name: {phase: dict(samples=0, same_timestamp_matches=0,
                              hour_final_matches=0, discriminating_samples=0,
                              hour_final_only_matches=0, unavailable=0)
                    for phase in ('before_hour_close', 'at_hour_close')}
             for name in HOURLY_METRICS}
    ratios = defaultdict(list)
    examples = []
    missing = 0
    ratio_unavailable = 0
    close = lambda a, b: math.isclose(a, b, rel_tol=1e-10, abs_tol=1e-10)
    for row in reference:
        start = int(row['time']) * 1000
        final_start = start // HOUR * HOUR + HOUR - step
        current = index.get(start, {})
        final = index.get(final_start, {})
        missing += not bool(current)
        phase = 'at_hour_close' if start == final_start else 'before_hour_close'
        for name in HOURLY_METRICS:
            key = 'PARITY_' + name
            ref, same, projected = (number(r.get(key)) for r in (row, current, final))
            item = stats[name][phase]
            if any(v is None for v in (ref, same, projected)):
                item['unavailable'] += 1
                continue
            item['samples'] += 1
            item['same_timestamp_matches'] += close(ref, same)
            item['hour_final_matches'] += close(ref, projected)
            item['discriminating_samples'] += not close(same, projected)
            only_final = close(ref, projected) and not close(ref, same)
            item['hour_final_only_matches'] += only_final
            if name == 'return_1h' and only_final and len(examples) < 5:
                examples.append(dict(bar_start=start, hour_final_bar_start=final_start,
                                     reference=ref, python_same_timestamp=same,
                                     python_hour_final=projected))
        numerator = number(row.get('PARITY_volume_24h'))
        denominator = number(final.get('PARITY_volume_24h'))
        if numerator is not None and denominator is not None and denominator > 0:
            ratios[start // DAY].append(numerator / denominator)
        else:
            ratio_unavailable += 1
    daily = []
    for day, values in sorted(ratios.items()):
        lo, hi = min(values), max(values)
        daily.append(dict(date=datetime.fromtimestamp(day * DAY / 1000, timezone.utc).date().isoformat(),
                          samples=len(values), median_ratio=median(values), min_ratio=lo,
                          max_ratio=hi, spread=hi-lo,
                          constant_within_tolerance=len(values) >= 2 and hi-lo <= 1e-9))
    return dict(status='DIAGNOSTIC_ONLY', parity_status='UNVERIFIED', timeframe=str(timeframe),
                reference_rows=len(reference), missing_python_rows=missing, hourly_alignment=stats,
                examples=examples,
                volume_ratio=dict(hypothesis='Reference volume divided by Python hour-final volume; not an observed FX rate.',
                                  unavailable=ratio_unavailable, days=len(daily),
                                  constant_days=sum(d['constant_within_tolerance'] for d in daily),
                                  daily=daily),
                limitations=['Hour-final projection uses future data on earlier chart bars and is not a valid acceptance result.',
                             'A daily multiplier is consistent with currency conversion but does not establish its cause.',
                             'TradingView script version, inputs, request timestamps and conversion rate require independent confirmation.'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--reference', required=True, type=Path)
    parser.add_argument('--actual', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    if args.output.resolve() in (args.reference.resolve(), args.actual.resolve()):
        parser.error('Output cannot overwrite either input')
    symbol, tf, _, closed, _, _ = inspect(args.reference)
    actual = [json.loads(line) for line in args.actual.read_text().splitlines() if line.strip()]
    report = diagnose(closed, actual, tf)
    report.update(symbol=symbol, reference_sha256=hashlib.sha256(args.reference.read_bytes()).hexdigest(),
                  actual_sha256=hashlib.sha256(args.actual.read_bytes()).hexdigest())
    dump(args.output, report)
    print(json.dumps({k: report[k] for k in ('status', 'reference_rows', 'missing_python_rows')}))


if __name__ == '__main__':
    main()
