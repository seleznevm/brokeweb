import json,math
import pytest
from backend.engine.runtime import PineEngine,PINE_HASH
from backend.engine.syntax import SOURCE
from backend.engine.parameters import input_schema
from backend.models.checkpoints import pack_checkpoint,unpack_checkpoint

def bar(i,confirmed=True):
    close=100+math.sin(i*.3)*3+i*.03
    return dict(start=i*900000,end=(i+1)*900000,open=close-.1,high=close+.6,low=close-.5,close=close,volume=1000.,confirmed=confirmed,received_at=(i+1)*900000)

@pytest.mark.parametrize('compressed',[False,True])
def test_full_source_checkpoint_roundtrip_preserves_next_closed_and_intrabar_state(compressed):
    engine=PineEngine('TESTUSDT','15',.01)
    for i in range(65):engine.update(bar(i))
    engine.update(bar(65,False),realtime=True)
    checkpoint=json.loads(json.dumps(engine.export_state(),allow_nan=False))
    if compressed:checkpoint=unpack_checkpoint(pack_checkpoint(checkpoint))
    restored=PineEngine('TESTUSDT','15',.01);restored.restore_state(checkpoint)
    for confirmed in (False,True):
        a=engine.update(bar(65,confirmed),realtime=True);b=restored.update(bar(65,confirmed),realtime=True)
        assert a['metrics']==b['metrics']
        assert a['signals']==b['signals']
        assert a['setup_generation_id']==b['setup_generation_id']
    assert engine.runtime.count==restored.runtime.count==66

def test_changed_parameter_checkpoint_is_rejected():
    engine=PineEngine('TESTUSDT','15');engine.update(bar(0))
    other=PineEngine('TESTUSDT','15',parameters={'emaFastLen':21})
    with pytest.raises(ValueError,match='version mismatch'):other.restore_state(engine.export_state())

def test_all_source_inputs_preserved_and_original_hash_pinned():
    import hashlib
    assert len(input_schema())==323
    assert hashlib.sha256(SOURCE.read_bytes()).hexdigest()==PINE_HASH
    assert PINE_HASH=='782ff6575c9e6e997dea386d429264ea277de22f170e29c0886c62a63c76881e'
