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
    for file in tmp_path.glob('*.pine'):
        program=Program(file.read_text())
        assert len(program.statements)>1000
        assert file.read_text().count('\nplot(')<=64
    assert 'WAIT SETUP' in action_codes()
