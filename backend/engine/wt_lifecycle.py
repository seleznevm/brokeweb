"""Application lifecycle overlay; the original Pine action remains available."""
import math

def finite(value):return type(value) in (int,float) and math.isfinite(value)

def apply_stop_guard(snapshot,bars,previous=None):
    result=dict(snapshot)
    result.setdefault('pine_action',result.get('action'))
    direction=result.get('direction');stop=result.get('sl');origin=result.get('plan_bar_start')
    if direction not in ('LONG','SHORT') or not finite(stop) or not finite(origin):return result
    previous=previous or {}
    same=previous.get('setup_generation_id')==result.get('setup_generation_id') and previous.get('parameter_hash')==result.get('parameter_hash')
    hit=previous.get('stop_hit_bar_start') if same and previous.get('stop_hit') else result.get('stop_hit_bar_start') if result.get('stop_hit') else None
    if hit is None:
        for bar in bars:
            if not origin<bar['start']<=result['bar_start']:continue
            price=bar.get('low' if direction=='LONG' else 'high')
            if finite(price) and (price<=stop if direction=='LONG' else price>=stop):
                hit=bar['start'];break
        # On the signal candle its earlier high/low may precede the plan.
        # Only an observed price beyond the stop proves a hit on that candle.
        price=result.get('price')
        if hit is None and finite(price) and (price<=stop if direction=='LONG' else price>=stop):hit=result['bar_start']
    result['stop_hit']=hit is not None
    if hit is not None:
        result.update(action='SL HIT',stop_hit_bar_start=hit,signals=[],signal_setups=[])
    return result
