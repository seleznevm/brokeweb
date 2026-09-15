"""PostgreSQL migration/archive/restore checks in an isolated temporary schema."""
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import uuid
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine,insert,select,text
from sqlalchemy.engine import make_url
from backend.models.repository import Repository
from backend.models.schema import ArchiveBatch,Current,Snapshot
from backend.retention import DAY_MS,LOCK_ID,Policy,Retention


def main():
    name='retention_validation_'+uuid.uuid4().hex[:10]
    original=make_url(os.environ['DATABASE_URL']);admin=create_engine(original)
    with admin.begin() as connection:connection.execute(text(f'CREATE SCHEMA {name}'))
    url=original.update_query_dict({'options':f'-csearch_path={name}'}).render_as_string(hide_password=False)
    os.environ['DATABASE_URL']=url;repo=Repository(url)
    try:
        config=Config('alembic.ini')
        command.upgrade(config,'0003')
        # Initial metadata includes new tables for fresh installations. Remove
        # only those new empty objects to exercise upgrading a real older schema.
        with repo.engine.begin() as connection:
            connection.execute(text('DROP TABLE archive_batches'))
            connection.execute(text('DROP INDEX ix_snapshot_retention'))
            connection.execute(text('DROP INDEX ix_event_retention'))
        command.upgrade(config,'head');repo.initialize()
        with repo.engine.connect() as connection:
            assert connection.scalar(text("SELECT count(*) FROM pg_index i JOIN pg_class c ON c.oid=i.indexrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=current_schema() AND c.relname IN ('ix_snapshot_retention','ix_event_retention') AND i.indisvalid"))==2
        now=1789430400000
        for index in range(4):
            repo.save_snapshot({'exchange':'BYBIT','symbol':'VALIDATIONUSDT','timeframe':'15',
                'event_time':now-(40-index)*DAY_MS,'bar_start':index,'confirmed':True,
                'setup_generation_id':'g1','action':'WATCH','fsm':1,'direction':'LONG',
                'data_health':'RECOVERING','price':.1+.2,'metrics':{'unicode':'Зона','na':None}},
                {'varip':{'samples':index},'zones':[1.2345678901234567]})
        def records():
            with repo.engine.connect() as connection:
                return [dict(row) for row in connection.execute(select(Snapshot.__table__).order_by(Snapshot.id)).mappings()]
        before=records();checkpoint=repo.load_checkpoint('BYBIT','VALIDATIONUSDT','15')
        root=Path(os.getenv('ARCHIVE_DIR','artifacts/local'));root.mkdir(parents=True,exist_ok=True)
        with TemporaryDirectory(prefix='retention-check-',dir=root) as directory:
            manager=Retention(repo,directory)
            assert manager.cycle(Policy(),now)['archived_rows']==0
            # A second connection cannot archive while another owner holds the lock.
            with repo.engine.begin() as owner:
                owner.execute(text('SELECT pg_advisory_xact_lock(:key)'),{'key':LOCK_ID})
                assert manager.archive_batch('setup_snapshots',now,2)['status']=='BUSY'
            result=manager.cycle(Policy(mode='archive',event_days=0,batch_rows=2,max_batches=2),now)
            assert result['archived_rows']==4 and not records()
            assert repo.load_checkpoint('BYBIT','VALIDATIONUSDT','15')==checkpoint
            with repo.session() as session:ids=list(session.scalars(select(ArchiveBatch.id)))
            try:command.downgrade(config,'0003')
            except RuntimeError as exc:assert 'catalogue' in str(exc)
            else:raise AssertionError('Downgrade discarded a nonempty archive catalogue')
            for batch_id in ids:assert manager.restore(batch_id)['inserted_rows']==2
            assert records()==before
            for batch_id in ids:assert manager.restore(batch_id)['inserted_rows']==0
            repo.save_snapshot({'symbol':'VALIDATIONUSDT','timeframe':'15','event_time':now,
                                'confirmed':True,'action':'WAIT SETUP','data_health':'RECOVERING'})
            assert records()[-1]['id']>max(row['id'] for row in before)
            # One corrupted file must not mutate restored records or erase catalogue.
            with repo.session() as session:batch=session.get(ArchiveBatch,ids[0])
            path=Path(directory)/batch.file_path;path.write_bytes(path.read_bytes()+b'bad')
            try:manager.restore(ids[0])
            except ValueError as exc:assert 'SHA-256' in str(exc)
            else:raise AssertionError('Corrupted archive accepted')
        print(json.dumps({'postgresql':'PASS','upgrade_from_0003':'PASS','concurrent_indexes':'PASS',
            'exclusive_archive_lock':'PASS','preview':'PASS','archive_restore':'PASS',
            'checkpoint_unchanged':'PASS','sequence_after_restore':'PASS','corruption_rejected':'PASS',
            'catalogue_downgrade_guard':'PASS',
            'live_history_deleted':0}))
    finally:
        repo.engine.dispose()
        with admin.begin() as connection:connection.execute(text(f'DROP SCHEMA {name} CASCADE'))
        admin.dispose()


if __name__=='__main__':main()
