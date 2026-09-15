"""Replay supplied market fixtures through the pinned source engine.

This produces Python observations, never TradingView reference observations.
Usage: python -m tools.pine_reference.replay --input fixture.json --output python.jsonl
Capture: python -m tools.pine_reference.replay --capture --symbol SYMBOL --timeframe 15 --output fixture.json
"""
from __future__ import annotations
import argparse
import asyncio
import hashlib
import json
import math
from pathlib import Path
import time
from typing import Iterator
from backend.engine.runtime import PineEngine, SIGNALS, PINE_HASH, ENGINE_VERSION
from backend.engine.parameters import validate_parameters, required_history
from backend.marketdata import BybitAdapter, BinanceBtcContextAdapter
from backend.marketdata.timeframes import bounds
from backend.engine.pine_compat import timeframe_seconds
from .catalog import METRICS, PATHS
from .exporter import action_codes


class FixtureError(ValueError):
    pass


def finite_number(value):
    return not isinstance(value,bool) and isinstance(value,(int,float)) and math.isfinite(value)


def normalize_bar(raw: dict, symbol: str, timeframe: str) -> dict:
    bar=dict(raw)
    for field in ('start','end'):
        if field=='end' and field not in bar:
            bar[field]=bounds(bar['start'],timeframe)[1]
        if not finite_number(bar.get(field)) or int(bar[field])!=bar[field]:
            raise FixtureError(f'{field} must be an integer UTC millisecond timestamp')
        bar[field]=int(bar[field])
    if bar['start']<0 or bar['end']<=bar['start']:
        raise FixtureError('Invalid bar interval')
    if bounds(bar['start'],timeframe)!=(bar['start'],bar['end']):
        raise FixtureError(f'Bar boundaries are not aligned to {timeframe}: {bar["start"]}')
    for field in ('open','high','low','close','volume'):
        if not finite_number(bar.get(field)):
            raise FixtureError(f'{field} must be finite numeric OHLCV')
    if bar['low']>min(bar['open'],bar['close']) or bar['high']<max(bar['open'],bar['close']) or bar['volume']<0:
        raise FixtureError('Invalid OHLCV geometry or negative volume')
    if 'symbol' in bar and bar['symbol']!=symbol:
        raise FixtureError('Fixture contains another chart symbol')
    if 'timeframe' in bar and str(bar['timeframe'])!=timeframe:
        raise FixtureError('Fixture contains another chart timeframe')
    if 'confirmed' in bar and not isinstance(bar['confirmed'],bool):
        raise FixtureError('confirmed must be boolean')
    bar.update(symbol=symbol,timeframe=timeframe,confirmed=bar.get('confirmed',True))
    return bar


def normalized_contexts(streams: dict) -> dict:
    result={}
    for key,rows in streams.items():
        if '|' not in key or ':' not in key:
            raise FixtureError(f'Invalid context key {key!r}; expected EXCHANGE:SYMBOL.P|TF')
        identity,tf=key.rsplit('|',1)
        symbol=identity.split(':',1)[1].removesuffix('.P')
        bars=[normalize_bar(row,symbol,tf) for row in rows]
        # Missing 30S samples remain explicit; they are not synthesized. Other
        # missing native candles are input errors and cannot be silently replayed.
        for previous,current in zip(bars,bars[1:]):
            if current['start']<=previous['start']:
                raise FixtureError(f'Context {key} must be strictly chronological')
            if current['start']!=previous['end'] and tf!='30S':
                raise FixtureError(f'Internal context gap in {key}: {previous["end"]} to {current["start"]}')
        result[key]=bars
    return result


def numeric_columns(snapshot: dict) -> dict:
    metrics=snapshot.get('metrics',{})
    codes=action_codes()
    output={}
    for name,(pine,kind) in METRICS.items():
        if name=='action':value=codes.get(metrics.get('actionText',snapshot.get('action')))
        elif name=='candidate_path':value=PATHS.get(metrics.get('candidateEntryPath',snapshot.get('candidate_path')))
        elif name=='trigger_path':value=PATHS.get(metrics.get('entryPath',snapshot.get('trigger_path')))
        else:value=metrics.get(pine)
        if kind=='bool':value=None if value is None else int(bool(value))
        output['PARITY_'+name]=value if finite_number(value) else None
    for pine,name in SIGNALS.items():
        value=metrics.get(pine)
        output['PARITY_'+name]=int(bool(value)) if value is not None else int(name in snapshot.get('signals',[]))
    return output


def replay_fixture(fixture: dict) -> Iterator[dict]:
    """Yield canonical snapshots plus numeric PARITY columns.

    Repeated starts model realtime updates; closing updates continue varip state.
    Each row may be a bar directly or {bar, realtime, event_time, contexts}.
    Per-update contexts replace the specified stream, retaining other streams.
    """
    symbol=fixture.get('symbol');timeframe=str(fixture.get('timeframe',''))
    if not isinstance(symbol,str) or not symbol or not timeframe:
        raise FixtureError('symbol and timeframe are required')
    tick=fixture.get('tick_size',.01)
    if not finite_number(tick) or tick<=0:raise FixtureError('tick_size must be positive')
    parameters=validate_parameters(fixture.get('parameters',{}))
    rows=fixture.get('bars')
    if not isinstance(rows,list) or not rows:raise FixtureError('bars must be a nonempty list')
    contexts=normalized_contexts(fixture.get('contexts',{}))
    engine=PineEngine(symbol,timeframe,tick,parameters)
    fingerprint=hashlib.sha256(json.dumps(fixture,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()).hexdigest()
    previous=None;last_event=None
    for index,row in enumerate(rows):
        raw=row.get('bar',row)
        bar=normalize_bar(raw,symbol,timeframe)
        explicit_realtime=row.get('realtime',raw.get('realtime'))
        if explicit_realtime is not None and not isinstance(explicit_realtime,bool):raise FixtureError('realtime must be boolean')
        same_open=previous is not None and bar['start']==previous['start'] and not previous['confirmed']
        realtime=explicit_realtime if explicit_realtime is not None else not bar['confirmed'] or same_open
        if not bar['confirmed'] and not realtime:raise FixtureError('An unconfirmed candle must execute in realtime mode')
        # REST received_at is download time (pagination runs backwards), not a
        # historical bar observation. Only realtime replay uses it as a fallback.
        event_time=row.get('event_time',raw.get('event_time'))
        if event_time is None and realtime:event_time=raw.get('received_at')
        if event_time is None:
            if realtime:raise FixtureError('Realtime updates require event_time or received_at in UTC milliseconds')
            event_time=bar['end']
        if not finite_number(event_time) or int(event_time)!=event_time or event_time<bar['start']:
            raise FixtureError('Invalid event_time timestamp')
        if last_event is not None and event_time<last_event:raise FixtureError('Event timestamps must be monotonic')
        if previous:
            if bar['start']<previous['start']:raise FixtureError('Chart bars must be chronological')
            if bar['start']==previous['start']:
                if previous['confirmed']:raise FixtureError('Cannot revise a committed bar; replay from an earlier fixture/checkpoint')
                if not realtime:raise FixtureError('Closing an open candle must preserve realtime execution state')
            else:
                if not previous['confirmed']:raise FixtureError('Cannot advance to another candle before confirming the open candle')
                if bar['start']!=previous['end']:raise FixtureError(f'Internal chart history gap: {previous["end"]} to {bar["start"]}')
        updates=row.get('contexts',raw.get('contexts'))
        if updates is not None:contexts.update(normalized_contexts(updates))
        bar['received_at']=int(event_time)
        for key in ('contexts','realtime','event_time'):bar.pop(key,None)
        snapshot=engine.update(bar,contexts,realtime)
        from .native import plot_values
        yield {**snapshot,**numeric_columns(snapshot),'source_plots':plot_values(engine.runtime),'replay_input_sha256':fingerprint,'replay_index':index,'replay_realtime':realtime,'replay_event_time':int(event_time)}
        previous=bar;last_event=event_time


def write_replay(fixture: dict, output: Path) -> int:
    output.parent.mkdir(parents=True,exist_ok=True)
    temporary=output.with_name(output.name+'.partial')
    count=0
    try:
        with temporary.open('w',encoding='utf-8') as handle:
            for row in replay_fixture(fixture):
                handle.write(json.dumps(row,ensure_ascii=False,allow_nan=False,separators=(',',':'))+'\n');count+=1
        temporary.replace(output)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return count


async def capture_fixture(symbol: str, timeframe: str, count: int | None=None, parameters: dict | None=None) -> dict:
    """Capture only actual confirmed exchange bars; no TradingView impersonation."""
    from backend.worker import context_requirements
    parameters=validate_parameters(parameters or {})
    count=count or required_history(parameters,timeframe)
    if count<1:raise FixtureError('count must be positive')
    bybit=BybitAdapter();btc=BinanceBtcContextAdapter()
    try:
        instruments={item.symbol:item for item in await bybit.instruments()}
        if symbol not in instruments:raise FixtureError(f'{symbol} is not a currently eligible Bybit USDT perpetual')
        chart=[b for b in await bybit.backfill(symbol,timeframe,count+1) if b.confirmed][-count:]
        if not chart:raise FixtureError('No confirmed chart bars returned')
        end=chart[-1].end-1
        span=chart[-1].end-chart[0].start
        own,bitcoin=context_requirements(parameters,[timeframe])
        contexts={};warnings=[]
        for exchange,tfs,adapter,ticker in [('BYBIT',own,bybit,symbol),('BINANCE',bitcoin,btc,'BTCUSDT')]:
            for tf in sorted(tfs,key=timeframe_seconds):
                key=f'{exchange}:{ticker}.P|{tf}'
                if tf=='30S':
                    warnings.append('Historical 30S public trades are unavailable from native REST klines; micro context omitted without fabrication.')
                    continue
                depth=max(required_history(parameters,tf),math.ceil(span/(timeframe_seconds(tf)*1000))+500)
                bars=await adapter.backfill(ticker,tf,depth,end=end)
                contexts[key]=[b.to_dict() for b in bars if b.confirmed and b.end<=end+1]
        return {'schema_version':1,'symbol':symbol,'timeframe':timeframe,'tick_size':instruments[symbol].tick_size,'parameters':parameters,'bars':[b.to_dict() for b in chart],'contexts':contexts,'provenance':{'kind':'exchange_market_fixture','tradingview_reference':False,'captured_at':int(time.time()*1000),'pine_source_hash':PINE_HASH,'engine_version':ENGINE_VERSION,'requested_chart_bars':count,'actual_chart_bars':len(chart),'warnings':warnings}}
    finally:
        await bybit.close();await btc.close()


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input',type=Path)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--capture',action='store_true')
    parser.add_argument('--symbol')
    parser.add_argument('--timeframe',default='15')
    parser.add_argument('--count',type=int)
    parser.add_argument('--parameters',type=Path)
    args=parser.parse_args(argv)
    try:
        if args.capture:
            if not args.symbol:parser.error('--capture requires --symbol')
            if args.input:parser.error('--capture cannot use --input')
            parameters=json.loads(args.parameters.read_text()) if args.parameters else {}
            fixture=asyncio.run(capture_fixture(args.symbol,args.timeframe,args.count,parameters))
            args.output.parent.mkdir(parents=True,exist_ok=True)
            args.output.write_text(json.dumps(fixture,ensure_ascii=False,allow_nan=False,indent=2),encoding='utf-8')
            print(json.dumps({'status':'CAPTURED_MARKET_DATA','bars':len(fixture['bars']),'output':str(args.output),'tradingview_reference':False}))
        else:
            if not args.input:parser.error('--input is required for replay')
            if args.input.resolve()==args.output.resolve():parser.error('--input and --output must differ')
            fixture=json.loads(args.input.read_text(encoding='utf-8-sig'))
            count=write_replay(fixture,args.output)
            print(json.dumps({'status':'PYTHON_REPLAY_COMPLETE','records':count,'output':str(args.output),'parity_status':'UNVERIFIED'}))
        return 0
    except (ValueError,OSError,RuntimeError) as exc:
        print(json.dumps({'status':'ERROR','error':str(exc)},ensure_ascii=False))
        return 2


if __name__=='__main__':raise SystemExit(main())
