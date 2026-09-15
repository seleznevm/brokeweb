"""Generate a source-line audit map without manually omitting intermediate fields."""
import json,hashlib
from pathlib import Path
from backend.engine.syntax import load_program,SOURCE
from backend.engine.runtime import FIELDS,SIGNALS
from .exporter import action_codes
from .catalog import PATHS

def names(expr):
    result=set()
    if expr.kind=='name':result.add(str(expr.value))
    for child in expr.args:result.update(names(child))
    return sorted(result-{'true','false','na'})
def generate():
    program=load_program();rows=[];reverse={v:k for k,v in FIELDS.items()}
    def visit(nodes,scope='global'):
        for st in nodes:
            if st.kind=='assign' and st.meta['op']=='=':
                name=st.meta['name'];mode=st.meta['mode'] or 'series';typ=st.meta['type'] or 'inferred tuple'
                fields=[n.strip() for n in name[1:-1].split(',')] if name.startswith('[') else [name]
                for name in fields:
                    api=reverse.get(name,f'metrics.{name}') if scope=='global' else f'metrics.source_locals.{scope}/{name}'
                    semantics='persists between intrabar updates' if mode=='varip' else 'rollback to prior committed close; commit only confirmed' if mode=='var' else 'recalculated in source order; history stores committed execution values'
                    formula=st.text.split(' = ',1)[-1].replace('|','\\|')
                    rows.append({'name':name,'scope':scope,'line':st.line,'type':typ,'state':mode,'formula':formula,'inputs':names(st.expr),'semantics':semantics,'api':api})
            visit(st.body,scope);visit(st.otherwise,scope)
    visit(program.statements)
    for name,fn in program.functions.items():visit(fn.body,name)
    out=Path('docs');out.mkdir(exist_ok=True)
    text=['# Pine → Python mapping','',f'Source SHA256: `{hashlib.sha256(SOURCE.read_bytes()).hexdigest()}`.', '', 'The source AST is the baseline; Python fields retain Pine identifiers. `Execution.scopes` holds values, `persistent` models var, `varip` models intrabar persistence. `setup_snapshots.payload.metrics` / `setup_current.payload.metrics` preserve globals; local callsite values are under `metrics.source_locals`. Frequently queried canonical scores have typed columns. Every formula below is executed from the cited original line, without rewritten thresholds. External TradingView parity remains unverified.','', '| Pine variable (scope) | Python field | Formula / source | Input dependencies | State / realtime semantics | DB field | API field |','|---|---|---|---|---|---|---|']
    for r in rows:
        text.append(f"| `{r['name']}` ({r['scope']}) | `Execution.scopes[{r['name']}]` | L{r['line']}: `{r['formula']}` | {', '.join(r['inputs'])} | {r['state']} / {r['semantics']} | `payload.{r['api']}` | `{r['api']}` |")
    (out/'pine_mapping.md').write_text('\n'.join(text)+'\n',encoding='utf-8')
    (Path('reference')/'mapping.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2),encoding='utf-8')
    enums={'action':action_codes(),'paths':PATHS,'fsm':{'NO SETUP':0,'FORMING':1,'WATCH':2,'APPROACH':3,'ARMED':4,'TRIGGERED':5,'POST-BREAK RETEST':6,'PINE READY':7,'ACTIVE':8}}
    (Path('reference')/'enums.json').write_text(json.dumps(enums,ensure_ascii=False,indent=2),encoding='utf-8')
    return len(rows)
if __name__=='__main__':print(generate())
