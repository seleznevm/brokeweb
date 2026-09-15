"""Lossless versioned storage codec; source engine state is unchanged."""
import json
import zlib

MAGIC=b'BWC1'


def pack_checkpoint(value):
    raw=json.dumps(value,ensure_ascii=False,allow_nan=False,separators=(',',':')).encode('utf-8')
    return MAGIC+zlib.compress(raw,level=1)


def unpack_checkpoint(value):
    value=bytes(value)
    if not value.startswith(MAGIC):raise ValueError('Unsupported checkpoint storage format')
    return json.loads(zlib.decompress(value[len(MAGIC):]))
