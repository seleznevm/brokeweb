import csv,io
from collections import Counter
from typing import Literal
from fastapi import APIRouter,Query,HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy import select,func
from backend.models.repository import now_ms
from .models import Evaluation,CaptureCursor
from .outcomes import POLICY
def serialize(row):
    return {key:getattr(row,key) for key in ('id','strategy','source','mode','family','exchange','symbol','timeframe','direction','event_time','updated_at','status','policy','plan','outcome')}
def rates(counts):
    win=counts.get('WIN',0);loss=counts.get('LOSS',0);expired=counts.get('EXPIRED',0)
    resolved=win+loss;mature=resolved+expired;total=sum(counts.values())
    return dict(counts=dict(counts),total=total,resolved=resolved,
        winrate=100*win/resolved if resolved else None,
        horizon_success=100*win/mature if mature else None,
        coverage=100*mature/total if total else None)
def router(repo):
    routes=APIRouter()
    @routes.get('/api/statistics')
    def statistics(strategy:Literal['BROKE']='BROKE',source:Literal['engine']|None=None,
                   mode:Literal['live','replay']='live',family:str|None=None,symbol:str|None=None,
                   direction:Literal['LONG','SHORT']|None=None,timeframe:str|None=None,
                   start:int|None=Query(None,ge=0),end:int|None=Query(None,ge=0),
                   quality:str|None=None,version:str|None=None,
                   limit:int=Query(50,ge=1,le=500),offset:int=Query(0,ge=0),export:Literal['csv']|None=None):
        end=now_ms() if end is None else end;start=end-7*86400000 if start is None else start
        if start>=end:raise HTTPException(422,'start must precede end')
        q=select(Evaluation).where(Evaluation.strategy==strategy,Evaluation.mode==mode,
            Evaluation.policy==POLICY,Evaluation.event_time>=start,Evaluation.event_time<end)
        for col,value in ((Evaluation.source,source),(Evaluation.family,family),(Evaluation.symbol,symbol.upper() if symbol else None),
                          (Evaluation.direction,direction),(Evaluation.timeframe,timeframe)):
            if value:q=q.where(col==value)
        if quality:q=q.where(Evaluation.plan['data_health'].as_string()==quality)
        if version:q=q.where(Evaluation.plan['parameter_version'].as_string()==version)
        q=q.order_by(Evaluation.event_time.desc(),Evaluation.id)
        if export:
            def stream():
                buf=io.StringIO();writer=csv.writer(buf)
                writer.writerow(['event_time_utc_ms','strategy','source','mode','family','exchange','symbol','timeframe','direction','entry','sl','status','reason','policy'])
                yield buf.getvalue();buf.seek(0);buf.truncate(0)
                with repo.session() as s:
                    for row in s.scalars(q.execution_options(yield_per=500)):
                        values=[row.event_time,row.strategy,row.source,row.mode,row.family,row.exchange,row.symbol,row.timeframe,row.direction,
                                row.plan.get('entry'),row.plan.get('sl'),row.status,row.outcome.get('reason',''),row.policy]
                        writer.writerow(["'"+v if isinstance(v,str) and v.startswith(('=','+','-','@')) else v for v in values])
                        yield buf.getvalue();buf.seek(0);buf.truncate(0)
            return StreamingResponse(stream(),media_type='text/csv',headers={'Content-Disposition':'attachment; filename=statistics.csv'})
        base=q.order_by(None).subquery()
        with repo.session() as s:
            groups=s.execute(select(base.c.source,base.c.family,base.c.status,func.count()).group_by(base.c.source,base.c.family,base.c.status)).all()
            counts=Counter();breakdowns={}
            for src,fam,status,n in groups:
                counts[status]+=n;breakdowns.setdefault((src,fam),Counter())[status]+=n
            rows=s.scalars(q.offset(offset).limit(limit)).all()
            cursors={r.name:r.last_id for r in s.scalars(select(CaptureCursor))}
            last_update=s.scalar(select(func.max(Evaluation.updated_at)))
        return dict(**rates(counts),items=[serialize(r) for r in rows],
            breakdowns=[dict(source=src,family=fam,**rates(c)) for (src,fam),c in sorted(breakdowns.items())],
            policy=POLICY,start=start,end=end,cursors=cursors,last_update=last_update)
    return routes
