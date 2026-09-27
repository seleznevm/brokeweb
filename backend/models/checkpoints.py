"""Lossless versioned storage codec; source engine state is unchanged."""
import json
import zlib
from dataclasses import dataclass

MAGIC=b'BWC1'


@dataclass(frozen=True)
class PackedCheckpoint:
    """Prepared by the worker while holding the symbol lock, ready for SQL."""
    blob: bytes

    def __post_init__(self):
        if type(self.blob) is not bytes or not self.blob.startswith(MAGIC):
            raise ValueError('Expected immutable BWC1 checkpoint bytes')

    @classmethod
    def from_state(cls, state):
        # Canonical engine exports are already JSON-normalized. The strict
        # encoder still rejects any nonfinite/unsupported value before SQL.
        return cls(pack_checkpoint(state))


def pack_checkpoint(value):
    raw=json.dumps(value,ensure_ascii=False,allow_nan=False,separators=(',',':')).encode('utf-8')
    return MAGIC+zlib.compress(raw,level=1)


def unpack_checkpoint(value):
    value=bytes(value)
    if not value.startswith(MAGIC):raise ValueError('Unsupported checkpoint storage format')
    return json.loads(zlib.decompress(value[len(MAGIC):]))
