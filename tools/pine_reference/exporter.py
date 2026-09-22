"""Generate separate debug Pine copies, respecting TradingView plot limits.

Original source stays byte-exact. Scripts require external TradingView compile
and chart export; generated files themselves are NOT a golden reference.
"""
import hashlib,json
from pathlib import Path
from backend.engine.syntax import SOURCE,load_program
from backend.engine.runtime import SIGNALS
from .catalog import METRICS,PATHS
from .trace import realtime_source,TRACE_COLUMNS,OMITTED_PARAMETERS,INTERVAL_MS,BUFFER_CHARS,RECORDER_REVISION
from .context_probe import context_source,FIELDS as CONTEXT_FIELDS

def literals(e):
    result=[]
    if e.kind=='literal' and isinstance(e.value,str):result.append(e.value)
    for child in e.args:result.extend(literals(child))
    return result

def action_codes():
    source=next(s for s in load_program().statements if s.kind=='assign' and s.meta['name']=='actionText')
    # Alphabetical ordering is stable and source-exhaustive; save version/hash with it.
    return {name:i for i,name in enumerate(sorted(set(literals(source.expr))))}

def generate(directory='tools/pine_reference/generated'):
    out=Path(directory);out.mkdir(parents=True,exist_ok=True)
    program=load_program();lines=program.source.splitlines();codes=action_codes()
    cutoff=next(i for i,line in enumerate(lines) if line.startswith('f_syncBox('))
    detector='\n'.join(lines[:cutoff])
    # The UI-only declarations below the cutoff feed displayed frozen prices.
    displayed='\n'.join(s.text for s in program.statements if s.kind=='assign' and s.meta['name'] in ('displayedTradePlanSL','displayedTradePlanT1','displayedTradePlanRR'))
    action='int parityActionCode = '+' : '.join(f'actionText == {json.dumps(name,ensure_ascii=False)} ? {code}' for name,code in codes.items())+' : -1'
    groups={'metrics':METRICS,'signals':{name:(pine,'bool') for pine,name in SIGNALS.items()}}
    manifest={'pine_source_hash':hashlib.sha256(SOURCE.read_bytes()).hexdigest(),'action_codes':codes,'path_codes':PATHS,'groups':{},'status':'EXPORT_SCRIPTS_UNVERIFIED'}
    base=detector+'\n'+displayed+'\n'+action+'\n'
    def titled(source,group,short):
        return source.replace('"Scalping_SMA 1.15.2"',json.dumps('Scalping_SMA 1.15.2 - PARITY '+group),1).replace('shorttitle = "Scalping_SMA 1.15.2"','shorttitle = '+json.dumps(short),1)
    for group,fields in groups.items():
        plots=[]
        for label,(expr,kind) in fields.items():
            if kind=='bool':expr=f'({expr} ? 1 : 0)'
            plots.append(f'plot({expr}, title = {json.dumps("PARITY_"+label)}, display = display.data_window)')
        metadata={'history_start':'parityHistoryStart','bar_index':'bar_index','tick_size':'syminfo.mintick','confirmed':'(barstate.isconfirmed ? 1 : 0)','volume':'volume','timeframe_seconds':'timeframe.in_seconds()'}
        plots.extend(f'plot({expr}, title = "PARITY_META_{group}_{name}", display = display.data_window)' for name,expr in metadata.items())
        if len(plots)>64:raise ValueError('Debug group exceeds plot budget')
        script=titled(base,group,'SMA-P-'+('MET' if group=='metrics' else 'SIG'))+'var int parityHistoryStart = time\n'+'\n'.join(plots)+'\n'
        path=out/f'Scalping_SMA_1.15.2_parity_{group}.pine';path.write_text(script,encoding='utf-8')
        manifest['groups'][group]={'file':str(path),'plots':{name:expr for name,(expr,_) in fields.items()},'metadata':metadata,'plot_count':len(plots)}
    context=out/'Scalping_SMA_1.15.2_parity_contexts.pine'
    context.write_text(context_source(titled(base,'contexts','SMA-P-CTX')),encoding='utf-8')
    manifest['contexts']={'file':str(context),'plots':CONTEXT_FIELDS,'plot_count':len(CONTEXT_FIELDS)+6,'status':'NOT_COMPILED_ON_TRADINGVIEW','purpose':'Diagnose HTF mapping and currency conversion; lookahead_on measurements are diagnostic only.'}
    trace=out/'Scalping_SMA_1.15.2_parity_intrabar.pine'
    trace.write_text(realtime_source(titled(base,'intrabar','SMA-P-RT')),encoding='utf-8')
    manifest['intrabar']={'file':str(trace),'recorder_revision':RECORDER_REVISION,'columns':TRACE_COLUMNS,'signal_bits':{name:i for i,name in enumerate(SIGNALS.values())},'omitted_parameters':OMITTED_PARAMETERS,'interval_ms':INTERVAL_MS,'buffer_chars':BUFFER_CHARS,'transport':'alert batches -> Alerts Log CSV','status':'NOT_COMPILED_ON_TRADINGVIEW'}
    (out/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    return manifest
if __name__=='__main__':print(json.dumps(generate(),ensure_ascii=False,indent=2))
