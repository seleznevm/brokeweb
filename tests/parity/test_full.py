import csv
import pytest
from tools.pine_reference.full import inspect
from tools.pine_reference.catalog import METRICS
from backend.engine.runtime import SIGNALS


def export(tmp_path):
    rows=[]
    for i in range(3):
        r={'time':str(1800*(i+1)),'open':'1','high':'1','low':'1','close':'1','Volume':'100'}
        r.update({'PARITY_'+k:'1' for k in [*METRICS,*SIGNALS.values()]})
        for g in ('metrics','signals'):
            for k,v in dict(history_start=0,bar_index=i+1,tick_size=.01,confirmed=int(i<2),volume=100,timeframe_seconds=1800).items():r['PARITY_META_'+g+'_'+k]=str(v)
        rows.append(r)
    path=tmp_path/'BYBIT_ETHFIUSDT.P, 30_test.csv'
    return path,rows


def write(path,rows):
    with path.open('w',newline='') as f:
        w=csv.DictWriter(f,fieldnames=rows[0]);w.writeheader();w.writerows(rows)


def test_open_last_bar_can_differ_between_two_live_scripts(tmp_path):
    path,rows=export(tmp_path);rows[-1]['PARITY_META_signals_volume']='101';write(path,rows)
    symbol,tf,all_rows,closed,origin,tick=inspect(path)
    assert (symbol,tf,len(all_rows),len(closed),origin,tick)==('ETHFIUSDT','30',3,2,0,.01)


@pytest.mark.parametrize('field,value',[('volume','101'),('bar_index','300'),('history_start','1')])
def test_closed_metadata_must_match(tmp_path,field,value):
    path,rows=export(tmp_path);rows[0]['PARITY_META_signals_'+field]=value;write(path,rows)
    with pytest.raises(ValueError):inspect(path)


def test_matching_but_wrong_history_index_rejected(tmp_path):
    path,rows=export(tmp_path)
    for g in ('metrics','signals'):rows[0]['PARITY_META_'+g+'_bar_index']='200'
    write(path,rows)
    with pytest.raises(ValueError,match='index mismatch'):inspect(path)
