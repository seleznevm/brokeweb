"""Conservative outcomes independent of Pine research."""
import math
MINUTE=60000
HORIZON=86400000
POLICY='move2-before-sl-24h-1m-v1'
TERMINAL={'WIN','LOSS','EXPIRED','MANAGED_EXIT','AMBIGUOUS','INVALID','UNSUPPORTED'}
FROZEN_POLICY='frozen-t1-before-sl-24h-1m-v1'
POLICIES={POLICY: {'hours':24,'target':'move2','management':None}}
POLICIES.update({f'frozen-t1-before-sl-{hours}h-1m-v1': {'hours':hours,'target':'t1','management':None}
                 for hours in (4,12,24,48)})
POLICIES.update({f'frozen-t1-{kind}-24h-1m-v1': {'hours':24,'target':'t1','management':kind}
                 for kind in ('exit','reduce')})
def number(v):return type(v) in (int,float) and math.isfinite(v)
def _legacy_evaluate(plan,bars,now):
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


def horizon(policy):
    return POLICIES[policy]['hours'] * 3600000


def execution_result(plan, exit_price, exit_time, bars):
    """Estimated one-unit round trip. R uses the original detector entry/SL."""
    sign=1 if plan['direction']=='LONG' else -1
    config=plan.get('costs',{})
    fee=config.get('fee_rate',0.)
    slip=(config.get('slippage_bps',0.)+config.get('spread_bps',0.)/2)/10000
    if not all(number(v) and v>=0 for v in (fee,slip)) or fee>=1 or slip>=1:
        return {'economics_status':'INVALID_COSTS','net_r':None}
    entry_fill=plan['entry']*(1+sign*slip)
    exit_fill=exit_price*(1-sign*slip)
    risk=abs(plan['entry']-plan['sl'])
    gross=sign*(exit_fill-entry_fill)
    fees=fee*(entry_fill+exit_fill)
    carry=0.
    complete=plan.get('funding_complete') is True
    for event in plan.get('funding_events',[]):
        at=event.get('time');rate=event.get('rate')
        if not number(at) or not number(rate): complete=False;continue
        if not plan['event_time']<at<=exit_time: continue
        price=event.get('mark_price')
        if not number(price):
            minute=next((b for b in bars if b['start']==at and b.get('confirmed')),None)
            price=minute.get('open') if minute else None
        if not number(price) or price<=0: complete=False;continue
        carry+=sign*rate*price
    net=gross-fees-carry
    return {'entry_fill':entry_fill,'exit_fill':exit_fill,'gross_r':gross/risk,
            'fees_r':fees/risk,'funding_r':carry/risk if complete else None,
            'net_r':net/risk if complete else None,'net_r_excluding_funding':(gross-fees)/risk,
            'net_return_pct':net/entry_fill*100 if complete else None,
            'economics_status':'ESTIMATED' if complete else 'FUNDING_UNVERIFIED',
            'fill_model':'signal-price plus fixed slippage/half-spread; adverse stop gaps; minute funding mark proxy'}


def evaluate(plan,bars,now,policy=POLICY):
    if policy==POLICY: return _legacy_evaluate(plan,bars,now)
    if policy not in POLICIES: raise ValueError('Unknown outcome policy')
    spec=POLICIES[policy]
    entry,sl,side,start=plan.get('entry'),plan.get('sl'),plan.get('direction'),plan.get('event_time')
    target=plan.get('t1')
    if (not all(number(v) for v in (entry,sl,target,start)) or min(entry,sl,target)<=0 or
            side not in ('LONG','SHORT') or (not sl<entry<target if side=='LONG' else not target<entry<sl)):
        return {'status':'INVALID','reason':'Missing or non-directional frozen Entry/SL/T1'}
    deadline=start+horizon(policy);cursor=start//MINUTE*MINUTE
    result={'status':'OPEN','target':target,'mfe_pct':0.,'mae_pct':0.,'observed_until':start}
    management=sorted([e for e in plan.get('management_events',[]) if number(e.get('time')) and
                       start<e['time']<=min(now,deadline) and (e.get('kind')=='exit' or spec['management']=='reduce' and e.get('kind')=='reduce')],key=lambda e:e['time']) if spec['management'] else []
    sign=1 if side=='LONG' else -1
    def finish(status,price,at,bar,reason):
        return {**result,'status':status,'reason':reason,'outcome_bar':bar,'exit_time':at,'exit_price':price,
                'observed_until':at,'elapsed_minutes':(at-start)/MINUTE,
                **execution_result(plan,price,at,bars)}
    for b in sorted(bars,key=lambda b:b['start']):
        if not b.get('confirmed',False) or b['start']<cursor: continue
        if b['start']>=min(now,deadline) or b['end']>now: break
        if b['start']!=cursor:
            return {**result,'status':'DATA_GAP','reason':'Missing minute before outcome','gap_at':cursor}
        if (b['end']-b['start']!=MINUTE or not all(number(b.get(k)) for k in ('open','high','low','close')) or
                b['low']<=0 or b['low']>min(b['open'],b['close']) or b['high']<max(b['open'],b['close']) or b['high']<b['low']):
            return {**result,'status':'DATA_GAP','reason':'Invalid OHLC minute','gap_at':cursor}
        win=b['high']>=target if sign==1 else b['low']<=target
        loss=b['low']<=sl if sign==1 else b['high']>=sl
        partial=b['start']<start or b['end']>deadline
        event=management[0] if management and management[0]['time']<=b['end'] else None
        if (win or loss) and (partial or win and loss or event and event['time']<b['end']):
            return {**result,'status':'AMBIGUOUS','reason':'Unknown first-touch order within boundary/management minute' if partial or event else 'T1 and SL in same minute','outcome_bar':b['start'],'observed_until':b['end']}
        if not partial:
            result['mfe_pct']=max(result['mfe_pct'],sign*( (b['high'] if sign==1 else b['low'])/entry-1)*100)
            result['mae_pct']=max(result['mae_pct'],-sign*((b['low'] if sign==1 else b['high'])/entry-1)*100)
        result['observed_until']=min(b['end'],deadline)
        if win or loss:
            # At an adverse opening gap the stop cannot fill at the old level.
            price=(min(sl,b['open']) if sign==1 else max(sl,b['open'])) if loss else target
            return finish('LOSS' if loss else 'WIN',price,b['end'],b['start'],'FROZEN_SL' if loss else 'FROZEN_T1')
        if event:
            if not number(event.get('price')) or event['price']<=0:
                return {**result,'status':'INVALID','reason':'Missing management exit price'}
            return finish('MANAGED_EXIT',event['price'],event['time'],b['start'],'ACTIVE_PLAN_'+event['kind'].upper())
        cursor=b['end']
        if cursor>=deadline:
            if b['end']!=deadline:
                return {**result,'status':'AMBIGUOUS','reason':'No exact horizon price in partial minute','outcome_bar':b['start']}
            return finish('EXPIRED',b['close'],deadline,b['start'],'TIME_EXIT')
    if cursor+MINUTE<=now:
        return {**result,'status':'DATA_GAP','reason':'Minute evidence unavailable','gap_at':cursor}
    return result
