"""Lossless, bounded archive-before-delete for immutable analytical history."""
from __future__ import annotations
import argparse
import asyncio
from dataclasses import asdict,dataclass
from datetime import datetime,timezone
import gzip
import hashlib
import json
import logging
import os
from pathlib import Path
import signal
import time
import uuid
from sqlalchemy import delete,func,insert,select,text
from backend.models.repository import Repository,now_ms
from backend.models.schema import ArchiveBatch,Event,Snapshot
from backend.logging_config import configure_logging

log=logging.getLogger(__name__)
TABLES={'setup_snapshots':Snapshot.__table__,'setup_events':Event.__table__}
DAY_MS=86400000
LOCK_ID=431580


@dataclass(frozen=True)
class Policy:
    mode: str='preview'
    snapshot_days: int=30
    event_days: int=90
    batch_rows: int=250
    max_batches: int=8
    interval_seconds: int=3600

    def __post_init__(self):
        if self.mode not in {'preview','archive'}:raise ValueError('RETENTION_MODE must be preview or archive')
        for value in (self.snapshot_days,self.event_days,self.batch_rows,self.max_batches,self.interval_seconds):
            if type(value) is not int:raise ValueError('Retention limits must be integers')
        if min(self.snapshot_days,self.event_days)<0:raise ValueError('Retention days must be nonnegative; 0 disables a table')
        if not 1<=self.batch_rows<=2000 or not 1<=self.max_batches<=100:raise ValueError('Retention batch limit out of bounds')
        if self.interval_seconds<60:raise ValueError('Retention interval must be >= 60 seconds')

    @classmethod
    def from_env(cls):
        return cls(mode=os.getenv('RETENTION_MODE','preview'),
                   snapshot_days=int(os.getenv('SNAPSHOT_RETENTION_DAYS','30')),
                   event_days=int(os.getenv('EVENT_RETENTION_DAYS','90')),
                   batch_rows=int(os.getenv('RETENTION_BATCH_ROWS','250')),
                   max_batches=int(os.getenv('RETENTION_MAX_BATCHES','8')),
                   interval_seconds=int(os.getenv('RETENTION_INTERVAL_SEC','3600')))

    def cutoffs(self,now):
        return {name:now-days*DAY_MS for name,days in
                [('setup_snapshots',self.snapshot_days),('setup_events',self.event_days)] if days}


def file_hash(path):
    with path.open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()


def read_archive(path,expected_hash):
    if file_hash(path)!=expected_hash:raise ValueError('Archive SHA-256 mismatch')
    with gzip.open(path,'rt',encoding='utf-8') as stream:
        header=json.loads(next(stream))
        if header.get('format')!='brokeweb.archive' or header.get('version')!=1:
            raise ValueError('Unsupported archive format')
        table=TABLES.get(header.get('table'))
        if table is None or header.get('columns')!=list(table.columns.keys()):
            raise ValueError('Archive table/schema mismatch')
        rows=[json.loads(line) for line in stream]
    if len(rows)!=header['rows'] or not rows:raise ValueError('Archive row count mismatch')
    if any(set(row)!=set(header['columns']) for row in rows):raise ValueError('Archive row schema mismatch')
    if len({row['id'] for row in rows})!=len(rows):raise ValueError('Duplicate archive row identity')
    return header,rows


class Retention:
    def __init__(self,repo,archive_dir):
        self.repo=repo;self.root=Path(archive_dir).resolve()

    def preview(self,policy,now=None):
        now=now_ms() if now is None else now
        tables={}
        with self.repo.engine.connect() as connection:
            for name,cutoff in policy.cutoffs(now).items():
                table=TABLES[name]
                count,oldest,newest=connection.execute(select(func.count(),func.min(table.c.event_time),func.max(table.c.event_time)).where(table.c.event_time<cutoff)).one()
                tables[name]={'cutoff':cutoff,'eligible_rows':count,'oldest_event':oldest,'newest_event':newest}
        return {'policy':asdict(policy),'tables':tables,'archive_dir':str(self.root),'checked_at':now}

    def write_archive(self,name,rows,cutoff):
        batch_id=str(uuid.uuid4());created=now_ms()
        relative=Path(name)/datetime.fromtimestamp(created/1000,timezone.utc).strftime('%Y-%m')/(batch_id+'.jsonl.gz')
        target=self.root/relative;target.parent.mkdir(parents=True,exist_ok=True)
        temporary=target.with_suffix('.partial')
        header={'format':'brokeweb.archive','version':1,'id':batch_id,'table':name,
                'columns':list(TABLES[name].columns.keys()),'rows':len(rows),'created_at':created,'cutoff':cutoff}
        try:
            with temporary.open('xb') as raw:
                with gzip.GzipFile(fileobj=raw,mode='wb',compresslevel=1,mtime=0) as stream:
                    for value in (header,*rows):
                        stream.write((json.dumps(value,ensure_ascii=False,allow_nan=False,separators=(',',':'))+'\n').encode('utf-8'))
                raw.flush();os.fsync(raw.fileno())
            digest=file_hash(temporary)
            _,verified=read_archive(temporary,digest)
            if verified!=rows:raise ValueError('Archive verification changed row values')
            os.replace(temporary,target)
            # Flush directory metadata before committing deletion from PostgreSQL.
            # Unsupported durable filesystem operations fail without deleting DB rows.
            for directory in (target.parent,target.parent.parent,self.root,self.root.parent):
                descriptor=os.open(directory,os.O_RDONLY|os.O_DIRECTORY)
                try:os.fsync(descriptor)
                finally:os.close(descriptor)
        finally:
            temporary.unlink(missing_ok=True)
        return {'id':batch_id,'table_name':name,'created_at':created,'cutoff':cutoff,
                'first_event':min(row['event_time'] for row in rows),'last_event':max(row['event_time'] for row in rows),
                'row_count':len(rows),'file_path':relative.as_posix(),'sha256':digest,'file_bytes':target.stat().st_size}

    def archive_batch(self,name,cutoff,limit):
        table=TABLES[name]
        if not 1<=limit<=2000:raise ValueError('Invalid archive batch size')
        with self.repo.engine.begin() as connection:
            if connection.dialect.name=='postgresql':
                connection.execute(text("SET LOCAL lock_timeout = '2s'"))
                if not connection.scalar(text('SELECT pg_try_advisory_xact_lock(:key)'),{'key':LOCK_ID}):
                    return {'status':'BUSY','rows':0}
            rows=[dict(row) for row in connection.execute(select(table).where(table.c.event_time<cutoff).order_by(table.c.event_time,table.c.id).limit(limit).with_for_update(skip_locked=True)).mappings()]
            if not rows:return {'status':'EMPTY','rows':0}
            record=self.write_archive(name,rows,cutoff)
            connection.execute(insert(ArchiveBatch).values(**record))
            result=connection.execute(delete(table).where(table.c.id.in_([row['id'] for row in rows])))
            if result.rowcount!=len(rows):raise RuntimeError('Archive deletion row count changed; rolling back')
        return {'status':'ARCHIVED','rows':len(rows),'archive_id':record['id'],'file_bytes':record['file_bytes']}

    def cycle(self,policy,now=None):
        now=now_ms() if now is None else now
        result=self.preview(policy,now);result.update(archived_rows=0,archived_bytes=0,batches=[])
        if policy.mode=='archive':
            for name,cutoff in policy.cutoffs(now).items():
                for _ in range(policy.max_batches):
                    batch=self.archive_batch(name,cutoff,policy.batch_rows)
                    result['batches'].append({'table':name,**batch})
                    result['archived_rows']+=batch['rows'];result['archived_bytes']+=batch.get('file_bytes',0)
                    if batch['status']!='ARCHIVED':break
        result['finished_at']=now_ms()
        return result

    def scheduled_cycle(self,policy):
        from backend.partitions import ensure_partitions
        partitions=ensure_partitions(self.repo,
            months_ahead=int(os.getenv('PARTITION_MONTHS_AHEAD','3')),
            max_move_rows=int(os.getenv('PARTITION_MAX_MOVE_ROWS','10000')))
        result=self.cycle(policy);result['partitions']=partitions
        return result

    def restore(self,batch_id):
        with self.repo.engine.begin() as connection:
            if connection.dialect.name=='postgresql':
                if not connection.scalar(text('SELECT pg_try_advisory_xact_lock(:key)'),{'key':LOCK_ID}):
                    raise RuntimeError('Archive/restore already running; retry later')
            record=connection.execute(select(ArchiveBatch.__table__).where(ArchiveBatch.id==batch_id).with_for_update()).mappings().one()
            path=(self.root/record['file_path']).resolve()
            if not path.is_relative_to(self.root):raise ValueError('Archive path escapes configured directory')
            header,rows=read_archive(path,record['sha256'])
            if header['id']!=batch_id or header['table']!=record['table_name'] or len(rows)!=record['row_count']:
                raise ValueError('Archive catalogue identity mismatch')
            table=TABLES[header['table']]
            if connection.dialect.name=='postgresql':
                connection.execute(text("SET LOCAL lock_timeout = '2s'"))
                # A restore is explicit and bounded. Serialize inserts while
                # restoring IDs and advancing the sequence, so setval cannot
                # race another writer's nextval and rewind its allocation.
                connection.execute(text('LOCK TABLE '+table.name+' IN SHARE ROW EXCLUSIVE MODE'))
            existing={row['id']:dict(row) for row in connection.execute(select(table).where(table.c.id.in_([r['id'] for r in rows]))).mappings()}
            if any(existing[row['id']]!=row for row in rows if row['id'] in existing):
                raise ValueError('Existing row differs from archive; restore rolled back')
            missing=[row for row in rows if row['id'] not in existing]
            if missing:connection.execute(insert(table),missing)
            # In the original DB sequences already exceed archived IDs. Imports
            # into a fresh schema must not leave the sequence behind restored rows.
            if connection.dialect.name=='postgresql':
                sequence=connection.scalar(text('SELECT pg_get_serial_sequence(:table, :column)'),{'table':table.name,'column':'id'})
                if sequence:
                    connection.execute(text('SELECT setval(CAST(:sequence AS regclass), GREATEST((SELECT max(id) FROM '+table.name+'), nextval(CAST(:sequence AS regclass))), true)'),{'sequence':sequence})
            connection.execute(ArchiveBatch.__table__.update().where(ArchiveBatch.id==batch_id).values(restored_at=now_ms()))
        return {'status':'RESTORED','archive_id':batch_id,'inserted_rows':len(missing),'already_present':len(existing)}


async def serve(retention,policy):
    stop=asyncio.Event();loop=asyncio.get_running_loop()
    for sig in (signal.SIGTERM,signal.SIGINT):loop.add_signal_handler(sig,stop.set)
    task=None;next_run=0;last_result=None;error=None
    try:
        while not stop.is_set():
            if task is not None and task.done():
                try:last_result=task.result();error=None
                except Exception as exc:error=str(exc);log.exception('retention_cycle_failed')
                task=None;next_run=time.monotonic()+policy.interval_seconds
            if task is None and time.monotonic()>=next_run:
                task=asyncio.create_task(asyncio.to_thread(retention.scheduled_cycle,policy))
            await asyncio.to_thread(retention.repo.heartbeat,'retention',{'status':'DEGRADED' if error else 'HEALTHY',
                'mode':policy.mode,'running':task is not None,'policy':asdict(policy),'error':error,'last_result':last_result})
            try:await asyncio.wait_for(stop.wait(),timeout=10)
            except TimeoutError:pass
    finally:
        if task is not None:
            # Do not detach the transaction's thread on process shutdown.
            try:await task
            except Exception:log.exception('retention_shutdown_cycle_failed')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=['preview','archive','restore','serve'])
    parser.add_argument('--archive-id')
    args=parser.parse_args();policy=Policy.from_env()
    retention=Retention(Repository(),os.getenv('ARCHIVE_DIR','data/archives'))
    if args.command=='serve':configure_logging();asyncio.run(serve(retention,policy))
    elif args.command=='preview':print(json.dumps(retention.preview(policy),indent=2))
    elif args.command=='archive':
        if policy.mode!='archive':parser.error('Set RETENTION_MODE=archive after reviewing preview and archive storage')
        print(json.dumps(retention.cycle(policy),indent=2))
    else:
        if not args.archive_id:parser.error('restore requires --archive-id')
        print(json.dumps(retention.restore(args.archive_id),indent=2))


if __name__=='__main__':main()
