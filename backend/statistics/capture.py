"""Freeze signal events; never count periodic state updates."""
import hashlib, json
from sqlalchemy import select
from backend.models.schema import Signal
from .models import Evaluation, CaptureCursor
from .outcomes import POLICY, evaluate

def candidates(row):
    p = row.payload
    families = {'LONG WATCH ENTRY': ['WE'], 'SHORT WATCH ENTRY': ['WE'], 'PINE READY LONG': ['PINE READY'], 'PINE READY SHORT': ['PINE READY']}.get(row.name, [])
    source = 'engine'
    strategy = 'BROKE'
    identity = ['signal', row.id]
    items = []
    for family in set(families):
        event_time = p.get('event_time') or row.event_time
        plan = dict(
            event_id=row.id, event_time=event_time,
            entry=p.get('price') if p.get('price') is not None else p.get('entry'),
            sl=p.get('sl'), direction=p.get('direction'),
            t1=p.get('tp1', p.get('t1')),
            parameter_version=p.get('parameter_hash', getattr(row, 'parameter_set_id', None)),
            engine_version=p.get('engine_version'), generation=p.get('setup_generation_id'),
            data_health=p.get('data_health'), path=p.get('trigger_path') or p.get('candidate_path'),
            time_basis='detector_event', original_plan_entry=p.get('entry'), signal_setups=p.get('signal_setups', [])
        )
        key = hashlib.sha256(json.dumps([POLICY, strategy, *identity, family], sort_keys=True).encode()).hexdigest()
        outcome = evaluate(plan, [], event_time)
        if outcome['status'] == 'OPEN': outcome = dict(outcome, status='PENDING', reason='Waiting for first price observation')
        exchange = p.get('exchange', 'BYBIT').upper()
        if exchange not in ('BYBIT', 'BINANCE'): outcome = dict(status='UNSUPPORTED', reason='No exchange adapter')
        items.append(Evaluation(
            id=key, policy=POLICY, strategy=strategy, source=source,
            mode='replay' if p.get('replay') else 'live', family=family,
            exchange=exchange, symbol=row.symbol, timeframe=row.timeframe,
            direction=p.get('direction') or 'NONE', event_time=event_time,
            updated_at=0, plan=plan, status=outcome['status'], outcome=outcome
        ))
    return items

def capture(repo):
    counts = {}
    with repo.session.begin() as s:
        cursor = s.get(CaptureCursor, 'broke')
        if cursor is None: cursor = CaptureCursor(name='broke', last_id=0); s.add(cursor)
        query = select(Signal).where(Signal.id > cursor.last_id, Signal.name.in_(['LONG WATCH ENTRY', 'SHORT WATCH ENTRY', 'PINE READY LONG', 'PINE READY SHORT']))
        rows = s.scalars(query.order_by(Signal.id).limit(25)).all()
        count = 0
        for row in rows:
            for item in candidates(row):
                if s.get(Evaluation, item.id) is None: s.add(item); s.flush(); count += 1
            cursor.last_id = row.id
        counts['broke'] = count
    return counts
