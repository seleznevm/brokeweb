"""Conservative outcomes independent of Pine research."""
import math
MINUTE=60000
HORIZON=86400000
POLICY='move2-before-sl-24h-1m-v1'
TERMINAL={'WIN','LOSS','EXPIRED','AMBIGUOUS','INVALID','UNSUPPORTED'}
def number(v):return type(v) in (int,float) and math.isfinite(v)
def evaluate(plan,bars,now):
    entry,sl,side,start=plan.get('entry'),plan.get('sl'),plan.get('direction'),plan['event_time']
    if not number(entry) or not number(sl) or entry<=0 or sl<=0 or side not in ('LONG','SHORT') or (sl>=entry if side=='LONG' else sl<=entry):
        return dict(status='INVALID',reason='Missing or non-directional entry/initial SL')
    sign=1 if side=='LONG' else -1
    target=entry*(1+sign*.02);deadline=start+HORIZON;cursor=start//MINUTE*MINUTE
    result=dict(status='OPEN',target=target,mfe_pct=0.,mae_pct=0.,observed_until=start)
    for b in sorted(bars,key=lambda b:b['start']):
        if not b.get('confirmed',False) or b['start']<cursor:continue
        if b['start']>=min(now,deadline):break
        if b['end']>now:continue
        if b['start']!=cursor:
            return dict(result,status='DATA_GAP',reason='Missing minute before outcome',gap_at=cursor)
        if b['end']-b['start']!=MINUTE or not all(number(b.get(k)) for k in ('open','high','low','close')):
            return dict(result,status='DATA_GAP',reason='Invalid minute evidence',gap_at=cursor)
        win=b['high']>=target if sign==1 else b['low']<=target
        loss=b['low']<=sl if sign==1 else b['high']>=sl
        partial=b['start']<start or b['end']>deadline
        if (win or loss) and partial:
            return dict(result,status='AMBIGUOUS',reason='Boundary minute touch: order relative to signal/deadline unknown',outcome_bar=b['start'])
        if not partial:
            result['mfe_pct']=max(result['mfe_pct'],((b['high']/entry-1) if sign==1 else (1-b['low']/entry))*100)
            result['mae_pct']=max(result['mae_pct'],((1-b['low']/entry) if sign==1 else (b['high']/entry-1))*100)
        result['observed_until']=min(b['end'],deadline)
        if win and loss:return dict(result,status='AMBIGUOUS',reason='2% and SL in same minute',outcome_bar=b['start'])
        if win or loss:return dict(result,status='WIN' if win else 'LOSS',outcome_bar=b['start'],elapsed_minutes=(b['end']-start)/MINUTE)
        cursor=b['end']
        if cursor>=deadline:return dict(result,status='EXPIRED')
    if cursor+MINUTE<=now:return dict(result,status='DATA_GAP',reason='Minute evidence unavailable',gap_at=cursor)
    return result
