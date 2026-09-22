"""Transport/provenance tests; synthetic messages are not TradingView parity evidence."""
import csv
import json
from copy import deepcopy

import pytest

from backend.engine.runtime import PINE_HASH, SIGNALS
from tools.pine_reference.catalog import METRICS
from tools.pine_reference.trace import (
    INTERVAL_MS, PARAMETERS, TRACE_COLUMNS, main, messages, unpack, realtime_source,
)


def batch(batch_id=1, seq=1):
    row = dict.fromkeys(TRACE_COLUMNS, 0)
    row.update(seq=seq, event_time=1700000000000 + seq * 20000,
               bar_start=1700000000000, bar_end=1700000300000,
               bar_index=100, bar_update=seq, volume=12,
               is_new=int(seq == 1), signal_mask=1 | (1 << (len(SIGNALS)-1)))
    row.update({f'PARITY_{name}': 1 for name in ('open', 'high', 'low', 'close')})
    return dict(brokeweb_trace=1, pine_source_hash=PINE_HASH,
                label='тест "CSV"', run_start=1700000020000,
                exchange='BYBIT', symbol='ETHFIUSDT.P', timeframe='5',
                tick_size=.0001, history_start=1690000000000,
                batch_id=batch_id, first_seq=seq, last_seq=seq, dropped=0,
                interval_ms=INTERVAL_MS,
                parameters={s['name']:s['default'] for s in PARAMETERS},
                rows=[[row[k] for k in TRACE_COLUMNS]])


def set_row(e, name, value):
    e['rows'][0][TRACE_COLUMNS.index(name)] = value


def test_alert_csv_reverse_order_duplicates_and_signal_decode(tmp_path):
    path=tmp_path/'alerts.csv'
    with path.open('w',encoding='utf-8-sig',newline='') as f:
        writer=csv.writer(f);writer.writerow(['Time','Message'])
        for e in (batch(2,2),batch(),batch()):writer.writerow(['UTC',json.dumps(e,ensure_ascii=False)])
        writer.writerow(['UTC','An unrelated alert'])
    sessions,duplicates=unpack(messages(path))
    assert duplicates==1 and len(sessions)==1
    s=sessions[0]
    assert [r['seq'] for r in s['rows']]==[1,2]
    assert s['rows'][0]['symbol']=='ETHFIUSDT'
    for i,name in enumerate(SIGNALS.values()):
        assert s['rows'][0]['PARITY_'+name]==int(i in (0,len(SIGNALS)-1))
    assert s['report']['sequence_contiguous']
    assert s['report']['parity_status']=='UNVERIFIED'
    assert not s['report']['comparison_ready']


def test_gap_overflow_partial_bar_and_unknown_tail_are_explicit():
    e=batch(3,5);e.update(last_seq=7,dropped=2)
    report=unpack([e])[0][0]['report']
    assert report['reported_dropped_updates']==2
    assert not report['sequence_contiguous']
    assert any('Missing initial' in s for s in report['issues'])
    assert any('inside' in s for s in report['issues'])
    assert any('Unsent tail' in s for s in report['issues'])


def test_same_millisecond_updates_are_retained():
    a,b=batch(),batch(2,2)
    set_row(b,'event_time',a['rows'][0][TRACE_COLUMNS.index('event_time')])
    s=unpack([a,b])[0][0]
    assert len(s['rows'])==2 and s['report']['same_timestamp_updates']==1


def test_missing_prefix_does_not_mark_later_complete_bars_as_missing():
    a,b=batch(2,2),batch(3,3)
    set_row(a,'is_new',1);set_row(b,'confirmed',1)
    report=unpack([a,b])[0][0]['report']
    assert not report['sequence_contiguous']
    assert report['received_sequence_contiguous']
    assert report['missing_prefix_updates']==1
    assert report['complete_bars']==1
    assert report['parity_status']=='UNVERIFIED'


def test_internal_gap_still_blocks_received_sequence():
    report=unpack([batch(),batch(3,3)])[0][0]['report']
    assert not report['received_sequence_contiguous']
    assert report['complete_bars']==0


def test_sessions_separate_and_metadata_cannot_change():
    a,b=batch(),batch(2,2);b['label']='other capture'
    assert len(unpack([a,b])[0])==2
    b['label']=a['label'];b['history_start']+=1
    with pytest.raises(ValueError,match='metadata changed'):unpack([a,b])
    b=deepcopy(a);b['dropped']=1
    with pytest.raises(ValueError,match='Conflicting duplicate'):unpack([a,b])


@pytest.mark.parametrize('field,value',[
    ('pine_source_hash','unknown'),('exchange','BINANCE'),('tick_size',0),
    ('batch_id',True),('last_seq',2),('parameters',{}),('rows',[]),
])
def test_invalid_envelope_rejected(field,value):
    e=batch();e[field]=value
    with pytest.raises(ValueError):unpack([e])


@pytest.mark.parametrize('field,value',[
    ('volume',-1),('PARITY_high',0),('PARITY_atr',float('nan')),
    ('PARITY_gate_liquidity',2),('PARITY_direction',.5),
    ('signal_mask',1<<len(SIGNALS)),('seq',0),('confirmed',True),
    ('event_time',0),('bar_end',0),
])
def test_invalid_observation_rejected(field,value):
    e=batch();set_row(e,field,value)
    with pytest.raises(ValueError):unpack([e])


def test_invalid_parameter_value_rejected():
    e=batch();spec=next(s for s in PARAMETERS if s['type']=='bool')
    e['parameters'][spec['name']]='true'
    with pytest.raises(ValueError,match='must be bool'):unpack([e])


@pytest.mark.parametrize('text',[
    'Time,Message\nUTC,"{""brokeweb_trace"":1"\n',
    'Time,Message\nUTC\n', 'Message,Message\na,b\n',
    'Message\nUnrelated alert\n',
])
def test_corrupt_or_unrelated_csv_rejected(tmp_path,text):
    path=tmp_path/'alerts.csv';path.write_text(text)
    with pytest.raises(ValueError):messages(path)


def test_cli_import_and_overwrite_preflight(tmp_path):
    source=tmp_path/'capture.jsonl';out=tmp_path/'out'
    a=batch();source.write_text(json.dumps(a))
    assert main(['--input',str(source),'--output-dir',str(out)])==0
    before={p:p.read_bytes() for p in out.rglob('*.json*')}
    b=batch();b['label']='another capture'
    source.write_text(json.dumps(b)+'\n'+json.dumps(a))
    assert main(['--input',str(source),'--output-dir',str(out)])==2
    assert {p:p.read_bytes() for p in out.rglob('*.json*')}==before


def test_legacy_enum_repair_preserves_raw_input_and_numeric_rows():
    e=batch();e['parameters']['lazyMode']='P\roxy T\radingView'
    e['parameters']['dashboardTextSizeInput']='No\rmal'
    original=deepcopy(e)
    s=unpack([e])[0][0]
    assert e==original
    assert s['metadata']['recorded_parameters']==original['parameters']
    assert s['metadata']['parameters']['lazyMode']=='Proxy TradingView'
    assert s['metadata']['parameters']['dashboardTextSizeInput']=='Normal'
    assert {r['parameter'] for r in s['report']['parameter_repairs']}=={'lazyMode','dashboardTextSizeInput'}
    assert s['report']['nondefault_parameters']=={}
    assert s['rows'][0]['PARITY_close']==original['rows'][0][TRACE_COLUMNS.index('PARITY_close')]


@pytest.mark.parametrize('revision,value',[(2,'P\roxy T\radingView'),(1,'Proxy T\radingView'),(1,'Not a valid\r option')])
def test_enum_repair_rejects_unknown_or_partially_corrupted_values(revision,value):
    e=batch();e['recorder_revision']=revision;e['parameters']['lazyMode']=value
    with pytest.raises(ValueError):unpack([e])


def test_recorder_revision_and_unrestricted_strings_are_not_guessed():
    e=batch();e['recorder_revision']=4
    with pytest.raises(ValueError,match='revision'):unpack([e])
    e=batch();e['parameters']['btcSymbol']='BINANCE:BT\rC.P'
    with pytest.raises(ValueError,match='unambiguously'):unpack([e])


def test_generator_does_not_treat_unsupported_pine_escape_as_carriage_return():
    source=realtime_source('')
    assert r'"\r"' not in source
    assert r'str.match(value, "\\x{000D}")' in source
    assert r'"\"recorder_revision\":" + "3"' in source


def test_bar_coverage_keeps_repeated_closes_and_excludes_partial_bars():
    e=batch();rows=[]
    for seq,(start,new,confirmed) in enumerate([(0,0,0),(0,0,1),(0,0,1),(300000,1,0),(300000,0,1),(600000,1,0)],1):
        r=dict(zip(TRACE_COLUMNS,e['rows'][0]));r.update(seq=seq,event_time=1700000000000+seq*200000,
            bar_start=1700000000000+start,bar_end=1700000300000+start,is_new=new,confirmed=confirmed)
        rows.append([r[k] for k in TRACE_COLUMNS])
    e.update(rows=rows,last_seq=len(rows))
    s=unpack([e])[0][0];r=s['report']
    assert len(s['rows'])==6 and r['closed_bars']==2 and r['complete_bars']==1
    assert r['repeated_confirmed_updates']==1
    assert r['bar_coverage'][0]['confirmed_updates']==2
    assert r['active_setup_updates']==0
    assert r['signal_positive_updates']['L WATCH']==6
    assert r['parity_status']=='UNVERIFIED'
