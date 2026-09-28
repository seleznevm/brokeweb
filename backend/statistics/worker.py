"""Low-rate independent observer; keeps tracking symbols outside the scan pool."""
import asyncio,dataclasses,logging
from sqlalchemy import select
from backend.models.repository import Repository,now_ms
from backend.models.schema import MarketBar
from .prices import BybitAdapter,BinancePerpetualAdapter
from .models import Evaluation
from .capture import capture
from .outcomes import evaluate,TERMINAL,HORIZON,MINUTE
log=logging.getLogger(__name__)
def cached(repo,row,stop):
    with repo.session() as s:
        bars=s.scalars(select(MarketBar).where(MarketBar.exchange==row.exchange,MarketBar.symbol==row.symbol,
            MarketBar.timeframe=='1',MarketBar.start>=row.event_time//MINUTE*MINUTE,
            MarketBar.start<stop,MarketBar.confirmed.is_(True)).order_by(MarketBar.start)).all()
        return [{k:getattr(b,k) for k in ('start','end','open','high','low','close','confirmed')} for b in bars]
async def observe(repo,row,adapters,now):
    stop=min(now//MINUTE*MINUTE,((row.event_time+HORIZON+MINUTE-1)//MINUTE)*MINUTE)
    bars=await asyncio.to_thread(cached,repo,row,stop)
    outcome=evaluate(row.plan,bars,now)
    if outcome['status']=='DATA_GAP' and stop>row.event_time:
        # At most 1441 one-minute bars; preserve the source exchange, never substitute.
        first=row.event_time//MINUTE*MINUTE
        count=min(1441,max(1,(stop-first)//MINUTE))
        evidence=await adapters[row.exchange].backfill(row.symbol,'1',count,end=stop-1)
        await asyncio.to_thread(repo.save_bars,[dataclasses.asdict(b) for b in evidence if b.confirmed])
        bars=await asyncio.to_thread(cached,repo,row,stop)
        outcome=evaluate(row.plan,bars,now)
    return outcome
def save(repo,key,outcome,now):
    with repo.session.begin() as s:
        row=s.get(Evaluation,key)
        if row and row.status not in TERMINAL:
            row.status=outcome['status'];row.outcome=outcome;row.updated_at=now
async def main():
    logging.basicConfig(level=logging.INFO)
    repo=Repository();await asyncio.to_thread(repo.initialize)
    adapters={'BYBIT':BybitAdapter(retries=1),'BINANCE':BinancePerpetualAdapter(retries=1)}
    try:
        while True:
            try:
                captured=await asyncio.to_thread(capture,repo)
                if any(captured.values()):log.info('Captured observations: %s',captured)
                with repo.session() as s:
                    rows=s.scalars(select(Evaluation).where(Evaluation.status.not_in(TERMINAL),
                        Evaluation.updated_at<now_ms()-60000).order_by(Evaluation.updated_at,Evaluation.event_time.desc()).limit(24)).all()
                for row in rows:
                    try:outcome=await observe(repo,row,adapters,now_ms())
                    except Exception as exc:
                        log.warning('Observation unavailable for %s %s: %s',row.exchange,row.symbol,type(exc).__name__)
                        outcome={**row.outcome,'status':'DATA_GAP','reason':'Exchange evidence temporarily unavailable'}
                    await asyncio.to_thread(save,repo,row.id,outcome,now_ms())
                    await asyncio.sleep(.25)
            except Exception:log.exception('Statistics cycle failed')
            await asyncio.sleep(15)
    finally:
        for adapter in adapters.values():await adapter.close()
if __name__=='__main__':asyncio.run(main())
