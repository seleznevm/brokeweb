"""Freeze signal events; never count periodic state updates."""
import hashlib, json
from sqlalchemy import select
from backend.models.schema import Signal
from .models import Evaluation, CaptureCursor
from .outcomes import POLICY, POLICIES, evaluate
from backend.setups_config import costs
from .entries import FAMILIES, ENTRY_TIMEFRAMES

def candidates(row, policy=POLICY, settings=None):
    p = row.payload
    if p.get('strategy','BROKE_SETUPS')!='BROKE_SETUPS' or p.get('signal_source','engine')!='engine': return []
    if row.timeframe not in ENTRY_TIMEFRAMES: return []
    family=FAMILIES.get(row.name)
    families=[family] if family else []
    source = 'engine'
    strategy = 'BROKE'
    identity = ['signal', row.id]
    items = []
    for family in set(families):
        event_time = p.get('event_time') if p.get('event_time') is not None else row.event_time
        metrics=p.get('metrics') or {}
        def value(name,fallback):
            v=metrics.get(name)
            return fallback if v is None else v
        ready=family=='PINE READY'
        modern=policy!=POLICY
        price=p.get('price') if p.get('price') is not None else p.get('entry')
        target=p.get('tp1') if p.get('tp1') is not None else p.get('t1')
        plan = dict(
            event_id=row.id, event_time=event_time,
            entry=value('tradePlanEntry',price) if ready and modern else price,
            sl=value('tradePlanSL' if ready else 'estimatedSL',p.get('sl')) if modern else p.get('sl'), direction=p.get('direction'),
            t1=value('tradePlanT1' if ready else 'targetT1',target) if modern else target,
            parameter_version=p.get('parameter_hash', getattr(row, 'parameter_set_id', None)),
            engine_version=p.get('engine_version'), generation=p.get('setup_generation_id'),
            data_health=p.get('data_health'), path=p.get('risk_path') or p.get('trigger_path') or p.get('candidate_path'),
            costs=costs(settings or {}), funding_complete=False,
            time_basis='detector_event', original_plan_entry=p.get('entry'), signal_setups=p.get('signal_setups', []),
            exchange=p.get('exchange','BYBIT'), symbol=row.symbol, timeframe=row.timeframe,
            confirmed=p.get('confirmed'), ready_diagnostics=p.get('ready_diagnostics'),
            formation=p.get('formation'), execution=p.get('execution'), geometry=p.get('geometry'),
            execution_check=p.get('execution_check'),
        )
        key = hashlib.sha256(json.dumps([policy, strategy, *identity, family], sort_keys=True).encode()).hexdigest()
        outcome = evaluate(plan, [], event_time,policy)
        if outcome['status'] == 'OPEN': outcome = dict(outcome, status='PENDING', reason='Waiting for first price observation')
        exchange = p.get('exchange', 'BYBIT').upper()
        if exchange not in ('BYBIT', 'BINANCE'): outcome = dict(status='UNSUPPORTED', reason='No exchange adapter')
        items.append(Evaluation(
            id=key, policy=policy, strategy=strategy, source=source,
            mode='replay' if p.get('replay') else 'live', family=family,
            exchange=exchange, symbol=row.symbol, timeframe=row.timeframe,
            direction=p.get('direction') or 'NONE', event_time=event_time,
            updated_at=0, plan=plan, status=outcome['status'], outcome=outcome
        ))
    return items

def capture(repo):
    counts = {}
    with repo.session.begin() as s:
        # A new cursor replays older signals; stable legacy IDs preserve settled outcomes.
        cursor = s.get(CaptureCursor, 'broke-outcomes-v2')
        if cursor is None: cursor = CaptureCursor(name='broke-outcomes-v2', last_id=0); s.add(cursor)
        live_cursor=s.get(CaptureCursor,'broke-outcomes-live-v2')
        if live_cursor is None:
            # Start at the recent tail while the independent history cursor
            # catches up. Stable evaluation IDs deduplicate overlapping pages.
            tail=s.scalars(select(Signal.id).where(Signal.name.in_(list(FAMILIES)))
                           .order_by(Signal.id.desc()).limit(25)).all()
            live_cursor=CaptureCursor(name='broke-outcomes-live-v2',last_id=min(tail)-1 if tail else 0)
            s.add(live_cursor)
        count = 0;frozen_count=0;settings=repo.settings(s)
        for page_cursor in (live_cursor,cursor):
            query=select(Signal).where(Signal.id>page_cursor.last_id,Signal.name.in_(list(FAMILIES)))
            rows=s.scalars(query.order_by(Signal.id).limit(25)).all()
            for row in rows:
                for policy in POLICIES:
                    for item in candidates(row,policy,settings):
                        if s.get(Evaluation,item.id) is None:
                            s.add(item);s.flush()
                            if policy==POLICY:count+=1
                            else:frozen_count+=1
                page_cursor.last_id=row.id
        counts['broke'] = count
        counts['broke_frozen'] = frozen_count
    return counts
