"""Generate and import bounded realtime TradingView alert-log batches.

No network receiver is required. An imported trace is evidence, not a parity
PASS: gaps, omitted inputs, the initial partial bar and trailing open bars are
reported explicitly. Alert CSV order may be newest first.
"""
from __future__ import annotations
import argparse
import csv
import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from backend.engine.parameters import input_schema, validate_parameters
from backend.engine.runtime import PINE_HASH, SIGNALS
from .catalog import METRICS
from .trace_contexts import (ARRAY_HELPERS, MAX_MICRO_INTRABARS, REQUEST_ROUTES, decode_observation,
                             pine_array, pine_observation, validate_routes)

INTERVAL_MS=20000
BUFFER_CHARS=24000
RECORDER_REVISION=3
TRACE_COLUMNS=['seq','event_time','bar_start','bar_end','bar_index','confirmed','is_new','bar_update','volume',*('PARITY_'+name for name in METRICS),'signal_mask']
CONTEXT_TRACE_COLUMNS=[*TRACE_COLUMNS, 'request_observations']
PARAMETERS=[s for s in input_schema() if s['type'] not in ('source','color')]
OMITTED_PARAMETERS=[s['name'] for s in input_schema() if s['type'] in ('source','color')]


def quoted(value):return json.dumps(value,ensure_ascii=False)


def realtime_source(base):
    helpers=r'''
// Recording only. All detector calculations above remain in source order.
f_bwrNumber(float value) =>
    na(value) ? "null" : str.tostring(value, "#.################")
f_bwrInt(int value) =>
    na(value) ? "null" : str.tostring(value, "#")
f_bwrBool(bool value) =>
    value ? "true" : "false"
f_bwrString(string value) =>
    string escaped = str.replace_all(str.replace_all(value, "\\", "\\\\"), "\"", "\\\"")
    escaped := str.replace_all(str.replace_all(escaped, "\n", "\\n"), "\t", "\\t")
    // Pine has no carriage-return literal escape. Match U+000D explicitly.
    string carriageReturn = str.match(value, "\\x{000D}")
    if not na(carriageReturn) and str.length(carriageReturn) > 0
        escaped := str.replace_all(escaped, carriageReturn, "\\r")
    "\"" + escaped + "\""
string bwrLabel = input.string("capture-001", "Capture label", group = "PARITY capture")
var int bwrOrigin = time
varip int bwrRunStart = na
varip int bwrSeq = 0
varip int bwrBarUpdate = 0
varip int bwrBatch = 0
varip int bwrLastSentAt = na
varip int bwrLastSentSeq = 0
varip int bwrDropped = 0
varip string bwrBuffer = ""
'''
    helpers += ARRAY_HELPERS
    parameters=[]
    for s in PARAMETERS:
        fn='f_bwrBool' if s['type']=='bool' else 'f_bwrInt' if s['type']=='int' else 'f_bwrNumber' if s['type']=='float' else 'f_bwrString'
        parameters.append(quoted(quoted(s['name'])+':')+f' + {fn}({s["name"]})')
    settings='var string bwrParameters = "{" + '+(' + "," + '.join(parameters))+' + "}"\n'
    values=['f_bwrInt(bwrSeq)','f_bwrInt(timenow)','f_bwrInt(time)','f_bwrInt(time_close)','f_bwrInt(bar_index)','(barstate.isconfirmed ? "1" : "0")','(barstate.isnew ? "1" : "0")','f_bwrInt(bwrBarUpdate)','f_bwrNumber(volume)']
    for expr,kind in METRICS.values():
        values.append(f'({expr} ? "1" : "0")' if kind=='bool' else f'f_bwrNumber({expr})')
    mask=' + '.join(f'({pine} ? {1<<i} : 0)' for i,pine in enumerate(SIGNALS))
    values.append('f_bwrInt(bwrMask)')
    values.append(pine_observation())
    fields=[('brokeweb_trace','"1"'),('pine_source_hash',quoted(quoted(PINE_HASH))),('label','f_bwrString(bwrLabel)'),('run_start','f_bwrInt(bwrRunStart)'),('exchange','f_bwrString(syminfo.prefix)'),('symbol','f_bwrString(syminfo.ticker)'),('timeframe','f_bwrString(timeframe.period)'),('tick_size','f_bwrNumber(syminfo.mintick)'),('history_start','f_bwrInt(bwrOrigin)'),('batch_id','f_bwrInt(bwrBatch)'),('first_seq','f_bwrInt(bwrLastSentSeq + 1)'),('last_seq','f_bwrInt(bwrSeq)'),('dropped','f_bwrInt(bwrDropped)'),('interval_ms',quoted(str(INTERVAL_MS))),('parameters','bwrParameters'),('rows','"[" + bwrBuffer + "]"')]
    fields.insert(1,('recorder_revision',quoted(str(RECORDER_REVISION))))
    routes='"{" + '+' + "," + '.join(quoted(quoted(k)+':')+' + '+pine_array([f'f_bwrString({v})' for v in route]) for k,route in REQUEST_ROUTES.items())+' + "}"'
    fields.extend([('request_routes',routes),('quote_currency','f_bwrString(syminfo.currency)'),('volume_type','f_bwrString(syminfo.volumetype)')])
    envelope='"{" + '+' + "," + '.join(quoted(quoted(k)+':')+' + '+v for k,v in fields)+' + "}"'
    recording='''if barstate.isrealtime
    if na(bwrRunStart)
        bwrRunStart := timenow
    bwrSeq += 1
    bwrBarUpdate := barstate.isnew ? 1 : bwrBarUpdate + 1
'''
    recording+='    int bwrMask = '+mask+'\n'
    recording+=f'    string bwrRow = ""\n    if microIntrabarCount <= {MAX_MICRO_INTRABARS}\n'
    recording+='        bwrRow := "[" + '+' + "," + '.join(values)+' + "]"\n'
    recording+=f'''    if str.length(bwrRow) > 0 and str.length(bwrBuffer) + str.length(bwrRow) + 1 <= {BUFFER_CHARS}
        bwrBuffer += (str.length(bwrBuffer) > 0 ? "," : "") + bwrRow
    else
        bwrDropped += 1
    if na(bwrLastSentAt) or timenow - bwrLastSentAt >= {INTERVAL_MS}
        bwrBatch += 1
        string bwrMessage = {envelope}
        if str.length(bwrMessage) > 38000
            runtime.error("PARITY message exceeds safe size; no truncated reference sent")
        alert(bwrMessage, alert.freq_all)
        bwrBuffer := ""
        bwrDropped := 0
        bwrLastSentSeq := bwrSeq
        bwrLastSentAt := timenow
'''
    return base+'\n'+helpers+settings+'plot(na, title = "PARITY recorder", display = display.none)\n'+recording


def messages(path):
    path=Path(path)
    if path.suffix.lower()=='.csv':
        with path.open(encoding='utf-8-sig',newline='') as f:
            reader=csv.DictReader(f)
            if not reader.fieldnames or len(reader.fieldnames)!=len(set(reader.fieldnames)):raise ValueError('Missing or duplicate alert CSV headers')
            records=list(reader)
            if any(None in r or None in r.values() for r in records):raise ValueError('Invalid alert CSV row width')
            texts=[v for row in records for v in row.values()]
    else:texts=path.read_text(encoding='utf-8-sig').splitlines()
    result=[]
    for text in texts:
        start=text.find('{"brokeweb_trace"')
        if start<0:continue
        try:message=json.loads(text[start:])
        except ValueError as exc:raise ValueError('Malformed/truncated Brokeweb trace message') from exc
        result.append(message)
    if not result:raise ValueError('No Brokeweb trace messages found; export Alerts Log with the Message column')
    return result


def integer(value,minimum=0):return isinstance(value,int) and not isinstance(value,bool) and value>=minimum

def finite(value):return isinstance(value,(int,float)) and not isinstance(value,bool) and math.isfinite(value)


def captured_parameters(envelope):
    """Repair only uniquely identifiable enum values from the revision-1 bug.

    The old Pine encoder replaced literal r with a JSON carriage-return escape.
    Keep the raw envelope untouched and record each repair; arbitrary strings
    and numeric observations are never inferred or rewritten.
    """
    revision=envelope.get('recorder_revision',1)
    if not integer(revision,1) or revision not in (1,2,RECORDER_REVISION):
        raise ValueError('Unsupported recorder revision')
    raw=envelope.get('parameters')
    if not isinstance(raw,dict) or set(raw)!={s['name'] for s in PARAMETERS}:
        raise ValueError('Incomplete captured parameters')
    values=dict(raw);repairs=[]
    if revision==1:
        for spec in PARAMETERS:
            value=values[spec['name']]
            if not isinstance(value,str) or '\r' not in value:continue
            candidates=[v for v in spec.get('options',[]) if isinstance(v,str) and v.replace('r','\r')==value]
            if len(candidates)!=1:raise ValueError('Cannot unambiguously repair legacy parameter: '+spec['name'])
            values[spec['name']]=candidates[0]
            repairs.append(dict(parameter=spec['name'],recorded=value,restored=candidates[0],reason='revision_1_string_escape'))
    validate_parameters(values)
    return values,repairs,revision


def coverage(rows, metadata):
    bars={}
    for row in rows:bars.setdefault(row['bar_start'],[]).append(row)
    bar_coverage=[]
    for start,updates in bars.items():
        confirmed=sum(r['confirmed'] for r in updates)
        # A startup gap invalidates the session, not every later candle.
        # Keep per-candle coverage separate from stateful replay eligibility.
        contiguous=all(b['seq']==a['seq']+1 for a,b in zip(updates,updates[1:]))
        bar_coverage.append(dict(bar_start=start,bar_end=updates[0]['bar_end'],updates=len(updates),
                                 first_seq=updates[0]['seq'],last_seq=updates[-1]['seq'],
                                 starts_with_new_bar=updates[0]['is_new'],confirmed_updates=confirmed,
                                 sequence_contiguous=contiguous,
                                 complete=bool(contiguous and updates[0]['is_new'] and updates[0]['bar_update']==1 and confirmed)))
    return dict(
        event_start_utc=datetime.fromtimestamp(rows[0]['event_time']/1000,timezone.utc).isoformat(),
        event_end_utc=datetime.fromtimestamp(rows[-1]['event_time']/1000,timezone.utc).isoformat(),
        duration_seconds=(rows[-1]['event_time']-rows[0]['event_time'])/1000,
        complete_bars=sum(b['complete'] for b in bar_coverage),bar_coverage=bar_coverage,
        repeated_confirmed_updates=sum(max(b['confirmed_updates']-1,0) for b in bar_coverage),
        active_setup_updates=sum(r['PARITY_direction'] in (-1,1) for r in rows),
        signal_positive_updates={name:sum(r['PARITY_'+name] for r in rows) for name in SIGNALS.values()},
        captured_parameter_count=len(metadata['parameters']),
        nondefault_parameters={s['name']:metadata['parameters'][s['name']] for s in PARAMETERS
                               if metadata['parameters'][s['name']]!=s['default']},
        parameter_repairs=metadata.get('parameter_repairs',[]),
        request_boundary_updates=sum('request_observations' in row for row in rows),
        request_boundary_scope='Captured request results only; native request calculations remain unverified.' if metadata['recorder_revision']>=3 else 'Not captured.',
        replay_blockers=([ 'Request-boundary replay still needs matching historical warmup and initial persistent/varip state.',
                          'Captured request results do not independently validate native request calculations; source input bindings are omitted.']
                         if metadata['recorder_revision']>=3 else ['No synchronized request-context updates or currency observations in recorder revisions 1/2.']) + [
                         'Chart warmup must be reconstructed from the recorded history_start.',
                         'Repeated confirmed executions and equal timestamps must retain their sequence identity; the generic bar replay/comparator cannot consume this trace directly.'])


def unpack(envelopes):
    sessions={};duplicates=0
    for e in envelopes:
        if not isinstance(e,dict):raise ValueError('Trace message must be an object')
        if e.get('brokeweb_trace')!=1 or e.get('pine_source_hash')!=PINE_HASH:raise ValueError('Unsupported trace protocol/source hash')
        for field in ('run_start','history_start','batch_id','first_seq','last_seq','dropped'):
            if not integer(e.get(field),1 if field in ('batch_id','first_seq','last_seq') else 0):raise ValueError(f'Invalid {field}')
        for field in ('exchange','symbol','timeframe','label'):
            if not isinstance(e.get(field),str) or not e[field]:raise ValueError(f'Missing {field}')
        if e['exchange']!='BYBIT' or not e['symbol'].endswith('.P'):raise ValueError('Expected a native Bybit perpetual chart')
        if not finite(e.get('tick_size')) or e['tick_size']<=0:raise ValueError('Invalid tick_size')
        if e.get('interval_ms')!=INTERVAL_MS:raise ValueError('Unexpected batch interval')
        parameters,repairs,revision=captured_parameters(e)
        if not isinstance(e.get('rows'),list) or (not e['rows'] and not e['dropped']):raise ValueError('Empty trace batch')
        identity={k:e[k] for k in ('run_start','history_start','label','exchange','symbol','timeframe','tick_size','parameters','pine_source_hash')}
        identity.update(parameters=parameters,recorder_revision=revision)
        if revision>=3:
            validate_routes(e.get('request_routes'))
            if not isinstance(e.get('quote_currency'),str) or not e['quote_currency'] or e.get('volume_type') not in ('base','quote','tick','n/a'):
                raise ValueError('Invalid request symbol metadata')
            identity.update({k:e[k] for k in ('request_routes','quote_currency','volume_type')})
        if repairs:identity.update(recorded_parameters=e['parameters'],parameter_repairs=repairs)
        key=(e['run_start'],e['label'],e['exchange'],e['symbol'],e['timeframe'])
        session=sessions.setdefault(key,{'identity':identity,'batches':{}})
        if session['identity']!=identity:raise ValueError('Session metadata changed; use separate captures')
        if e['batch_id'] in session['batches']:
            if session['batches'][e['batch_id']]!=e:raise ValueError('Conflicting duplicate batch')
            duplicates+=1;continue
        session['batches'][e['batch_id']]=e
    results=[]
    for session in sessions.values():
        ident=session['identity'];rows=[];issues=[];previous_seq=0;previous_batch=0;dropped=0;contiguous=True
        for batch,e in sorted(session['batches'].items()):
            if batch!=previous_batch+1 or e['first_seq']!=previous_seq+1:contiguous=False
            if batch!=previous_batch+1:issues.append(f'Missing initial/intermediate batch before {batch}')
            if e['first_seq']!=previous_seq+1:issues.append(f'Sequence discontinuity before batch {batch}')
            if e['last_seq']-e['first_seq']+1!=len(e['rows'])+e['dropped']:raise ValueError('Batch row/drop accounting mismatch')
            last=e['first_seq']-1
            for array in e['rows']:
                columns=CONTEXT_TRACE_COLUMNS if ident['recorder_revision']>=3 else TRACE_COLUMNS
                if not isinstance(array,list) or len(array)!=len(columns):raise ValueError('Trace row has wrong column count')
                r=dict(zip(columns,array))
                if ident['recorder_revision']>=3:
                    r['request_observations']=decode_observation(r['request_observations'])
                for field in ('seq','event_time','bar_start','bar_end','bar_index','bar_update','signal_mask'):
                    if not integer(r[field],1 if field in ('seq','bar_update') else 0):raise ValueError(f'Invalid row {field}')
                if not last<r['seq']<=e['last_seq'] or r['seq']<e['first_seq']:raise ValueError('Duplicate/unordered sequence')
                if any(not integer(r[k]) or r[k]>1 for k in ('confirmed','is_new')):raise ValueError('Invalid bar state')
                if r['signal_mask']>=1<<len(SIGNALS):raise ValueError('Unknown signal bit')
                if not r['bar_start']<=r['event_time'] or r['bar_end']<=r['bar_start'] or ident['history_start']>r['bar_start']:raise ValueError('Invalid observation time')
                for name,(_,kind) in METRICS.items():
                    v=r['PARITY_'+name]
                    if v is not None and not finite(v):raise ValueError('Invalid metric number')
                    if kind=='bool' and v not in (0,1):raise ValueError('Invalid boolean metric')
                    if kind=='discrete' and v is not None and v!=int(v):raise ValueError('Invalid discrete metric')
                o,h,l,c=(r['PARITY_'+k] for k in ('open','high','low','close'))
                if not all(finite(v) for v in (o,h,l,c,r['volume'])) or not l<=min(o,c)<=max(o,c)<=h or r['volume']<0:raise ValueError('Invalid trace OHLCV')
                if rows and (r['event_time']<rows[-1]['event_time'] or r['bar_start']<rows[-1]['bar_start']):raise ValueError('Nonmonotonic capture times')
                r.update(exchange=ident['exchange'],symbol=ident['symbol'].removesuffix('.P'),timeframe=ident['timeframe'],history_start=ident['history_start'],confirmed=bool(r['confirmed']),is_new=bool(r['is_new']))
                for i,name in enumerate(SIGNALS.values()):r['PARITY_'+name]=(r['signal_mask']>>i)&1
                rows.append(r);last=r['seq']
            dropped+=e['dropped'];previous_batch=batch;previous_seq=e['last_seq']
        if dropped:issues.append(f'{dropped} updates dropped by bounded Pine recorder')
        if not rows:raise ValueError('No observations received; all recorded updates were dropped')
        # No synthetic closing row is invented for the unflushed tail.
        incomplete_start=not rows[0]['is_new'];closed_bars=len({r['bar_start'] for r in rows if r['confirmed']})
        if incomplete_start:issues.append('Capture begins inside an already open candle')
        if not rows[-1]['confirmed']:issues.append('Last received candle is open')
        if ident.get('parameter_repairs'):issues.append('Legacy string escaping repaired for explicit enum settings; raw values retained in metadata')
        if '\r' in ident['label']:issues.append('Legacy capture label contains a carriage return; free-form text was not repaired')
        issues.append('Unsent tail after the last received batch is unknown')
        stamp=hashlib.sha256(json.dumps(ident,sort_keys=True,ensure_ascii=False).encode()).hexdigest()[:16]
        results.append({'session_id':stamp,'metadata':ident,'rows':rows,'report':{'status':'CAPTURE_IMPORTED','parity_status':'UNVERIFIED','rows':len(rows),'closed_bars':closed_bars,'reported_dropped_updates':dropped,'sequence_contiguous':contiguous and not dropped,'issues':issues,'omitted_parameters':OMITTED_PARAMETERS,'same_timestamp_updates':sum(a['event_time']==b['event_time'] for a,b in zip(rows,rows[1:])),'comparison_ready':False,'reason':'Recorded outputs need synchronized Python replay and request contexts; import alone does not prove parity.'}})
        received_contiguous=not dropped and all(b['seq']==a['seq']+1 for a,b in zip(rows,rows[1:]))
        results[-1]['report'].update(coverage(rows,ident),batches=len(session['batches']),
                                     received_sequence_contiguous=received_contiguous,
                                     missing_prefix_updates=rows[0]['seq']-1,
                                     session_id=stamp,symbol=ident['symbol'],timeframe=ident['timeframe'],
                                     history_start=ident['history_start'],recorder_revision=ident['recorder_revision'])
    return results,duplicates


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--input',required=True,type=Path);p.add_argument('--output-dir',required=True,type=Path);a=p.parse_args(argv)
    try:
        sessions,duplicates=unpack(messages(a.input))
        if not sessions:raise ValueError('No trace sessions found')
        for s in sessions:
            target=a.output_dir/s['session_id']
            if any((target/n).exists() for n in ('reference.jsonl','metadata.json','report.json')):
                raise ValueError('Output session exists; choose a new output directory')
        a.output_dir.mkdir(parents=True,exist_ok=True)
        for s in sessions:
            target=a.output_dir/s['session_id'];target.mkdir(exist_ok=True)
            s['report'].update(input_file=str(a.input),input_sha256=hashlib.sha256(a.input.read_bytes()).hexdigest(),duplicate_batches=duplicates)
            # Refuse accidental overwrite; an export remains independently reviewable.
            paths=[target/n for n in ('reference.jsonl','metadata.json','report.json')]
            if any(path.exists() for path in paths):raise ValueError('Output session exists; choose a new output directory')
            paths[0].write_text(''.join(json.dumps(r,ensure_ascii=False,allow_nan=False)+'\n' for r in s['rows']),encoding='utf-8')
            paths[1].write_text(json.dumps(s['metadata'],ensure_ascii=False,indent=2),encoding='utf-8')
            paths[2].write_text(json.dumps(s['report'],ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps({'status':'CAPTURE_IMPORTED','sessions':len(sessions),'duplicate_batches':duplicates,'parity_status':'UNVERIFIED'}));return 0
    except (ValueError,OSError) as exc:
        print(json.dumps({'status':'INVALID_INPUT','error':str(exc)},ensure_ascii=False));return 2


if __name__=='__main__':raise SystemExit(main())
