"""Pine scalar value semantics and serializable runtime values."""
import math
from dataclasses import dataclass

NA = float('nan')

def is_na(value):
    return value is None or isinstance(value, float) and math.isnan(value)

def truth(value): return False if is_na(value) else bool(value)

def binary(op,a,b):
    if is_na(a) or is_na(b): return False if op in ('==','!=','>','<','>=','<=') else NA
    # Pine rounds float comparison operands to nine fractional digits; do not
    # round arithmetic results or replace this with a relative epsilon.
    # https://www.tradingview.com/pine-script-docs/language/type-system/#float
    if op in ('==','!=','>','<','>=','<=') and isinstance(a,(int,float)) and isinstance(b,(int,float)) and (isinstance(a,float) or isinstance(b,float)):
        a,b=round(a,9),round(b,9)
    if op=='+': return a+b
    if op=='-': return a-b
    if op=='*': return a*b
    if op=='/': return a/b if b else NA
    if op=='%': return math.fmod(a,b) if b else NA
    if op=='==': return a==b
    if op=='!=': return a!=b
    if op=='>': return a>b
    if op=='<': return a<b
    if op=='>=': return a>=b
    if op=='<=': return a<=b
    raise ValueError(op)

@dataclass
class Record:
    type_name: str
    fields: dict

@dataclass(frozen=True)
class Namespace:
    name: str


def encode(value):
    if isinstance(value,Record): return {'__pine_record__':value.type_name,'fields':encode(value.fields)}
    if isinstance(value,Namespace): return {'__pine_namespace__':value.name}
    if isinstance(value,dict): return {str(k):encode(v) for k,v in value.items()}
    if isinstance(value,(list,tuple)): return [encode(v) for v in value]
    if isinstance(value,float) and not math.isfinite(value): return None
    return value

def decode(value):
    if value is None: return NA
    if isinstance(value,dict):
        if '__pine_record__' in value: return Record(value['__pine_record__'],decode(value['fields']))
        if '__pine_namespace__' in value: return Namespace(value['__pine_namespace__'])
        return {k:decode(v) for k,v in value.items()}
    if isinstance(value,list): return [decode(v) for v in value]
    return value
