import csv
import pytest
from tools.pine_reference.context_capture import inspect_contexts, currency_observations
from tools.pine_reference.context_probe import FIELDS
from tools.pine_reference.catalog import METRICS


def export(tmp_path):
    path=tmp_path/'BYBIT_ETHFIUSDT.P, 30_probe.csv';rows=[]
    for i in range(3):
        row={'time':str(i*1800),'open':'1','high':'1','low':'1','close':'1','Volume':'100'}
        row.update({'PARITY_'+k:'1' for k in METRICS})
        row.update({'PARITY_CTX_'+k:'1' for k in FIELDS})
        row.update(PARITY_CTX_chart_start=str(i*1800000),PARITY_CTX_chart_end=str((i+1)*1800000),
                   PARITY_CTX_realtime=str(int(i==2)),PARITY_CTX_quote_usd_requested='.9998')
        for g in ('metrics','contexts'):
            for k,v in dict(history_start=0,bar_index=i,tick_size=.0001,confirmed=int(i<2),volume=100,timeframe_seconds=1800).items():
                row['PARITY_META_'+g+'_'+k]=str(v)
        rows.append(row)
    return path,rows


def write(path,rows):
    with path.open('w',newline='') as f:
        w=csv.DictWriter(f,fieldnames=rows[0]);w.writeheader();w.writerows(rows)


def test_contexts_capture_excludes_different_open_candle(tmp_path):
    path,rows=export(tmp_path);rows[-1]['PARITY_META_metrics_volume']='200';write(path,rows)
    symbol,tf,all_rows,closed,origin,tick=inspect_contexts(path)
    assert (symbol,tf,len(all_rows),len(closed),origin,tick)==('ETHFIUSDT','30',3,2,0,.0001)


@pytest.mark.parametrize('key,value', [('PARITY_META_contexts_history_start','1'),
    ('PARITY_CTX_chart_start','1'),('PARITY_CTX_probe_revision','2'),
    ('PARITY_CTX_realtime','1'),('PARITY_close','2'),('PARITY_CTX_quote_usd_requested','Infinity')])
def test_contexts_capture_rejects_inconsistent_historical_rows(tmp_path,key,value):
    path,rows=export(tmp_path);rows[0][key]=value;write(path,rows)
    with pytest.raises(ValueError):inspect_contexts(path)


def test_currency_is_recorded_input_not_inferred_from_output(tmp_path):
    _,rows=export(tmp_path)
    rows[1]['PARITY_CTX_volume_24h_usd']='999999'
    assert currency_observations(rows[:2],'30')=={'USDT|USD':[
        dict(available_at=0,expires_at=3600000,rate=.9998)]}
    rows[1]['PARITY_CTX_quote_usd_requested']=''
    assert currency_observations(rows[:2],'30')['USDT|USD'][1]==dict(available_at=1800000,expires_at=3600000,rate=None)
