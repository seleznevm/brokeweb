from datetime import datetime,timezone
import pytest
from backend.partitions import month_start,shift_month,child_name,ensure_partitions
from backend.models.repository import Repository


def ts(year,month,day=1):
    return int(datetime(year,month,day,tzinfo=timezone.utc).timestamp()*1000)


def test_partition_calendar_handles_leap_year_and_year_rollover():
    assert month_start(ts(2024,2,29)+86399999)==ts(2024,2)
    assert shift_month(ts(2024,2),1)==ts(2024,3)
    assert shift_month(ts(2024,12),1)==ts(2025,1)
    assert shift_month(ts(2025,1),-1)==ts(2024,12)
    assert shift_month(ts(2025,3),-13)==ts(2024,2)
    assert child_name('setup_snapshots',ts(2024,2))=='setup_snapshots_y2024m02'


def test_partition_maintenance_does_not_change_sqlite_test_schema():
    repo=Repository('sqlite:///:memory:');repo.initialize()
    assert ensure_partitions(repo)=={'status':'NOT_APPLICABLE','partitions':[]}


@pytest.mark.parametrize('kwargs',[{'months_ahead':-1},{'months_ahead':25},
    {'months_ahead':True},{'max_move_rows':0},{'max_move_rows':1000001}])
def test_partition_limits_rejected_before_ddl(kwargs):
    with pytest.raises(ValueError):ensure_partitions(Repository('sqlite:///:memory:'),**kwargs)
