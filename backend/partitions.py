"""UTC monthly PostgreSQL partitions for snapshots/events, with default fallback."""
from __future__ import annotations
import argparse
from datetime import datetime,timezone
import json
import os
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from backend.models.repository import Repository,now_ms

PARENTS=('setup_snapshots','setup_events')
LOCK_ID=431580  # Shared with archive/restore; DDL and moving rows are serialized.


def month_start(timestamp):
    dt=datetime.fromtimestamp(timestamp/1000,timezone.utc)
    return int(datetime(dt.year,dt.month,1,tzinfo=timezone.utc).timestamp()*1000)


def shift_month(timestamp,offset):
    dt=datetime.fromtimestamp(timestamp/1000,timezone.utc)
    year,month=divmod(dt.year*12+dt.month-1+offset,12)
    return int(datetime(year,month+1,1,tzinfo=timezone.utc).timestamp()*1000)


def child_name(parent,start):
    if parent not in PARENTS:raise ValueError('Unsupported partition parent')
    return parent+datetime.fromtimestamp(start/1000,timezone.utc).strftime('_y%Ym%m')


def children(connection,parent):
    return [dict(row) for row in connection.execute(text('''
        SELECT child.relname AS name, pg_get_expr(child.relpartbound,child.oid) AS bounds
        FROM pg_inherits i JOIN pg_class child ON child.oid=i.inhrelid
        WHERE i.inhparent=to_regclass(:parent) ORDER BY child.relname
    '''),{'parent':parent}).mappings()]


def kind(connection,parent):
    return connection.scalar(text('SELECT relkind FROM pg_class WHERE oid=to_regclass(:parent)'),{'parent':parent})


def ensure_month(connection,parent,start,max_move_rows=10000):
    """Caller owns a transaction. A bounded DDL lock protects insert routing."""
    if parent not in PARENTS or start!=month_start(start):raise ValueError('Invalid partition boundary')
    name=child_name(parent,start);end=shift_month(start,1)
    expected=f'FOR VALUES FROM (\'{start}\') TO (\'{end}\')'
    existing={row['name']:row['bounds'] for row in children(connection,parent)}
    if name in existing:
        if existing[name]!=expected:raise ValueError('Existing partition bounds differ from expected month')
        return {'partition':name,'status':'EXISTS','moved_rows':0}
    connection.execute(text('LOCK TABLE '+parent+' IN ACCESS EXCLUSIVE MODE'))
    # Recheck after acquiring the lock: a different transaction may have created it.
    if name in {row['name'] for row in children(connection,parent)}:
        return {'partition':name,'status':'EXISTS','moved_rows':0}
    default=parent+'_default'
    count=connection.scalar(text('SELECT count(*) FROM '+default+' WHERE event_time>=:start AND event_time<:end'),{'start':start,'end':end})
    if count>max_move_rows:
        return {'partition':name,'status':'DEFERRED','default_rows':count,'moved_rows':0}
    # INCLUDING INDEXES copies the composite physical PK and parent indexes.
    # The standalone table allows a transactional move out of DEFAULT before ATTACH.
    connection.execute(text('CREATE TABLE '+name+' (LIKE '+parent+' INCLUDING ALL)'))
    connection.execute(text('ALTER TABLE '+name+' ADD CONSTRAINT '+name+'_month CHECK (event_time >= '+str(start)+' AND event_time < '+str(end)+')'))
    moved=connection.execute(text('WITH moved AS (DELETE FROM '+default+' WHERE event_time>=:start AND event_time<:end RETURNING *) INSERT INTO '+name+' SELECT * FROM moved'),{'start':start,'end':end}).rowcount
    if moved!=count:raise RuntimeError('Default partition move count changed')
    connection.execute(text('ALTER TABLE '+parent+' ATTACH PARTITION '+name+' FOR VALUES FROM ('+str(start)+') TO ('+str(end)+')'))
    return {'partition':name,'status':'CREATED','moved_rows':moved}


def ensure_partitions(repo,now=None,months_ahead=3,max_move_rows=10000):
    if type(months_ahead) is not int or not 0<=months_ahead<=24:raise ValueError('months_ahead must be 0..24')
    if type(max_move_rows) is not int or not 1<=max_move_rows<=1000000:raise ValueError('max_move_rows must be 1..1000000')
    if repo.engine.dialect.name!='postgresql':return {'status':'NOT_APPLICABLE','partitions':[]}
    current=month_start(now_ms() if now is None else now);result=[]
    for parent in PARENTS:
        with repo.engine.connect() as connection:
            if kind(connection,parent)!='p':
                result.append({'table':parent,'status':'NOT_PARTITIONED'});continue
            # Include actual months in DEFAULT, e.g. a historical replay. The
            # limit bounds automatic creation per cycle even for a large backlog.
            fallback=connection.scalars(text("SELECT DISTINCT (extract(epoch FROM date_trunc('month',to_timestamp(event_time/1000.0) AT TIME ZONE 'UTC'))*1000)::bigint FROM "+parent+'_default ORDER BY 1 LIMIT 12')).all()
        targets=sorted({*(shift_month(current,offset) for offset in range(-1,months_ahead+1)),*fallback})
        for start in targets:
            try:
                with repo.engine.begin() as connection:
                    connection.execute(text("SET LOCAL lock_timeout = '2s'"))
                    connection.execute(text("SET LOCAL statement_timeout = '30s'"))
                    if not connection.scalar(text('SELECT pg_try_advisory_xact_lock(:key)'),{'key':LOCK_ID}):
                        result.append({'table':parent,'status':'BUSY'});break
                    result.append({'table':parent,**ensure_month(connection,parent,start,max_move_rows)})
            except DBAPIError as exc:
                if getattr(exc.orig,'sqlstate',None) not in {'55P03','57014'}:raise
                result.append({'table':parent,'status':'BUSY'});break
    pending=any(row['status'] in {'BUSY','DEFERRED','NOT_PARTITIONED'} for row in result)
    return {'status':'PENDING' if pending else 'HEALTHY','partitions':result}


def inventory(repo):
    if repo.engine.dialect.name!='postgresql':return {'status':'NOT_APPLICABLE','tables':[]}
    with repo.engine.connect() as connection:
        return {'status':'AVAILABLE','tables':[{'table':parent,'kind':kind(connection,parent),'partitions':children(connection,parent)} for parent in PARENTS]}


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('command',choices=['list','ensure'])
    parser.add_argument('--months-ahead',type=int,default=int(os.getenv('PARTITION_MONTHS_AHEAD','3')))
    parser.add_argument('--max-move-rows',type=int,default=int(os.getenv('PARTITION_MAX_MOVE_ROWS','10000')))
    args=parser.parse_args();repo=Repository()
    result=inventory(repo) if args.command=='list' else ensure_partitions(repo,months_ahead=args.months_ahead,max_move_rows=args.max_move_rows)
    print(json.dumps(result,indent=2))


if __name__=='__main__':main()
