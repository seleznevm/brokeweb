"""Compose PostgreSQL dump/restore with nonempty archives in disposable databases.

Uses running api/postgres containers. Never changes the application database,
rules or archive mount. Requires the local Compose PostgreSQL role to create DBs.
"""
import argparse
import base64
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import uuid


PREPARE = r'''
import base64, hashlib, json, os, sys
from pathlib import Path
from alembic import command
from alembic.config import Config
from sqlalchemy import select
from sqlalchemy.engine import make_url
from backend.models.repository import Repository, clean
from backend.models.schema import Base, ArchiveBatch, Snapshot, Event
from backend.retention import Retention, Policy, DAY_MS
database, directory = sys.argv[1:]
os.environ['DATABASE_URL'] = make_url(os.environ['DATABASE_URL']).set(database=database).render_as_string(hide_password=False)
command.upgrade(Config('alembic.ini'), 'head')
repo=Repository(); repo.initialize()
now=1790467200000
for i in range(4):
    repo.save_snapshot(dict(symbol='DRTESTUSDT',timeframe='30',event_time=now-(40-i)*DAY_MS,
        bar_start=i,confirmed=True,setup_generation_id=str(i),action='WATCH',fsm=1,
        direction='LONG',data_health='RECOVERING',signals=[],
        metrics={'unicode':'Зона','fraction':1.2345678901234567,'missing':None}),
        {'varip':{'samples':i},'frozen_plan':[.1+.2,123.45678901234567]})
def rows(model):
    with repo.engine.connect() as c:
        return [dict(r) for r in c.execute(select(model.__table__).order_by(*model.__table__.primary_key.columns)).mappings()]
original={m.__tablename__:rows(m) for m in (Snapshot,Event)}
manager=Retention(repo,directory)
result=manager.cycle(Policy(mode='archive',event_days=30,batch_rows=2,max_batches=100),now)
assert result['archived_rows']>0 and not rows(Snapshot) and not rows(Event)
files={}
for record in rows(ArchiveBatch):
    p=Path(directory)/record['file_path']
    files[record['file_path']]=base64.b64encode(p.read_bytes()).decode()
assert files
# All mapped tables, including bytea checkpoint, catalogue and dedupe/settings.
def fingerprint(table):
    with repo.engine.connect() as c:
        values=[dict(r) for r in c.execute(select(table)).mappings()]
    def default(v):
        if isinstance(v,(bytes,memoryview)):return {'hex':bytes(v).hex()}
        raise TypeError(type(v).__name__)
    encoded=sorted(json.dumps(clean(v),sort_keys=True,ensure_ascii=False,default=default) for v in values)
    return {'count':len(values),'sha256':hashlib.sha256('\n'.join(encoded).encode()).hexdigest()}
print(json.dumps({'original':original,'files':files,
    'tables':{t.name:fingerprint(t) for t in Base.metadata.sorted_tables},
    'checkpoint':repo.load_checkpoint('BYBIT','DRTESTUSDT','30')},ensure_ascii=False))
repo.engine.dispose()
'''

VERIFY = r'''
import base64, hashlib, json, os, sys
from pathlib import Path
from sqlalchemy import select
from sqlalchemy.engine import make_url
from backend.models.repository import Repository, clean
from backend.models.schema import Base, ArchiveBatch, Snapshot, Event
from backend.retention import Retention
database,directory,evidence_file=sys.argv[1:]
expected=json.loads(Path(evidence_file).read_text())
url=make_url(os.environ['DATABASE_URL']).set(database=database).render_as_string(hide_password=False)
repo=Repository(url)
def fingerprint(table):
    with repo.engine.connect() as c:
        values=[dict(r) for r in c.execute(select(table)).mappings()]
    def default(v):
        if isinstance(v,(bytes,memoryview)):return {'hex':bytes(v).hex()}
        raise TypeError(type(v).__name__)
    encoded=sorted(json.dumps(clean(v),sort_keys=True,ensure_ascii=False,default=default) for v in values)
    return {'count':len(values),'sha256':hashlib.sha256('\n'.join(encoded).encode()).hexdigest()}
actual={t.name:fingerprint(t) for t in Base.metadata.sorted_tables}
assert actual==expected['tables'], 'Dump restore changed database values'
with repo.session() as s: ids=list(s.scalars(select(ArchiveBatch.id)))
manager=Retention(repo,directory)
restored=sum(manager.restore(i)['inserted_rows'] for i in ids)
for model in (Snapshot,Event):
    with repo.engine.connect() as c:
        rows=[dict(r) for r in c.execute(select(model.__table__).order_by(*model.__table__.primary_key.columns)).mappings()]
    assert rows==expected['original'][model.__tablename__]
assert all(manager.restore(i)['inserted_rows']==0 for i in ids)
assert repo.load_checkpoint('BYBIT','DRTESTUSDT','30')==expected['checkpoint']
repo.save_snapshot(dict(symbol='DRTESTUSDT',timeframe='30',event_time=1790467200000,
    bar_start=5,confirmed=True,action='WAIT SETUP',data_health='RECOVERING'))
with repo.engine.connect() as c:
    latest=c.scalar(select(Snapshot.id).order_by(Snapshot.id.desc()).limit(1))
assert latest>max(r['id'] for r in expected['original']['setup_snapshots'])
print(json.dumps({'status':'PASS','tables_verified':len(actual),'archive_batches':len(ids),
    'archive_rows_restored':restored,'checkpoint_unchanged':True,'idempotent_restore':True,
    'sequence_after_restore':True,'live_database_modified':False,'telegram_sent':False}))
repo.engine.dispose()
'''


def compose(*args, data=None):
    return subprocess.run(['docker','compose',*args],input=data,stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE,check=True,timeout=180).stdout


def remote(script,*args):
    return compose('exec','-T','api','python','-',*args,data=script.encode())


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=Path('artifacts/local/disaster-recovery.json'))
    args=parser.parse_args()
    token=uuid.uuid4().hex[:12]
    source=f'brokeweb_dr_{token}_source';target=f'brokeweb_dr_{token}_target'
    source_dir=f'/tmp/{source}';target_dir=f'/tmp/{target}'
    created=[]
    try:
        for database in (source,target):
            compose('exec','-T','postgres','createdb','-U','brokeweb',database)
            created.append(database)
        evidence=json.loads(remote(PREPARE,source,source_dir))
        with tempfile.TemporaryDirectory(prefix='brokeweb-dr-') as local:
            dump=compose('exec','-T','postgres','pg_dump','-U','brokeweb','-Fc',source)
            backup=Path(local)/'database.dump';backup.write_bytes(dump)
            # Physically round-trip archive files through the backup location.
            for i,(name,encoded) in enumerate(evidence['files'].items()):
                file=Path(local)/f'archive-{i}.gz';file.write_bytes(base64.b64decode(encoded))
                evidence['files'][name]=base64.b64encode(file.read_bytes()).decode()
            compose('exec','-T','postgres','pg_restore','-U','brokeweb','--exit-on-error','-d',target,data=backup.read_bytes())
        # Remove the source before restoration: the target must stand alone.
        compose('exec','-T','postgres','dropdb','-U','brokeweb',source);created.remove(source)
        remote('import shutil,sys; shutil.rmtree(sys.argv[1])',source_dir)
        installer='''import base64,json,pathlib,sys
root=pathlib.Path(sys.argv[1]);root.mkdir()
evidence=json.loads(sys.argv[2])
for name,encoded in evidence['files'].items():
    path=root/name
    assert path.resolve().is_relative_to(root.resolve())
    path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(base64.b64decode(encoded))
(root/'evidence.json').write_text(json.dumps(evidence))
'''
        remote(installer,target_dir,json.dumps(evidence))
        result=json.loads(remote(VERIFY,target,target_dir,f'{target_dir}/evidence.json'))
        result.update(measured_at=datetime.now(timezone.utc).isoformat(),dump_bytes=len(dump),
            dump_sha256=hashlib.sha256(dump).hexdigest(),source_removed_before_archive_restore=True,
            scope='isolated fixture databases and copied nonempty archives; not a production-sized RTO/RPO test')
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text(json.dumps(result,indent=2)+'\n')
        print(json.dumps(result,indent=2))
    finally:
        for database in created:compose('exec','-T','postgres','dropdb','-U','brokeweb','--if-exists',database)
        remote('import shutil,sys; [shutil.rmtree(p,ignore_errors=True) for p in sys.argv[1:]]',source_dir,target_dir)


if __name__=='__main__':main()
