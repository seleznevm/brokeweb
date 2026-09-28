from __future__ import annotations
import hashlib
import json
from sqlalchemy import select
from backend.models.schema import Rule,RuleState,Delivery
from backend.alerts.rules import matches

def digest(parts): return hashlib.sha256(json.dumps(parts,sort_keys=True,default=str).encode()).hexdigest()

def enqueue_matching(session,current,previous,now):
    """Runs inside the same transaction as the persisted setup. Rule rows serialize writers."""
    if current.get('replay'): return
    health=current.get('data_health')
    if isinstance(health,dict): health=health.get('status')
    if health not in {'HEALTHY','FULL_REALTIME','KLINE_REALTIME'}: return
    for row in session.scalars(select(Rule).where(Rule.enabled.is_(True)).with_for_update()):
        rule=row.payload
        if rule.get('strategy','BROKE_SETUPS')!=current.get('strategy','BROKE_SETUPS'):continue
        if rule['mode']=='confirmed' and not current.get('confirmed'): continue
        market=[current.get(x) for x in ('exchange','symbol','timeframe')]
        if current.get('strategy')=='WT_SETUPS':market+=['WT_SETUPS',current.get('signal_source')]
        state_key=digest([row.id,row.version,*market])
        state=session.get(RuleState,state_key)
        if state is None:
            state=RuleState(key=state_key,matched=False); session.add(state)
        eligible_previous=state.previous if rule['mode']=='confirmed' else previous
        matched=matches(rule['conditions'],current,eligible_previous)
        # event can target any signal without replacing its frozen canonical snapshot.
        if not matched:
            matched=any(matches(rule['conditions'],{**current,'event':name},eligible_previous) for name in current.get('signals',[]))
        state.previous=current
        was_matched=state.matched
        state.matched=matched
        if not matched: continue
        frequency=rule['frequency']; generation=current.get('setup_generation_id')
        if frequency=='first_occurrence' and was_matched: continue
        if state.last_enqueued is not None and now-state.last_enqueued<rule['cooldown_seconds']*1000: continue
        identity=[row.id,row.version,*market]
        if frequency=='once_per_bar': identity+=['bar',current.get('bar_start') if current.get('bar_start') is not None else current.get('reference_id')]
        elif frequency=='once_per_generation': identity+=['generation',generation]
        elif frequency=='first_occurrence': identity+=['occurrence',now,current.get('event_time')]
        else: identity+=['repeat',now,current.get('event_time')]
        key=digest(identity)
        if session.scalar(select(Delivery.id).where(Delivery.dedupe_key==key)) is not None: continue
        session.add(Delivery(dedupe_key=key,rule_id=row.id,rule_version=row.version,created_at=now,updated_at=now,next_attempt=now,status='pending',payload={'snapshot':current,'template':rule.get('template',''),'rule_name':rule['name']}))
        state.last_enqueued=now; state.generation=str(generation)
