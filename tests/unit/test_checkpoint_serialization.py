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
