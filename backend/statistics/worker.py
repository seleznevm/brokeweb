"""Low-rate independent observer; keeps tracking symbols outside the scan pool."""
import asyncio,dataclasses,logging
from collections import OrderedDict
from sqlalchemy import select
from backend.models.repository import Repository,now_ms
from backend.models.schema import MarketBar,Signal
from .prices import BybitStatisticsAdapter,BinancePerpetualAdapter
from .models import Evaluation
from .capture import capture
from .entries import entry_cohort
from .outcomes import evaluate,execution_result,TERMINAL,POLICY,POLICIES,horizon,MINUTE,number
log=logging.getLogger(__name__)
FUNDING_CACHE=OrderedDict()
def cached(repo,row,stop):
    with repo.session() as s:
        bars=s.scalars(select(MarketBar).where(MarketBar.exchange==row.exchange,MarketBar.symbol==row.symbol,
            MarketBar.timeframe=='1',MarketBar.start>=row.event_time//MINUTE*MINUTE,
            MarketBar.start<stop,MarketBar.confirmed.is_(True)).order_by(MarketBar.start)).all()
        return [{k:getattr(b,k) for k in ('start','end','open','high','low','close','confirmed')} for b in bars]
async def observe(repo,row,adapters,now):
    deadline=row.event_time+horizon(row.policy)
    stop=min(now//MINUTE*MINUTE,((deadline+MINUTE-1)//MINUTE)*MINUTE)
    plan=dict(row.plan)
    settled=row.status in TERMINAL
    if settled and number(row.outcome.get('exit_time')):
        stop=min(stop,row.outcome['exit_time']+MINUTE)
    if POLICIES[row.policy]['management'] and not settled:
        with repo.session() as s:
            events=s.scalars(select(Signal).where(Signal.symbol==row.symbol,Signal.timeframe==row.timeframe,
                Signal.parameter_set_id==plan.get('parameter_version'),Signal.event_time>row.event_time,
                Signal.event_time<=min(now,deadline),Signal.name.in_(['ACTIVE PLAN EXIT','ACTIVE PLAN DEGRADED'])))
            plan['management_events']=[{'time':e.event_time,'price':e.payload.get('price'),
                'kind':'exit' if e.name=='ACTIVE PLAN EXIT' else 'reduce'} for e in events
                if e.payload.get('exchange','BYBIT')==row.exchange and e.payload.get('setup_generation_id')==plan.get('generation')
                and bool(e.payload.get('replay'))==(row.mode=='replay') and e.payload.get('signal_source','engine')=='engine'
                and e.payload.get('strategy','BROKE_SETUPS')=='BROKE_SETUPS'
                and (e.name=='ACTIVE PLAN EXIT' or e.payload.get('active_plan_health')=='REDUCE')]
    bars=await asyncio.to_thread(cached,repo,row,stop)
    outcome=dict(row.outcome) if settled else evaluate(plan,bars,now,row.policy)
    if outcome['status']=='DATA_GAP' and stop>row.event_time:
        # Fetch missing evidence forward in bounded pages. Long horizons never
        # skip an early gap by loading only the latest 1000 minutes.
        first=outcome.get('gap_at',row.event_time//MINUTE*MINUTE)
        fetch_stop=min(stop,first+1000*MINUTE)
        count=max(1,(fetch_stop-first)//MINUTE)
        evidence=await adapters[row.exchange].backfill(row.symbol,'1',count,end=fetch_stop-1)
        await asyncio.to_thread(repo.save_bars,[dataclasses.asdict(b) for b in evidence if b.confirmed])
        bars=await asyncio.to_thread(cached,repo,row,stop)
        outcome=evaluate(plan,bars,now,row.policy)
    if row.policy!=POLICY and outcome['status'] in ('WIN','LOSS','EXPIRED','MANAGED_EXIT'):
        funding_stop=outcome['exit_time']
        key=(row.exchange,row.symbol,row.event_time,funding_stop//MINUTE)
        try:
            if key not in FUNDING_CACHE:
                FUNDING_CACHE[key]=await adapters[row.exchange].funding_history(row.symbol,row.event_time,funding_stop)
                if len(FUNDING_CACHE)>128:FUNDING_CACHE.popitem(last=False)
            plan.update(funding_events=FUNDING_CACHE[key],funding_complete=True)
            # Bybit returns the funding rate without its historical mark. A
            # boundary payment may need one extra closed minute for the proxy.
            starts={b['start'] for b in bars}
            for payment in plan['funding_events']:
                at=payment.get('time')
                if (number(at) and row.event_time<at<=funding_stop and not number(payment.get('mark_price'))
                        and at not in starts and at+MINUTE<=now):
                    extra=await adapters[row.exchange].backfill(row.symbol,'1',1,end=at+MINUTE-1)
                    confirmed=[dataclasses.asdict(b) for b in extra if b.confirmed and b.start==at]
                    await asyncio.to_thread(repo.save_bars,confirmed)
                    bars.extend(confirmed);starts.update(b['start'] for b in confirmed)
        except Exception as exc:
            log.warning('Funding unavailable for %s: %s',row.symbol,type(exc).__name__)
            plan['funding_complete']=False
        outcome={**outcome,**execution_result(plan,outcome['exit_price'],outcome['exit_time'],bars)}
    return outcome
def save(repo,key,outcome,now):
    with repo.session.begin() as s:
        row=s.get(Evaluation,key)
        if row and (row.status not in TERMINAL or row.outcome.get('economics_status')=='FUNDING_UNVERIFIED'):
            if row.status in TERMINAL and row.status!=outcome['status']:return
            if row.status in TERMINAL:
                # Late funding evidence may complete economics, never rewrite the
                # original first touch, price, time or excursion evidence.
                economic_keys=('entry_fill','exit_fill','gross_r','fees_r','funding_r','net_r',
                               'net_r_excluding_funding','net_return_pct','economics_status','fill_model')
                row.outcome={**row.outcome,**{k:outcome[k] for k in economic_keys if k in outcome}}
            else:
                row.status=outcome['status'];row.outcome=outcome
            row.updated_at=now


def due_observations(repo,now,limit=24):
    from sqlalchemy import or_
    with repo.session() as s:
        q=select(Evaluation).where(entry_cohort(),or_(Evaluation.status.not_in(TERMINAL),
            Evaluation.outcome['economics_status'].as_string()=='FUNDING_UNVERIFIED'),
            Evaluation.updated_at<now-60000).order_by(Evaluation.updated_at,Evaluation.event_time.desc(),Evaluation.id)
        boundary=now-48*60*60*1000
        recent=list(s.scalars(q.where(Evaluation.event_time>=boundary).limit(limit)))
        history=list(s.scalars(q.where(Evaluation.event_time<boundary).limit(limit)))
        share=limit//2
        rows=recent[:share]+history[:share]
        return (rows+recent[share:]+history[share:])[:limit]


async def main():
    logging.basicConfig(level=logging.INFO)
    repo=Repository();await asyncio.to_thread(repo.initialize)
    adapters={'BYBIT':BybitStatisticsAdapter(retries=1),'BINANCE':BinancePerpetualAdapter(retries=1)}
    try:
        while True:
            try:
                captured=await asyncio.to_thread(capture,repo)
                if any(captured.values()):log.info('Captured observations: %s',captured)
                rows=await asyncio.to_thread(due_observations,repo,now_ms())
                for row in rows:
                    try:outcome=await observe(repo,row,adapters,now_ms())
                    except Exception as exc:
                        log.warning('Observation unavailable for %s %s: %s',row.exchange,row.symbol,type(exc).__name__)
                        outcome={**row.outcome,'status':'DATA_GAP','reason':'Exchange evidence temporarily unavailable'}
                    await asyncio.to_thread(save,repo,row.id,outcome,now_ms())
                    await asyncio.sleep(.25)
                pending=sum(row.status not in TERMINAL for row in rows)
                await asyncio.to_thread(repo.heartbeat,'statistics',{'status':'HEALTHY','observations_in_cycle':len(rows),'pending_in_cycle':pending,
                    'recent_in_cycle':sum(r.event_time>=now_ms()-48*60*60*1000 for r in rows),
                    'oldest_event_in_cycle':min((r.event_time for r in rows),default=None)})
            except Exception:log.exception('Statistics cycle failed')
            await asyncio.sleep(15)
    finally:
        for adapter in adapters.values():await adapter.close()
if __name__=='__main__':asyncio.run(main())
