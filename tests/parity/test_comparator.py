from tools.pine_reference.compare import compare
from tools.pine_reference.exporter import generate,action_codes
from tools.pine_reference.catalog import METRICS
from backend.engine.runtime import SIGNALS
from backend.engine.syntax import SOURCE,Program

def test_absent_reference_is_never_a_pass():
    report=compare([],[],.01)
    assert report['status']=='UNVERIFIED'
    assert all(r['error_p95'] is None for r in report['metrics'])

def test_missing_metric_or_signal_columns_cannot_pass():
    report=compare([{'time':1700000000,'PARITY_atr':2}],[{'bar_start':1700000000000,'metrics':{'atr':2}}],.01)
    assert report['status']=='FAIL'
    assert any(r['missing_columns'] for r in report['metrics'])

def complete_rows():
    ref={'time':1700000000};actual={'bar_start':1700000000000}
    for name in METRICS:ref['PARITY_'+name]=1;actual['PARITY_'+name]=1
    for name in SIGNALS.values():ref['PARITY_'+name]=0;actual['PARITY_'+name]=0
    return ref,actual

def test_known_exact_fixture_exercises_comparator_not_pine_parity():
    ref,actual=complete_rows();assert compare([ref],[actual],.01)['status']=='PASS'
    actual['PARITY_fsm']=2;assert compare([ref],[actual],.01)['status']=='FAIL'

def test_missing_reference_rows_and_na_mismatch_fail():
    ref,actual=complete_rows();actual['PARITY_atr']=None
    assert compare([ref],[actual],.01)['status']=='FAIL'
    actual['bar_start']+=60000
    assert compare([ref],[actual],.01)['missing_records']==1

def test_exporter_preserves_source_and_covers_all_events(tmp_path):
    original=SOURCE.read_bytes();manifest=generate(tmp_path)
    assert SOURCE.read_bytes()==original
    assert len(manifest['groups']['signals']['plots'])==len(SIGNALS)
    trace=(tmp_path/'Scalping_SMA_1.15.2_parity_intrabar.pine').read_text(encoding='utf-8')
    assert f'PARITY intrabar v{manifest["intrabar"]["recorder_revision"]}' in trace
    for file in tmp_path.glob('*.pine'):
        program=Program(file.read_text(encoding='utf-8'))
        assert len(program.statements)>1000
        assert file.read_text(encoding='utf-8').count('\nplot(')<=64
    assert 'WAIT SETUP' in action_codes()


def metric(report,name):return next(r for r in report['metrics'] if r['metric']==name)
def signal(report,name):return next(r for r in report['signals'] if r['signal']==name)


def test_absent_python_signal_cannot_be_assumed_false():
    ref,actual=complete_rows();name=next(iter(SIGNALS.values()))
    del actual['PARITY_'+name]
    result=compare([ref],[actual],.01)
    assert signal(result,name)['status']=='FAIL'
    assert signal(result,name)['missing_python_columns']==1
    # An explicitly supplied complete, empty signal list does mean no events.
    actual['signals']=[]
    assert compare([ref],[actual],.01)['status']=='PASS'


def test_malformed_and_missing_signal_values_are_never_zero():
    name=next(iter(SIGNALS.values()))
    for bad in ('oops','',None,2,-1,'Infinity'):
        for side in (0,1):
            ref,actual=complete_rows();(ref,actual)[side]['PARITY_'+name]=bad
            actual['signals']=[]
            result=compare([ref],[actual],.01)
            assert result['status']=='FAIL',bad
            assert signal(result,name)['invalid_values']==1


def test_all_na_metric_is_unverified_and_missing_column_is_fail():
    ref,actual=complete_rows();ref['PARITY_sl']=None;actual['PARITY_sl']=None
    result=compare([ref],[actual],.01)
    assert result['status']=='UNVERIFIED'
    assert metric(result,'sl')['status']=='NOT_OBSERVED'
    assert metric(result,'sl')['agreement_percent'] is None
    del actual['PARITY_sl']
    result=compare([ref],[actual],.01)
    assert metric(result,'sl')['status']=='FAIL'
    assert metric(result,'sl')['missing_python_columns']==1


def test_na_mismatches_do_not_create_negative_agreement():
    refs=[];rows=[]
    for i in range(3):
        ref,actual=complete_rows();ref['time']+=i;actual['bar_start']+=i*1000
        if i<2:actual['PARITY_atr']=None
        else:actual['PARITY_atr']=2
        refs.append(ref);rows.append(actual)
    result=compare(refs,rows,.01)
    assert metric(result,'atr')['agreement_percent']==0
    assert metric(result,'atr')['missing_values']==2
    assert metric(result,'atr')['pine']==3 and metric(result,'atr')['python']==1


def test_invalid_numeric_fields_and_boolean_domains_fail():
    for name,bad in [('atr','oops'),('atr','Infinity'),('gate_liquidity',2)]:
        ref,actual=complete_rows();ref['PARITY_'+name]=actual['PARITY_'+name]=bad
        result=compare([ref],[actual],.01)
        assert metric(result,name)['status']=='FAIL'
        assert metric(result,name)['invalid_values']==1


def test_realtime_anonymous_reference_matches_one_instrument_with_bounded_time():
    ref,actual=complete_rows()
    ref['event_time']=1700000000000;actual['event_time']=1700000001250
    actual.update(exchange='BYBIT',symbol='ETHFIUSDT',timeframe=30)
    result=compare([ref],[actual],.01,realtime=True)
    assert result['status']=='PASS' and result['timestamp_error_max_ms']==1250
    actual['event_time']+=251
    assert compare([ref],[actual],.01,realtime=True)['status']=='FAIL'


def test_realtime_matching_reserves_exact_observations_before_nearby_ones():
    ref,actual=complete_rows();base=1700000000000
    refs=[dict(ref,event_time=base+offset) for offset in (0,1000)]
    rows=[dict(actual,event_time=base+offset) for offset in (1000,1500)]
    # The exact match at 1000 belongs to the second reference, regardless of input order.
    result=compare(refs,list(reversed(rows)),.01,realtime=True)
    assert result['status']=='PASS' and result['timestamp_error_max_ms']==1500


def test_duplicate_and_ambiguous_identity_are_rejected():
    import pytest
    ref,actual=complete_rows()
    with pytest.raises(ValueError,match='Duplicate reference'):compare([ref,ref],[actual],.01)
    with pytest.raises(ValueError,match='Duplicate Python'):compare([ref],[actual,actual],.01)
    rows=[dict(actual,exchange='BYBIT',symbol=s,timeframe='30') for s in ('ARB','DOT')]
    rows[1]['bar_start']+=1800000
    with pytest.raises(ValueError,match='ambiguous'):compare([ref],rows,.01)
    with pytest.raises(ValueError,match='identity'):compare([dict(ref,symbol='ARB')],[actual],.01)


def test_unmatched_python_rows_inside_reference_window_fail_but_warmup_is_allowed():
    ref,actual=complete_rows();base=actual['bar_start']
    refs=[dict(ref,time=ref['time']+i) for i in (0,2)]
    rows=[dict(actual,bar_start=base+i*1000) for i in (-10,0,2,10)]
    assert compare(refs,rows,.01)['status']=='PASS'
    rows.append(dict(actual,bar_start=base+1000))
    report=compare(refs,rows,.01)
    assert report['status']=='FAIL' and report['unexpected_python_records']==1


def test_timestamps_and_tolerances_are_validated():
    import pytest
    from tools.pine_reference.compare import timestamp
    assert timestamp({'time':'2026-09-15T12:00:00+07:00'})==1789448400000
    assert timestamp({'bar_start':1800000})==1800000
    assert timestamp({'event_time':1800000},True)==1800000
    for value in ('2026-09-15T12:00:00',True,-1,'bad',1700000000000.25):
        with pytest.raises(ValueError):timestamp({'time':value})
    ref,actual=complete_rows()
    for tick in (0,-1,float('nan'),float('inf'),True):
        with pytest.raises(ValueError):compare([ref],[actual],tick)
    with pytest.raises(ValueError):compare([ref],[actual],.01,max_timestamp_delta_ms=1501)


def test_loader_rejects_duplicate_columns_and_bad_row_width(tmp_path):
    import pytest
    from tools.pine_reference.compare import load
    p=tmp_path/'reference.csv'
    for text in ('time,PARITY_atr,PARITY_atr\n1,2,3\n','time,PARITY_atr\n1\n','time,PARITY_atr\n1,2,3\n'):
        p.write_text(text)
        with pytest.raises(ValueError):load(p)


def test_cli_invalid_input_replaces_stale_pass_and_preserves_inputs(tmp_path):
    import json,pytest
    from tools.pine_reference.compare import main
    p=tmp_path/'reference.csv';p.write_text('time,time\n1,1\n')
    out=tmp_path/'report.json';out.write_text('{"status":"PASS"}')
    assert main(['--reference',str(p),'--python',str(p),'--output',str(out)])==2
    assert json.loads(out.read_text())['status']=='INVALID_INPUT'
    with pytest.raises(SystemExit):main(['--reference',str(p),'--output',str(p)])
    assert p.read_text()=='time,time\n1,1\n'
    # A deterministic temporary output name could also overwrite an input.
    partial=tmp_path/'report.json.partial';partial.write_text('{"metrics":null}')
    assert main(['--reference',str(partial),'--output',str(out)])==2
    assert partial.read_text()=='{"metrics":null}'
