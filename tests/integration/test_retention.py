from unittest.mock import patch
import pytest
from sqlalchemy import event,select,update
from fastapi.testclient import TestClient
from backend.api.main import create_app
from backend.models.repository import Repository
from backend.models.schema import ArchiveBatch,Current,Event,ParameterSet,Signal,Snapshot
from backend.retention import DAY_MS,Policy,Retention,read_archive

NOW=1789430400000


@pytest.fixture
def setup(tmp_path):
    repo=Repository(f'sqlite:///{tmp_path}/retention.db');repo.initialize()
    manager=Retention(repo,tmp_path/'archives')
    for index,timestamp in enumerate([NOW-40*DAY_MS,NOW-31*DAY_MS,NOW-30*DAY_MS,NOW]):
        repo.save_snapshot({'exchange':'BYBIT','symbol':'TESTUSDT','timeframe':'15',
            'event_time':timestamp,'bar_start':timestamp,'confirmed':True,'setup_generation_id':f'g{index}',
            'action':'WATCH','fsm':index,'direction':'LONG','signals':['BRONZE'] if index==0 else [],
            'data_health':'RECOVERING','price':1.2345678901234567,
            'metrics':{'null':None,'bool':True,'series':[.1+.2,None],'label':'Зона'},
            'parameter_set_id':repo.parameters()['id']},{'varip':{'count':index},'persistent_zones':[1,2]})
    return repo,manager


def rows(repo,model):
    with repo.engine.connect() as connection:
        return [dict(row) for row in connection.execute(select(model.__table__).order_by(*model.__table__.primary_key.columns)).mappings()]


def test_preview_changes_no_rows_or_files_and_cutoff_is_exclusive(setup):
    repo,manager=setup;before=rows(repo,Snapshot)
    result=manager.cycle(Policy(),NOW)
    assert result['tables']['setup_snapshots']['eligible_rows']==2
    assert result['tables']['setup_events']['eligible_rows']==0
    assert result['archived_rows']==0
    assert rows(repo,Snapshot)==before and not rows(repo,ArchiveBatch)
    assert not manager.root.exists()
    assert manager.preview(Policy(snapshot_days=0,event_days=0),NOW)['tables']=={}


def test_lossless_archive_restore_keeps_live_state_and_deduplication(setup):
    repo,manager=setup
    before={model:rows(repo,model) for model in (Snapshot,Event,Current,Signal,ParameterSet)}
    checkpoint=repo.load_checkpoint('BYBIT','TESTUSDT','15')
    result=manager.cycle(Policy(mode='archive',event_days=30,batch_rows=1,max_batches=100),NOW)
    assert result['archived_rows']>2
    assert len(rows(repo,Snapshot))==2
    for model in (Current,Signal,ParameterSet):assert rows(repo,model)==before[model]
    assert repo.load_checkpoint('BYBIT','TESTUSDT','15')==checkpoint
    catalogue=rows(repo,ArchiveBatch)
    assert catalogue
    for record in catalogue:
        header,data=read_archive(manager.root/record['file_path'],record['sha256'])
        assert header['id']==record['id'] and len(data)==1
        assert manager.restore(record['id'])['inserted_rows']==1
        assert manager.restore(record['id'])['already_present']==1
    for model in (Snapshot,Event,Current,Signal,ParameterSet):assert rows(repo,model)==before[model]
    # Replaying an old detector event cannot duplicate it after retention.
    payload={**before[Signal][0]['payload'],'confirmed':False}
    repo.save_snapshot(payload)
    assert rows(repo,Signal)==before[Signal]
    with TestClient(create_app(repo)) as client:
        response=client.get('/api/storage?limit=1')
        assert response.status_code==200 and len(response.json()['archives'])==1
        assert response.json()['archives'][0]['restored_at'] is not None


def test_archive_cycle_has_bounded_work(setup):
    repo,manager=setup
    policy=Policy(mode='archive',event_days=0,batch_rows=1,max_batches=1)
    assert manager.cycle(policy,NOW)['archived_rows']==1
    assert len(rows(repo,Snapshot))==3
    assert manager.cycle(policy,NOW)['archived_rows']==1
    assert manager.cycle(policy,NOW)['archived_rows']==0
    assert [row['event_time'] for row in rows(repo,Snapshot)]==[NOW-30*DAY_MS,NOW]


def test_disk_failure_never_deletes_history(setup):
    repo,manager=setup;before=rows(repo,Snapshot)
    with patch.object(manager,'write_archive',side_effect=OSError('disk full')):
        with pytest.raises(OSError,match='disk full'):
            manager.archive_batch('setup_snapshots',NOW,250)
    assert rows(repo,Snapshot)==before and not rows(repo,ArchiveBatch)


def test_database_failure_leaves_history_and_recoverable_orphan(setup):
    repo,manager=setup;before=rows(repo,Snapshot)
    def fail_delete(connection,cursor,statement,parameters,context,executemany):
        if statement.startswith('DELETE FROM setup_snapshots'):raise RuntimeError('simulated rollback')
    event.listen(repo.engine,'before_cursor_execute',fail_delete)
    try:
        with pytest.raises(RuntimeError,match='simulated rollback'):
            manager.archive_batch('setup_snapshots',NOW,250)
    finally:event.remove(repo.engine,'before_cursor_execute',fail_delete)
    assert rows(repo,Snapshot)==before and not rows(repo,ArchiveBatch)
    assert len(list(manager.root.rglob('*.jsonl.gz')))==1
    assert manager.archive_batch('setup_snapshots',NOW,250)['rows']==3
    assert len(rows(repo,ArchiveBatch))==1


def test_corrupt_archive_fails_restore_and_keeps_catalogue(setup):
    repo,manager=setup
    record=manager.archive_batch('setup_snapshots',NOW,250)
    catalog=rows(repo,ArchiveBatch)[0]
    path=manager.root/catalog['file_path'];path.write_bytes(path.read_bytes()+b'corrupted')
    before=rows(repo,Snapshot)
    with pytest.raises(ValueError,match='SHA-256'):
        manager.restore(record['archive_id'])
    assert rows(repo,Snapshot)==before
    assert rows(repo,ArchiveBatch)[0]['restored_at'] is None


def test_restore_conflict_rolls_back_entire_batch(setup):
    repo,manager=setup
    record=manager.archive_batch('setup_snapshots',NOW,250)
    manager.restore(record['archive_id'])
    with repo.engine.begin() as connection:
        connection.execute(update(Snapshot).where(Snapshot.id==1).values(price=999))
    before=rows(repo,Snapshot)
    with pytest.raises(ValueError,match='differs'):
        manager.restore(record['archive_id'])
    assert rows(repo,Snapshot)==before


@pytest.mark.parametrize('kwargs',[{'mode':'delete'},{'snapshot_days':-1},{'event_days':True},
    {'batch_rows':0},{'batch_rows':2001},{'max_batches':101},{'interval_seconds':0}])
def test_invalid_retention_policy_rejected(kwargs):
    with pytest.raises(ValueError):Policy(**kwargs)


def test_catalogue_path_must_stay_inside_archive_directory(setup):
    repo,manager=setup;record=manager.archive_batch('setup_snapshots',NOW,250)
    with repo.engine.begin() as connection:
        connection.execute(update(ArchiveBatch).values(file_path='../outside.jsonl.gz'))
    with pytest.raises(ValueError,match='escapes'):manager.restore(record['archive_id'])
