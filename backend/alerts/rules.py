"""A data-only rule language; never evaluates Python or user expressions."""
from __future__ import annotations
import math
from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator

OPS={'==','!=','>','>=','<','<=','IN','NOT IN','BETWEEN','contains','changed','changed_to','crosses_above','crosses_below'}

def field_value(record: dict, path: str):
    value: Any=record
    for part in path.split('.'):
        if not isinstance(value,dict): return None
        value=value.get(part)
    return value

def validate_condition(node: dict, depth: int=0) -> dict:
    if not isinstance(node,dict) or depth>10: raise ValueError('Invalid condition or nesting exceeds 10')
    op=node.get('op')
    if op in {'AND','OR','NOT'}:
        children=node.get('conditions')
        if not isinstance(children,list) or not children or len(children)>100: raise ValueError('Groups require 1 to 100 conditions')
        if op=='NOT' and len(children)!=1: raise ValueError('NOT requires exactly one condition')
        for child in children: validate_condition(child,depth+1)
    else:
        if op not in OPS or not isinstance(node.get('field'),str): raise ValueError('Unknown operator or missing field')
        if op!='changed' and 'value' not in node: raise ValueError('Operator requires value')
        if op in {'IN','NOT IN','BETWEEN'} and not isinstance(node.get('value'),list): raise ValueError('Operator requires array value')
        if op=='BETWEEN' and len(node['value'])!=2: raise ValueError('BETWEEN requires two bounds')
    return node

def matches(node:dict, current:dict, previous:dict|None=None) -> bool:
    op=node['op']
    if op=='AND': return all(matches(c,current,previous) for c in node['conditions'])
    if op=='OR': return any(matches(c,current,previous) for c in node['conditions'])
    if op=='NOT': return not matches(node['conditions'][0],current,previous)
    a=field_value(current,node['field']); b=node.get('value'); old=field_value(previous or {},node['field'])
    if op in {'changed','changed_to','crosses_above','crosses_below'} and previous is None: return False
    if op=='changed': return a!=old
    if op=='changed_to': return a!=old and a==b
    # Missing measurements never pass numeric or inequality predicates.
    if a is None or isinstance(a,float) and not math.isfinite(a): return False
    try:
        if op=='==': return a==b
        if op=='!=': return a!=b
        if op=='>': return a>b
        if op=='>=': return a>=b
        if op=='<': return a<b
        if op=='<=': return a<=b
        if op=='IN': return a in b
        if op=='NOT IN': return a not in b
        if op=='BETWEEN': return b[0]<=a<=b[1]
        if op=='contains': return b in a
        if op=='crosses_above': return old is not None and old<=b<a
        if op=='crosses_below': return old is not None and old>=b>a
    except (TypeError,ValueError): return False
    return False

class AlertRuleInput(BaseModel):
    model_config=ConfigDict(extra='forbid')
    name: str=Field(min_length=1,max_length=120)
    enabled: bool=True
    conditions: dict
    mode: Literal['realtime','confirmed']='realtime'
    frequency: Literal['once_per_bar','once_per_generation','first_occurrence','repeat']='once_per_bar'
    cooldown_seconds: int=Field(default=60,ge=0,le=604800)
    template: str=Field(default='',max_length=3500)
    @field_validator('conditions')
    @classmethod
    def valid_conditions(cls,v): return validate_condition(v)
