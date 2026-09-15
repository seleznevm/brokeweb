"""request.security gaps_off/lookahead_off and lower-TF array execution."""
from __future__ import annotations
from collections import deque
from .interpreter import Execution, tf_seconds, qualified
from .values import NA, is_na, encode, decode

class ContextProvider:
    def __init__(self,engine):self.engine=engine;self.streams={}
    def __call__(self,parent,call,name,nodes):
        symbol=parent.eval(nodes[0]);tf=str(parent.eval(nodes[1]));expr=nodes[2]
        lower=name.endswith('lower_tf')
        returned = parent.program.functions[qualified(expr.args[0])].body[-1].expr if expr.kind == 'call' and qualified(expr.args[0]) in parent.program.functions else expr
        n=len(returned.args) if returned.kind=='list' else 1
        if lower and tf_seconds(tf)>=tf_seconds(parent.timeframe):return [[] for _ in range(n)]
        context_key=f'{symbol}|{tf}';key=f'{call.uid}|{context_key}'
        if key not in self.streams:
            ex=Execution(parent.program,parent.parameters,symbol,tf,parent.tick_size,parent.history_limit);ex.request_provider=self
            self.streams[key]=(ex,deque(maxlen=parent.history_limit))
        ex,results=self.streams[key]
        bars=self.engine.contexts.get(context_key,[])
        if tf==parent.timeframe and symbol==parent.symbol:
            bars=self.engine.chart_bars+[parent.bar]
        asof=parent.bar.get('received_at',parent.bar['end']) if parent.realtime else parent.bar['end']
        asof=min(asof,parent.bar['end'])
        last=ex.last_value
        for raw in bars:
            bar=raw.to_dict() if hasattr(raw,'to_dict') else raw
            if ex.last_start is not None and bar['start']<=ex.last_start:continue
            closed=bar.get('confirmed',True) and bar['end']<=asof
            open_available=parent.realtime and not lower and not bar.get('confirmed',True) and bar['start']<=asof and bar.get('received_at',asof)<=asof
            if not closed and not open_available:continue
            ex.begin(bar,realtime=parent.realtime and not closed,outer=parent.scopes[0]);ex.special['barstate.isconfirmed']=closed
            last=ex.eval(expr)
            if closed:
                ex.last_value=last;ex.commit();results.append((bar['start'],bar['end'],last))
        if lower:
            inside=[value for start,end,value in results if start>=parent.bar['start'] and end<=asof]
            if not inside:
                if not bars or asof-parent.bar['start'] >= tf_seconds(tf)*1000:parent.missing.add(context_key)
                return [[] for _ in range(n)]
            return [list(values) for values in zip(*inside)]
        if is_na(last):
            parent.missing.add(context_key)
            return [NA]*n if returned.kind == 'list' else NA
        # Gaps off retains last confirmed value, but health still exposes stale context.
        if not results or asof-results[-1][1]>tf_seconds(tf)*1000:
            parent.missing.add(context_key)
        return last
    def export_state(self):return {key:{'state':ex.export_state(),'results':encode(list(results)),'symbol':ex.symbol,'timeframe':ex.timeframe} for key,(ex,results) in self.streams.items()}
    def restore_state(self,payload):
        self.streams={}
        for key,value in payload.items():
            ex=Execution(self.engine.runtime.program,self.engine.parameters,value['symbol'],value['timeframe'],self.engine.tick_size,self.engine.runtime.history_limit);ex.restore_state(value['state']);ex.request_provider=self
            self.streams[key]=(ex,deque(decode(value['results']),maxlen=ex.history_limit))
