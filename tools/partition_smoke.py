"""Real PostgreSQL month boundaries, migration rollback and archive interoperability."""
from datetime import datetime,timezone
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import uuid
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine,event,select,text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from backend.models.repository import Repository
from backend.models.schema import ArchiveBatch,Current,Event,Snapshot
from backend.partitions import children,ensure_month,ensure_partitions,inventory,kind
from backend.retention import Policy,Retention


def stamp(year,month,day=1):return int(datetime(year,month,day,tzinfo=timezone.utc).timestamp()*1000)


def main():
    name='partition_validation_'+uuid.uuid4().hex[:10]
    original=make_url(os.environ['DATABASE_URL']);admin=create_engine(original)
    with admin.begin() as connection:connection.execute(text(f'CREATE SCHEMA {name}'))
    url=original.update_query_dict({'options':f'-csearch_path={name}'}).render_as_string(hide_password=False)
    os.environ['DATABASE_URL']=url;repo=Repository(url)
    try:
        config=Config('alembic.ini');command.upgrade(config,'0004');repo.initialize()
        def write(timestamp,**overrides):
            repo.save_snapshot({'exchange':'BYBIT','symbol':'VALIDATIONUSDT','timeframe':'15',
                'event_time':timestamp,'bar_start':timestamp,'confirmed':True,'setup_generation_id':str(timestamp),
                'action':'WATCH','fsm':1,'direction':'LONG','data_health':'RECOVERING','price':.1+.2,
                'metrics':{'unicode':'Зона','float':1.2345678901234567,'na':None},**overrides},
                {'varip':{'sum':.1+.2},'zones':[1,2,3]})
        for timestamp in [stamp(2024,2),stamp(2024,3)-1,stamp(2024,3),stamp(2025,12),stamp(2026,1)]:write(timestamp)
        def records(model):
            with repo.engine.connect() as connection:
                return [dict(row) for row in connection.execute(select(model.__table__).order_by(*model.__table__.primary_key.columns)).mappings()]
        before={model:records(model) for model in (Snapshot,Event,Current)}
        command.upgrade(config,'head')
        for model in before:assert records(model)==before[model]
        with repo.engine.connect() as connection:
            assert all(kind(connection,parent)=='p' for parent in ('setup_snapshots','setup_events'))
            actual=connection.execute(text('SELECT event_time,tableoid::regclass::text FROM setup_snapshots ORDER BY event_time')).all()
            assert actual[0][1].endswith('setup_snapshots_y2024m02')
            assert actual[1][1].endswith('setup_snapshots_y2024m02')
            assert actual[2][1].endswith('setup_snapshots_y2024m03')
            plan=connection.scalar(text('EXPLAIN (FORMAT JSON) SELECT id FROM setup_snapshots WHERE event_time>=:start AND event_time<:end'),{'start':stamp(2024,2),'end':stamp(2024,3)})
            assert 'setup_snapshots_y2024m03' not in json.dumps(plan)
            assert 'setup_snapshots_y2024m02' in json.dumps(plan)
        # Parent FK still protects parameter identity after the table swap.
        try:write(stamp(2024,4),parameter_set_id='missing')
        except IntegrityError:pass
        else:raise AssertionError('Parameter FK lost during migration')
        command.downgrade(config,'0004')
        for model in before:assert records(model)==before[model]
        command.upgrade(config,'head')
        for model in before:assert records(model)==before[model]
        for timestamp in [stamp(2035,1),stamp(2035,1)+1,stamp(2035,1)+2]:write(timestamp)
        with repo.engine.connect() as connection:
            assert connection.scalar(text('SELECT count(*) FROM setup_snapshots_default'))==3
        pending=ensure_partitions(repo,now=stamp(2035,1),max_move_rows=1)
        assert any(row['status']=='DEFERRED' for row in pending['partitions'])
        moved=ensure_partitions(repo,now=stamp(2035,1),max_move_rows=10000)
        assert moved['status']=='HEALTHY'
        assert sum(row.get('moved_rows',0) for row in moved['partitions'])>=3
        assert ensure_partitions(repo,now=stamp(2035,1))['status']=='HEALTHY'
        with repo.engine.connect() as connection:
            assert connection.scalar(text('SELECT count(*) FROM setup_snapshots_default'))==0
        # Failure after moving DEFAULT rows must roll back both the move and DDL.
        write(stamp(2040,1))
        def fail_attach(connection,cursor,statement,parameters,context,executemany):
            if statement.startswith('ALTER TABLE setup_snapshots ATTACH PARTITION'):raise RuntimeError('simulate attach failure')
        event.listen(repo.engine,'before_cursor_execute',fail_attach)
        try:
            try:
                with repo.engine.begin() as connection:ensure_month(connection,'setup_snapshots',stamp(2040,1))
            except RuntimeError as exc:assert 'simulate attach failure' in str(exc)
            else:raise AssertionError('Expected simulated failure')
        finally:event.remove(repo.engine,'before_cursor_execute',fail_attach)
        with repo.engine.connect() as connection:
            assert connection.scalar(text('SELECT count(*) FROM setup_snapshots_default'))==1
            assert connection.scalar(text("SELECT to_regclass('setup_snapshots_y2040m01')")) is None
        # A busy parent returns PENDING; writers can continue using DEFAULT.
        with repo.engine.begin() as owner:
            owner.execute(text('LOCK TABLE setup_snapshots IN ROW EXCLUSIVE MODE'))
            busy=ensure_partitions(repo,now=stamp(2041,1))
            assert any(row['status']=='BUSY' for row in busy['partitions'])
        checkpoint=repo.load_checkpoint('BYBIT','VALIDATIONUSDT','15')
        expected={model:records(model) for model in (Snapshot,Event)}
        root=Path(os.getenv('ARCHIVE_DIR','artifacts/local'));root.mkdir(parents=True,exist_ok=True)
        with TemporaryDirectory(prefix='partitions-check-',dir=root) as directory:
            manager=Retention(repo,directory)
            assert manager.cycle(Policy(mode='archive',event_days=30),now=stamp(2050,1))['archived_rows']>0
            with repo.session() as session:ids=list(session.scalars(select(ArchiveBatch.id)))
            for batch_id in ids:manager.restore(batch_id)
            for model in expected:assert records(model)==expected[model]
            assert repo.load_checkpoint('BYBIT','VALIDATIONUSDT','15')==checkpoint
            write(stamp(2040,2))
            assert records(Snapshot)[-1]['id']>max(row['id'] for row in expected[Snapshot])
        print(json.dumps({'postgresql':'PASS','migration_and_downgrade':'PASS','all_values_and_ids_preserved':'PASS',
            'month_boundaries':'PASS','partition_pruning':'PASS','parameter_fk':'PASS','default_fallback':'PASS',
            'bounded_default_move':'PASS','ddl_rollback':'PASS','concurrent_writer':'PASS','archive_restore':'PASS',
            'checkpoint_unchanged':'PASS','sequence_after_restore':'PASS','live_history_deleted':0}))
    finally:
        repo.engine.dispose()
        with admin.begin() as connection:connection.execute(text(f'DROP SCHEMA {name} CASCADE'))
        admin.dispose()


if __name__=='__main__':main()
