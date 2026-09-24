"""Audit multi-symbol alert logs and optionally run bounded native TA diagnostics.

No snapshots, rules or runtime settings are written. Request contexts, stateful
signals and TradingView recursive seeds remain unverified, even on a TA match.
"""
import argparse
import asyncio
from collections import Counter
import hashlib
import json
import math
from pathlib import Path

from backend.engine.interpreter import tf_seconds
from backend.engine.interpreter import Execution
from backend.engine.syntax import load_program
from backend.engine.runtime import PINE_HASH, ENGINE_VERSION, SIGNALS
from backend.marketdata import BybitAdapter
from .native import dump
from .trace import messages, unpack
from .trace_prices import check, FIELDS


def zero_volume_hypothesis(session,bars):
    """Counterfactual bar selection, never fed into a parity acceptance result."""
    rows=session['rows'];m=session['metadata']
    if len({r['bar_start'] for r in rows})!=1:
        return {'status':'NOT_APPLICABLE','reason':'Hypothesis is limited to a single captured candle'}
    retained=[b for b in bars if b['volume']>0]
    if len(retained)==len(bars) or not retained:
        return {'status':'NOT_APPLICABLE','reason':'No meaningful zero-volume alternative'}
    program=load_program();statements=[s for s in program.statements if s.kind=='assign' and s.meta['name'] in FIELDS.values()]
    ex=Execution(program,m['parameters'],f'BYBIT:{m["symbol"]}',m['timeframe'],m['tick_size'])
    for bar in retained:
        ex.begin(bar);ex.execute(statements);ex.commit()
    errors={k:[] for k in FIELDS}
    for row in rows:
        bar=dict(start=row['bar_start'],end=row['bar_end'],received_at=row['event_time'],confirmed=row['confirmed'],
            volume=row['volume'],**{k:row['PARITY_'+k] for k in ('open','high','low','close')})
        ex.begin(bar,realtime=True);ex.execute(statements)
        for field,variable in FIELDS.items():
            ref=row['PARITY_'+field];actual=ex.lookup(variable)
            if ref is None or actual is None or not math.isfinite(ref) or not math.isfinite(actual):
                return {'status':'UNAVAILABLE','reason':'Nonfinite TA pair'}
            errors[field].append(abs(ref-actual)/max(abs(ref),1e-12))
    maxima={field:max(values) for field,values in errors.items()}
    return {'status':'HYPOTHESIS_MATCH' if all(v<=.0005 for v in maxima.values()) else 'HYPOTHESIS_MISMATCH',
        'scope':'Counterfactual omission of native zero-volume history; no TradingView historical OHLCV proof; original mismatch retained.',
        'removed_bars':len(bars)-len(retained),'retained_bars':len(retained),'relative_error_max':maxima}


def signal_coverage(rows):
    result={}
    for name in SIGNALS.values():
        field='PARITY_'+name;positive=edges=unknown=0
        previous=None
        for row in rows:
            if row[field]:
                positive+=1
                if previous is None or row['seq']!=previous['seq']+1:unknown+=1
                elif not previous[field]:edges+=1
            previous=row
        if positive:result[name]={'positive_updates':positive,'observed_rising_edges':edges,'positive_after_unknown_prefix_or_gap':unknown}
    return result


def audit(sessions,duplicates):
    items=[]
    for session in sorted(sessions,key=lambda s:(s['metadata']['symbol'],s['metadata']['run_start'])):
        r=session['report'];m=session['metadata'];rows=session['rows']
        item={k:r[k] for k in ('session_id','symbol','timeframe','recorder_revision','rows','batches',
            'closed_bars','complete_bars','reported_dropped_updates','missing_prefix_updates',
            'received_sequence_contiguous','sequence_contiguous','active_setup_updates',
            'event_start_utc','event_end_utc','duration_seconds','nondefault_parameters')}
        item.update(history_start=m['history_start'],label=m['label'],signals=signal_coverage(rows))
        item.update({key:r.get(key,0 if key!='bar_coverage' else []) for key in
            ('request_boundary_updates','repeated_confirmed_updates','bar_coverage')})
        item['origin_grid_matches']=all(row['bar_start']-m['history_start']==row['bar_index']*tf_seconds(m['timeframe'])*1000 for row in rows)
        item['stateful_replay_blockers']=[]
        if m['recorder_revision']<3:item['stateful_replay_blockers'].append('REQUEST_CONTEXTS_NOT_CAPTURED')
        if not r['sequence_contiguous']:item['stateful_replay_blockers'].append('SEQUENCE_INCOMPLETE')
        if not r['complete_bars']:item['stateful_replay_blockers'].append('NO_COMPLETE_CANDLE')
        if not item['origin_grid_matches']:item['stateful_replay_blockers'].append('HISTORY_INDEX_NOT_WALL_CLOCK_GRID')
        item['stateful_replay_blockers'].append('INITIAL_STATE_UNVERIFIED')
        items.append(item)
    signals={}
    for item in items:
        for name,counts in item['signals'].items():
            total=signals.setdefault(name,Counter());total.update(counts);total['sessions']+=1
    return {'status':'CAPTURE_IMPORTED','parity_status':'UNVERIFIED','full_intrabar_status':'UNVERIFIED',
        'pine_source_hash':PINE_HASH,'engine_version':ENGINE_VERSION,'sessions':len(items),
        'symbols':len({r['symbol'] for r in items}),'timeframes':dict(Counter(r['timeframe'] for r in items)),
        'recorder_revisions':dict(Counter(r['recorder_revision'] for r in items)),'duplicate_batches':duplicates,
        **{key:sum(r[key] for r in items) for key in ('rows','batches','closed_bars','complete_bars','reported_dropped_updates','active_setup_updates','request_boundary_updates','repeated_confirmed_updates')},
        'sessions_with_active_setups':sum(bool(r['active_setup_updates']) for r in items),
        'sessions_with_gaps_or_drops':sum(not r['received_sequence_contiguous'] for r in items),
        'sessions_with_missing_prefix':sum(bool(r['missing_prefix_updates']) for r in items),
        'event_start_utc':min(r['event_start_utc'] for r in items),'event_end_utc':max(r['event_end_utc'] for r in items),
        'signals':signals,'items':items,
        'limitations':['Positive flags are sampled latched states, not delivered alerts or unique events.',
            'Observed 0->1 transitions exclude unknown prefixes/gaps; they do not establish signal parity.',
            'No full intrabar PASS from capture import or bounded ATR/EMA diagnostics.',
            'Unknown unsent tails are not reconstructed.']}


async def diagnose(session,adapter,target):
    r=session['report'];m=session['metadata']
    if not r['received_sequence_contiguous'] or r['reported_dropped_updates']:
        return {'status':'NOT_ELIGIBLE','reason':'Trace has internal gaps or recorder drops'}
    target.mkdir(parents=True,exist_ok=True)
    warmup_path=target/'warmup.json'
    if warmup_path.exists():bars=json.loads(warmup_path.read_text())
    else:
        start=session['rows'][0]['bar_start'];step=tf_seconds(m['timeframe'])*1000
        depth=min((start-m['history_start'])//step,20*max(m['parameters'][k] for k in ('atrLen','emaFastLen','emaSlowLen')))
        native=await adapter.backfill(m['symbol'].removesuffix('.P'),m['timeframe'],depth,end=start-1)
        bars=[bar.to_dict() for bar in native if bar.start>=m['history_start'] and bar.end<=start]
        dump(warmup_path,bars)
    actual,report=await asyncio.to_thread(check,session,bars,bounded=True)
    if report['status']=='DIAGNOSTIC_MISMATCH':
        report['zero_volume_hypothesis']=await asyncio.to_thread(zero_volume_hypothesis,session,bars)
    report['warmup_sha256']=hashlib.sha256(warmup_path.read_bytes()).hexdigest()
    dump(target/'ta-report.json',report)
    (target/'python.jsonl').write_text(''.join(json.dumps(row,allow_nan=False)+'\n' for row in actual))
    return report


def clean_bar_suffix(session):
    """Select an intact suffix for TA only; never repair the original sequence."""
    rows=session['rows']
    last_gap=max((i for i in range(1,len(rows)) if rows[i]['seq']!=rows[i-1]['seq']+1),default=0)
    complete={b['bar_start'] for b in session['report'].get('bar_coverage',[]) if b['complete']}
    start=next((i for i in range(last_gap,len(rows)) if rows[i]['is_new'] and rows[i]['bar_start'] in complete),None)
    if start is None:return None
    selected=rows[start:]
    return {**session,'rows':selected,'report':{**session['report'],
        'rows':len(selected),'received_sequence_contiguous':True,'sequence_contiguous':False,
        'reported_dropped_updates':0,'missing_prefix_updates':selected[0]['seq']-1}}


async def diagnose_clean_bars(session,adapter,target):
    selected=clean_bar_suffix(session)
    if selected is None:return {'status':'NOT_ELIGIBLE','reason':'No complete candle in the contiguous suffix'}
    result=await diagnose(selected,adapter,target)
    result.update(selection_scope='TA-only contiguous suffix beginning at a complete candle; no stateful replay or repaired capture.',
        first_seq=selected['rows'][0]['seq'],last_seq=selected['rows'][-1]['seq'],
        excluded_received_updates=len(session['rows'])-len(selected['rows']),
        original_reported_dropped_updates=session['report']['reported_dropped_updates'])
    dump(target/'ta-report.json',result)
    return result


async def run(args):
    sha=hashlib.sha256(args.input.read_bytes()).hexdigest()
    sessions,duplicates=unpack(messages(args.input));report=audit(sessions,duplicates)
    report.update(input_file=str(args.input),input_sha256=sha)
    manifest={'input_sha256':sha,'engine_version':ENGINE_VERSION,'diagnostic':'bounded TA 20x longest length'}
    if args.output_dir.exists():
        path=args.output_dir/'manifest.json'
        if not args.resume or not path.exists() or json.loads(path.read_text())!=manifest:
            raise ValueError('Choose a new output directory, or resume the same input/engine')
    args.output_dir.mkdir(parents=True,exist_ok=True);dump(args.output_dir/'manifest.json',manifest)
    dump(args.output_dir/'report.json',report)
    if args.native_ta or args.clean_bar_ta:
        adapter=BybitAdapter();limit=asyncio.Semaphore(args.concurrency);completed=0
        async def one(session):
            nonlocal completed
            async with limit:
                try:
                    diagnose_fn=diagnose_clean_bars if args.clean_bar_ta else diagnose
                    target=args.output_dir/session['session_id']
                    result=await diagnose_fn(session,adapter,target/'clean-bars' if args.clean_bar_ta else target)
                except Exception as exc:result={'status':'UNAVAILABLE','reason':str(exc),'error_type':type(exc).__name__}
                completed+=1
                print(f"TA {completed}/{len(sessions)} {session['metadata']['symbol']}: {result['status']}",flush=True)
                return session['session_id'],result
        try:results=dict(await asyncio.gather(*(one(session) for session in sessions)))
        finally:await adapter.close()
        report['ta_selection']='clean_bar_suffix' if args.clean_bar_ta else 'whole_received_session'
        for item in report['items']:item['ta_diagnostic']=results[item['session_id']]
        report['ta_diagnostic_status_counts']=dict(Counter(r['status'] for r in results.values()))
        report['ta_diagnostic_matched_updates']=sum(r.get('matched_updates',0) for r in results.values() if r['status']=='DIAGNOSTIC_MATCH')
        report['ta_warmup_scope']='Up to 20x longest TA length; original recursive seed not established. Strict origin replay unchanged.'
        dump(args.output_dir/'report.json',report)
    print(json.dumps({k:v for k,v in report.items() if k!='items'},ensure_ascii=False,indent=2))
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input',type=Path,required=True);parser.add_argument('--output-dir',type=Path,required=True)
    parser.add_argument('--native-ta',action='store_true');parser.add_argument('--resume',action='store_true')
    parser.add_argument('--clean-bar-ta',action='store_true',help='TA only on a continuous suffix starting at a complete candle; preserves original gap blockers')
    parser.add_argument('--concurrency',type=int,default=3);args=parser.parse_args()
    if not 1<=args.concurrency<=5:parser.error('concurrency must be 1..5')
    try:report=asyncio.run(run(args))
    except (OSError,ValueError) as exc:
        print(json.dumps({'status':'INVALID_INPUT','error':str(exc)}));return 2
    return 1 if report.get('ta_diagnostic_status_counts',{}).get('DIAGNOSTIC_MISMATCH') else 0


if __name__=='__main__':raise SystemExit(main())
