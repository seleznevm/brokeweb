import csv,io
from collections import Counter
from typing import Literal
from fastapi import APIRouter,Query,HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy import select,func
from backend.models.repository import now_ms
from .models import Evaluation,CaptureCursor
from .outcomes import POLICY,POLICIES
from .performance import summary
from .trades import campaign_cohort
from .notifications import notification_evidence
from .entries import entry_cohort
def serialize(row):
    result={key:getattr(row,key) for key in ('id','strategy','source','mode','family','exchange','symbol','timeframe','direction','event_time','updated_at','status','policy','plan','outcome')}
    if result['strategy']=='BROKE':result['strategy']='BROKE_SETUPS'
    return result
def rates(counts):
    win=counts.get('WIN',0);loss=counts.get('LOSS',0);expired=counts.get('EXPIRED',0)+counts.get('MANAGED_EXIT',0)+counts.get('BREAKEVEN',0)
    resolved=win+loss;mature=resolved+expired;total=sum(counts.values())
    return dict(counts=dict(counts),total=total,resolved=resolved,
        winrate=100*win/resolved if resolved else None,
        horizon_success=100*win/mature if mature else None,
        coverage=100*mature/total if total else None)
def router(repo):
    routes=APIRouter()
    @routes.get('/api/statistics')
    def statistics(strategy:Literal['BROKE','BROKE_SETUPS','BROKE_PB']|None=None,strategies:str|None=None,source:Literal['engine']|None=None,
                   mode:Literal['live','replay']='live',family:str|None=None,symbol:str|None=None,
                   direction:Literal['LONG','SHORT']|None=None,timeframe:str|None=None,
                   start:int|None=Query(None,ge=0),end:int|None=Query(None,ge=0),
                   quality:str|None=None,version:str|None=None,engine_version:str|None=None,
                   policy:str=POLICY,path:str|None=None,group_by:Literal['family','direction','path','timeframe']='family',
                   limit:int=Query(50,ge=1,le=500),offset:int=Query(0,ge=0),export:Literal['csv']|None=None):
        end=now_ms() if end is None else end;start=end-7*86400000 if start is None else start
        if start>=end:raise HTTPException(422,'start must precede end')
        if policy not in POLICIES:raise HTTPException(422,'Unknown policy')
        selected=set(strategies.split(',')) if strategies is not None else {strategy} if strategy else {'BROKE_SETUPS','BROKE_PB'}
        if 'BROKE' in selected:selected.remove('BROKE');selected.add('BROKE_SETUPS')
        selected.discard('')
        if selected-{'BROKE_SETUPS','BROKE_PB'}:raise HTTPException(422,'Unknown strategy')
        q=select(Evaluation).where(entry_cohort(),Evaluation.mode==mode,
            Evaluation.policy==policy,Evaluation.event_time>=start,Evaluation.event_time<end)
        for col,value in ((Evaluation.source,source),(Evaluation.family,family),(Evaluation.symbol,symbol.upper() if symbol else None),
                          (Evaluation.direction,direction),(Evaluation.timeframe,timeframe)):
            if value:q=q.where(col==value)
        if quality:q=q.where(Evaluation.plan['data_health'].as_string()==quality)
        if version:q=q.where(Evaluation.plan['parameter_version'].as_string()==version)
        if engine_version:q=q.where(Evaluation.plan['engine_version'].as_string()==engine_version)
        if path:q=q.where(Evaluation.plan['path'].as_string()==path)
        q=q.order_by(Evaluation.event_time.desc(),Evaluation.id)
        with repo.session() as s:
            cohort=list(s.scalars(q.execution_options(yield_per=500))) if 'BROKE_SETUPS' in selected else []
            if 'BROKE_PB' in selected and mode=='live':
                pb=campaign_cohort(s,start,end)
                for name,value in (('source',source),('family',family),('symbol',symbol.upper() if symbol else None),('direction',direction),('timeframe',timeframe)):
                    if value:pb=[r for r in pb if getattr(r,name)==value]
                for name,value in (('data_health',quality),('parameter_version',version),('engine_version',engine_version),('path',path)):
                    if value:pb=[r for r in pb if r.plan.get(name)==value]
                cohort.extend(pb)
            cohort.sort(key=lambda r:(-r.event_time,r.id))
            cursors={r.name:r.last_id for r in s.scalars(select(CaptureCursor))}
            last_update=s.scalar(select(func.max(Evaluation.updated_at)))
            if cohort:last_update=max(last_update or 0,max(r.updated_at for r in cohort))
            rows=cohort[offset:offset+limit]
            notifications=notification_evidence(s,rows) if not export else {}
        if export:
            def stream():
                buf=io.StringIO();writer=csv.writer(buf)
                writer.writerow(['event_time_utc_ms','strategy','source','mode','family','exchange','symbol','timeframe','direction','entry','sl','t1','status','reason','policy','path','net_r','fees_r','funding_r','economics_status','parameter_version','engine_version'])
                yield buf.getvalue();buf.seek(0);buf.truncate(0)
                for row in cohort:
                        values=[row.event_time,'BROKE_SETUPS' if row.strategy=='BROKE' else row.strategy,row.source,row.mode,row.family,row.exchange,row.symbol,row.timeframe,row.direction,
                                row.plan.get('entry'),row.plan.get('sl'),row.plan.get('t1'),row.status,row.outcome.get('reason',''),row.policy,
                                row.plan.get('path'),row.outcome.get('net_r'),row.outcome.get('fees_r'),row.outcome.get('funding_r'),row.outcome.get('economics_status'),row.plan.get('parameter_version'),row.plan.get('engine_version')]
                        writer.writerow(["'"+v if isinstance(v,str) and v.startswith(('=','+','-','@')) else v for v in values])
                        yield buf.getvalue();buf.seek(0);buf.truncate(0)
            return StreamingResponse(stream(),media_type='text/csv',headers={'Content-Disposition':'attachment; filename=statistics.csv'})
        counts=Counter(r.status for r in cohort)
        performance=summary(cohort)
        buckets={}
        for row in cohort:
            group=row.plan.get('path') if group_by=='path' else getattr(row,group_by)
            buckets.setdefault((row.strategy,row.source,group),[]).append(row)
        detailed=[]
        for (strat,src,group),items in sorted(buckets.items(),key=lambda item:str(item[0])):
            detailed.append(dict(strategy='BROKE_SETUPS' if strat=='BROKE' else strat,source=src,family=group if group_by=='family' else None,group=group,
                **rates(Counter(r.status for r in items)),performance=summary(items)))
        return dict(**rates(counts),items=[{**serialize(r),'telegram':notifications.get(r.id)} for r in rows],
            breakdowns=detailed,performance=performance,group_by=group_by,
            policy=policy,policies=[dict(id=k,**v) for k,v in POLICIES.items()],
            strategies=sorted(selected),start=start,end=end,cursors=cursors,last_update=last_update)
    from .charts import register_chart_route
    register_chart_route(routes,repo)
    return routes
