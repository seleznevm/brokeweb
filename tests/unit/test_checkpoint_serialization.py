"""Storage optimizations must preserve runtime types and every JSON value."""
import json
from dataclasses import dataclass
from backend.engine.values import Record, Namespace, encode, decode
from backend.models.repository import clean
from backend.models.checkpoints import pack_checkpoint, unpack_checkpoint


def test_runtime_records_nonfinite_values_and_scalars_roundtrip():
    class FloatSubclass(float):pass
    value={7:Record('Plan',{'levels':(1.2345678901234567,-0.0,True,'Зона'),
                           'missing':[float('nan'),float('inf'),FloatSubclass('-inf')]}),
           'namespace':Namespace('color'),'integer':2**60,'null':None}
    expected={'7':{'__pine_record__':'Plan','fields':{'levels':[1.2345678901234567,-0.0,True,'Зона'],
                    'missing':[None,None,None]}},'namespace':{'__pine_namespace__':'color'},'integer':2**60,'null':None}
    encoded=encode(value)
    assert json.dumps(encoded,allow_nan=False)==json.dumps(expected,allow_nan=False)
    restored=decode(unpack_checkpoint(pack_checkpoint(clean(encoded))))
    assert isinstance(restored['7'],Record)
    assert isinstance(restored['namespace'],Namespace)
    assert encode(restored)==expected


def test_storage_clean_preserves_dataclass_and_scalar_subclass_semantics():
    @dataclass
    class State:
        values:tuple
    class FloatSubclass(float):pass
    value=State((float('nan'),FloatSubclass('inf'),FloatSubclass(.25),False,None,{'v':-0.0}))
    expected={'values':[None,None,.25,False,None,{'v':-0.0}]}
    assert pack_checkpoint(clean(value))==pack_checkpoint(expected)


def test_engine_export_is_canonical_and_detached_for_native_fields():
    from backend.engine.runtime import PineEngine
    from backend.models.checkpoints import PackedCheckpoint
    engine=PineEngine('TESTUSDT','30')
    engine.chart_bars=[{'start':0,'close':1.2345678901234567,'turnover':float('nan')}]
    engine.snapshot={'native_turnover':float('inf'),'nested':{True:(-0.0,2**70,'Зона')}}
    state=engine.export_state()
    blob=PackedCheckpoint.from_state(state).blob
    assert blob==pack_checkpoint(clean(state))
    engine.chart_bars[0]['close']=99
    engine.snapshot['nested'].clear()
    restored=unpack_checkpoint(blob)
    assert restored['snapshot']=={'native_turnover':None,'nested':{'True':[-0.0,2**70,'Зона']}}
    assert state['chart_bars'][0]['close']==1.2345678901234567
    assert restored['chart_bars'][0]['turnover'] is None


def test_prepared_checkpoint_rejects_noncanonical_nonfinite_input():
    import pytest
    from backend.models.checkpoints import PackedCheckpoint
    with pytest.raises(ValueError):PackedCheckpoint.from_state({'invalid':float('nan')})
    for invalid in (b'unknown',bytearray(b'BWC1')):
        with pytest.raises(ValueError,match='immutable BWC1'):PackedCheckpoint(invalid)
