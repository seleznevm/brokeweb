"""Source-order Pine executor with separate committed, working and varip stores."""
from __future__ import annotations
import copy
import math
from collections import deque
from .syntax import Expr, Statement, load_program
from .values import NA, Namespace, Record, binary, encode, decode, is_na, truth

NAMESPACES={'ta','math','array','input','color','str','timeframe','syminfo','barstate','barmerge','request','format','display','plot','shape','location','size','position','box','label','table','alert','xloc','yloc','extend','line'}
COLORS={'red':'#f23645','green':'#089981','lime':'#00e676','orange':'#ff9800','yellow':'#ffeb3b','white':'#ffffff','black':'#000000','gray':'#787b86','aqua':'#00bcd4','blue':'#2196f3','purple':'#9c27b0','teal':'#00897b','silver':'#b2b5be','maroon':'#880e4f','navy':'#311b92','fuchsia':'#e040fb','olive':'#808000'}

class PineRuntimeError(RuntimeError): pass
class BreakLoop(Exception): pass
class ContinueLoop(Exception): pass

def tf_seconds(tf):
    s=str(tf)
    if s.endswith('S'): return int(s[:-1] or 1)
    if s.endswith('D'): return int(s[:-1] or 1)*86400
    if s.endswith('W'): return int(s[:-1] or 1)*604800
    if s.endswith('M'): return int(s[:-1] or 1)*2592000
    return int(s)*60

def qualified(expr):
    if expr.kind=='name': return expr.value
    if expr.kind=='attr':
        base=qualified(expr.args[0]); return f'{base}.{expr.value}' if base else None
    return None

class Execution:
    def __init__(self,program=None,parameters=None,symbol='BYBIT:BTCUSDT.P',timeframe='15',tick_size=0.01,history_limit=2600):
        self.program=program or load_program(); self.parameters=parameters or {}; self.symbol=symbol; self.timeframe=str(timeframe); self.tick_size=tick_size
        self.history_limit=history_limit; self.histories={}; self.persistent={}; self.varip={}; self.count=0; self.last_start=None; self.open_start=None
        self.requests={}; self.request_provider=None; self.contexts={}; self.last_value=NA
        self.initial={}; self.missing=set()
        self.history_names={'close'}; self.history_exprs=set()
        def scan_expr(node):
            if node.kind=='history':
                target=node.args[0]
                if target.kind=='name':self.history_names.add(target.value)
                else:self.history_exprs.add(target.uid)
            for child in node.args:scan_expr(child)
        def scan(nodes):
            for node in nodes:
                if node.expr:scan_expr(node.expr)
                scan(node.body);scan(node.otherwise)
        scan(self.program.statements)
        for fn in self.program.functions.values():scan(fn.body)
    def begin(self,bar,realtime=False,outer=None):
        self.bar=bar; self.realtime=realtime; self.current={}; self.work_persistent=copy.deepcopy(self.persistent)
        self.scopes=[dict(outer or {})]; self.paths=['g']; self.callpath=''; self.declarations={}; self.varip_names={}; self.capture=[]; self.alertconditions=[]; self.research_completed=[]
        self.missing=set()
        new=not realtime or self.open_start!=bar['start']
        self.open_start=bar['start']
        self.scopes[0].update(self.parameters)
        o,h,l,c,v=(bar[k] for k in ('open','high','low','close','volume'))
        self.scopes[0].update(open=o,high=h,low=l,close=c,volume=v,hlc3=(h+l+c)/3,hl2=(h+l)/2,ohlc4=(o+h+l+c)/4,bar_index=self.count,time=bar['start'],time_close=bar['end'],timenow=bar.get('received_at',bar['end']))
        self.special={'syminfo.mintick':self.tick_size,'syminfo.tickerid':self.symbol,'syminfo.ticker':self.symbol.split(':')[-1], 'syminfo.currency':'USDT','syminfo.basecurrency':self.symbol.split(':')[-1].removesuffix('USDT.P'),'syminfo.volumetype':'base','timeframe.period':self.timeframe,
          'barstate.isconfirmed':bar.get('confirmed',True),'barstate.isnew':new,'barstate.isfirst':self.count==0,'barstate.islast':realtime,'barstate.isrealtime':realtime,'barstate.ishistory':not realtime}
    def key(self,name,index=None):
        selected = index if index is not None else len(self.paths)-1
        return f'/g/{name}' if selected == 0 else f'{self.callpath}/{self.paths[selected]}/{name}'
    def lookup(self,name):
        if name=='na': return NA
        if name=='true': return True
        if name=='false': return False
        for scope in reversed(self.scopes):
            if name in scope:return scope[name]
        if name in NAMESPACES or name in self.program.types:return Namespace(name)
        raise PineRuntimeError(f'Undefined Pine variable {name}')
    def name_key(self,name):
        for i in range(len(self.scopes)-1,-1,-1):
            if name in self.scopes[i]: return self.key(name,i)
        return self.key(name,0)
    def remember(self,key,value): self.current[key]=value; return value
    def previous(self,key,offset=1):
        if offset==0:return self.current.get(key,NA)
        values=self.histories.get(key,())
        return values[-offset] if offset>0 and len(values)>=offset else NA
    def eval(self,e):
        k=e.kind
        if k=='literal':return e.value
        if k=='name': return self.lookup(e.value)
        if k=='list':return [self.eval(x) for x in e.args]
        if k=='attr':
            name=qualified(e)
            if name in self.special:return self.special[name]
            obj=self.eval(e.args[0])
            if isinstance(obj,Record): return obj.fields[e.value]
            if isinstance(obj,Namespace):return COLORS.get(e.value,f'{obj.name}.{e.value}') if obj.name=='color' else f'{obj.name}.{e.value}'
            raise PineRuntimeError(f'Not a record: {name} ({obj})')
        if k=='unary':
            a=self.eval(e.args[0])
            if e.value=='not':return not truth(a)
            return NA if is_na(a) else (-a if e.value=='-' else +a)
        if k=='binary':
            a=self.eval(e.args[0])
            if e.value=='and':return truth(a) and truth(self.eval(e.args[1]))
            if e.value=='or':return truth(a) or truth(self.eval(e.args[1]))
            return binary(e.value,a,self.eval(e.args[1]))
        if k=='ternary':return self.eval(e.args[1] if truth(self.eval(e.args[0])) else e.args[2])
        if k=='history':
            node,offset=e.args; offset=self.eval(offset)
            if is_na(offset):return NA
            offset=int(offset)
            if offset<0:raise PineRuntimeError('Negative history offset')
            value=self.eval(node)
            key=self.name_key(node.value) if node.kind=='name' else f'{self.callpath}/expr/{node.uid}'
            self.remember(key,value)
            historical=value if offset==0 else self.previous(key,offset)
            return False if isinstance(value,bool) and is_na(historical) else historical
        if k=='call':return self.call(e)
        raise PineRuntimeError(f'Unknown expression {k}')
    def execute(self,nodes,new_scope=False,scope_id=''):
        if new_scope:self.scopes.append({});self.paths.append(scope_id)
        result=NA
        try:
            for st in nodes:
                try:result=self.statement(st)
                except (BreakLoop,ContinueLoop):raise
                except Exception as ex:
                    if isinstance(ex,PineRuntimeError) and 'Pine L' in str(ex):raise
                    raise PineRuntimeError(f'Pine L{st.line}: {st.text[:160]} — {ex}') from ex
            return result
        finally:
            if new_scope:
                for name,value in self.scopes[-1].items():self.remember(self.key(name),value)
                self.scopes.pop();self.paths.pop()
    def statement(self,st):
        if st.kind=='expression':return self.eval(st.expr)
        if st.kind=='assign':
            m=st.meta; name=m['name']; mode=m['mode']; op=m['op']; key=self.key(name)
            store=self.varip if mode=='varip' else self.work_persistent
            if mode in ('var','varip') and key in store: value=store[key]
            elif st.expr.kind == 'call' and str(qualified(st.expr.args[0])).startswith('input.') and name in self.parameters:
                value = self.parameters[name]
                if qualified(st.expr.args[0]) == 'input.source': value = self.lookup(value)
            else:value=self.eval(st.expr)
            if m['type']=='bool':value=truth(value)
            if name.startswith('['):
                names=[s.strip() for s in name[1:-1].split(',')]
                if not isinstance(value,(tuple,list)) or len(value)!=len(names):raise PineRuntimeError(f'Tuple mismatch {names}: {value}')
                for n,v in zip(names,value):self.scopes[-1][n]=v
                return value
            if '.' in name:
                obj,field=name.split('.',1); record=self.lookup(obj)
                record.fields[field]=binary(op[0],record.fields[field],value) if op in ('+=','-=','*=','/=') else value
                return record.fields[field]
            target=len(self.scopes)-1
            if op!='=':
                for i in range(len(self.scopes)-1,-1,-1):
                    if name in self.scopes[i]:target=i;break
                else: raise PineRuntimeError(f'Reassignment of undeclared {name}')
                if op!=':=':value=binary(op[0],self.scopes[target][name],value)
            self.scopes[target][name]=value
            target_key=self.key(name,target)
            if mode in ('var','varip'):
                self.declarations[target_key]=mode;store[target_key]=value
            if target_key in self.work_persistent:self.work_persistent[target_key]=value
            if target_key in self.varip:self.varip[target_key]=value
            return value
        if st.kind=='if':return self.execute(st.body if truth(self.eval(st.expr)) else st.otherwise,True,f'block{st.line}')
        if st.kind=='for':
            start,end=self.eval(st.meta['start']),self.eval(st.meta['end'])
            if is_na(start) or is_na(end):return NA
            step=int(self.eval(st.meta['step'])) if st.meta['step'] else 1
            step=abs(step)*(1 if end>=start else -1)
            if not step:raise PineRuntimeError('Zero loop step')
            if abs(end-start)>100000:raise PineRuntimeError('Loop resource limit')
            self.scopes.append({});self.paths.append(f'loop{st.line}')
            result=NA
            try:
                # v6 reevaluates the end boundary at each iteration.
                i=int(start)
                while (i<=end if step>0 else i>=end):
                    self.scopes[-1][st.meta['name']]=i
                    try:result=self.execute(st.body)
                    except BreakLoop:break
                    except ContinueLoop:pass
                    i+=step;end=self.eval(st.meta['end'])
                    if is_na(end):break
                return result
            finally:self.scopes.pop();self.paths.pop()
        if st.kind=='break':raise BreakLoop()
        if st.kind=='continue':raise ContinueLoop()
        raise PineRuntimeError(f'Unknown statement {st.kind}')
    def call(self,e):
        name=qualified(e.args[0]); nodes=e.args[1:]
        if name in ('request.security','request.security_lower_tf'):
            if not self.request_provider:raise PineRuntimeError('No market context provider')
            return self.request_provider(self,e,name,nodes)
        args=[self.eval(x) for x in nodes if x.kind!='kw']; kwargs={x.value:self.eval(x.args[0]) for x in nodes if x.kind=='kw'}
        if name in self.program.functions:
            fn=self.program.functions[name]; oldpath=self.callpath
            self.callpath=f'{oldpath}/{e.uid}'
            oldscopes,oldpaths=self.scopes,self.paths
            self.scopes=[oldscopes[0],dict(zip(fn.meta['params'],args))];self.paths=['g','f']
            for key,val in kwargs.items():self.scopes[-1][key]=val
            try:
                if name=='f_dashRow':
                    bound=self.scopes[-1];self.capture.append({'row':bound.get('row'),'label':bound.get('labelText'),'value':bound.get('valueText'),'note':bound.get('noteText'),'state':bound.get('state'),'color':bound.get('valueBgOverride')})
                result=self.execute(fn.body)
                for key,val in self.scopes[-1].items():self.remember(self.key(key),copy.deepcopy(val) if isinstance(val,(list,Record)) else val)
                return self.remember(f'{oldpath}/expr/{e.uid}',result)
            finally:self.scopes,self.paths,self.callpath=oldscopes,oldpaths,oldpath
        return self.builtin(name,args,kwargs,e)
    def builtin(self,name,a,kw,e):
        if name=='na':return is_na(a[0])
        if name=='nz':return (a[1] if len(a)>1 else 0) if is_na(a[0]) else a[0]
        if name in ('float','int','bool','color'):
            if name=='bool':return truth(a[0])
            return NA if is_na(a[0]) else (int(a[0]) if name=='int' else float(a[0]) if name=='float' else a[0])
        if name.startswith('input.'):
            return a[0] if a else kw['defval']
        if name=='timeframe.in_seconds':return tf_seconds(a[0] if a else self.timeframe)
        if name=='request.currency_rate':return NA # Explicit stablecoin fallback is in the original source.
        if name=='str.tostring':
            x=a[0]
            if is_na(x):return 'NaN'
            if isinstance(x,bool):return str(x).lower()
            if len(a)>1 and isinstance(x,(int,float)):
                fmt=a[1]; places=len(fmt.split('.')[-1]) if '.' in fmt and not fmt.startswith('format.') else (8 if fmt=='format.mintick' else 0)
                return f'{x:.{places}f}'.rstrip('0').rstrip('.') if places else str(math.floor(x+0.5))
            return str(x)
        if name=='color.new':return f'{a[0]}@{a[1]}'
        if name=='color.rgb':return '#'+''.join(f'{int(v):02x}' for v in a[:3])
        if name.startswith('array.'):
            op=name[6:]
            if op.startswith('new_'):return [a[1] if len(a)>1 else NA for _ in range(int(a[0]) if a else 0)]
            arr=a[0]
            if op=='size':return len(arr)
            if op=='get':return arr[int(a[1])]
            if op=='set':arr[int(a[1])]=a[2];return NA
            if op=='push':arr.append(a[1]);return NA
            if op=='shift':return arr.pop(0)
            if op=='remove':
                removed=arr.pop(int(a[1]))
                if isinstance(removed,Record) and removed.type_name=='ResearchSignal':self.research_completed.append(copy.deepcopy(removed))
                return removed
            raise PineRuntimeError(f'Unsupported {name}')
        if name.endswith('.new') and name[:-4] in self.program.types:
            typ=name[:-4]; values={}
            for i,field in enumerate(self.program.types[typ]):values[field.meta['name']]=a[i] if i<len(a) else kw.get(field.meta['name'],self.eval(field.expr))
            return Record(typ,values)
        if name.startswith('ta.') or name=='math.sum':return self.ta(name,a,e)
        if name.startswith('math.'):
            if any(is_na(x) for x in a):return NA
            op=name[5:]
            funcs={'max':max,'min':min,'abs':abs,'floor':math.floor,'ceil':math.ceil,'log10':math.log10,'pow':pow,'sqrt':math.sqrt,'round':lambda x:math.floor(x+0.5)}
            if op not in funcs:raise PineRuntimeError(f'Unsupported math {name}')
            return funcs[op](a) if op in ('max','min') else funcs[op](*a)
        if name=='alertcondition':
            if truth(a[0]):self.alertconditions.append({'title':a[1] if len(a)>1 else kw.get('title'),'message':a[2] if len(a)>2 else kw.get('message')})
            return NA
        if name in ('indicator','plot','plotshape','bgcolor','alert') or name.startswith(('box.','label.','table.')):
            # Render commands never feed the detector; object handles support the
            # original persistent drawing update functions, and f_dashRow is captured.
            return Record('drawing',{}) if name.endswith('.new') else NA
        raise PineRuntimeError(f'Unsupported Pine builtin {name}')
    def ta(self,name,a,e):
        key=f'{self.callpath}/ta/{e.uid}'
        for i,x in enumerate(a):self.remember(f'{key}/a{i}',x)
        def series(index,length):
            old=self.histories.get(f'{key}/a{index}',())
            return list(old)[-max(0,length-1):]+[a[index]] if length>1 else [a[index]]
        op=name.split('.')[-1]; previous=self.previous(f'{key}/out')
        if op in ('ema','rma','sma','sum','highest','lowest','highestbars','lowestbars'):
            if len(a)==1: # implicit high/low overload
                a=[self.lookup('high' if op.startswith('highest') else 'low'),a[0]]; self.remember(f'{key}/a0',a[0])
            if is_na(a[1]) or a[1]<=0:raise PineRuntimeError(f'Invalid {name} length {a[1]}')
            length=int(a[1]); x=a[0]
            if op=='ema':out=previous if is_na(x) else x if is_na(previous) else 2/(length+1)*x+(1-2/(length+1))*previous
            elif op=='rma':
                valid=[v for v in series(0,self.history_limit) if not is_na(v)]
                out=previous if is_na(x) else ((sum(valid[-length:])/length if len(valid)>=length else NA) if is_na(previous) else x/length+(1-1/length)*previous)
            elif op in ('sma','sum'):
                values=series(0,self.history_limit)
                values=[v for v in values if not is_na(v)][-length:]
                out=(sum(values)/length if op=='sma' else sum(values)) if len(values)>=length and not any(is_na(v) for v in values) else NA
            else:
                vals=series(0,length); valid=[(i,v) for i,v in enumerate(reversed(vals)) if not is_na(v)]
                if not valid:out=NA
                else:
                    best=(max if op.startswith('highest') else min)(v for _,v in valid)
                    out=-next(i for i,v in valid if v==best) if op.endswith('bars') else best
        elif op=='atr':
            length=int(a[0]); h,l=self.lookup('high'),self.lookup('low'); pc=self.previous('/g/close')
            tr=h-l if is_na(pc) else max(h-l,abs(h-pc),abs(l-pc));self.remember(f'{key}/tr',tr)
            old=list(self.histories.get(f'{key}/tr',())); valid=[v for v in old+[tr] if not is_na(v)]
            out=(sum(valid[-length:])/length if len(valid)>=length else NA) if is_na(previous) else (previous*(length-1)+tr)/length
        elif op in ('pivothigh','pivotlow'):
            if len(a)==2:a=[self.lookup('high' if op=='pivothigh' else 'low')]+a;self.remember(f'{key}/a0',a[0])
            left,right=int(a[1]),int(a[2]); vals=series(0,left+right+1);out=NA
            if len(vals)==left+right+1 and not any(is_na(x) for x in vals):
                candidate=vals[left]
                valid=all(candidate>=v for v in vals[:left]) and all(candidate>v for v in vals[left+1:]) if op=='pivothigh' else all(candidate<=v for v in vals[:left]) and all(candidate<v for v in vals[left+1:])
                if valid:out=candidate
        elif op=='barssince':out=0 if truth(a[0]) else NA if is_na(previous) else previous+1
        elif op=='valuewhen':
            conditions=series(0,self.history_limit); values=series(1,self.history_limit)
            hits=[v for c,v in zip(conditions,values) if truth(c)]
            occurrence=int(a[2]);out=hits[-occurrence-1] if len(hits)>occurrence else NA
        elif op in ('crossover','crossunder'):
            pa=self.previous(f'{key}/a0');pb=self.previous(f'{key}/a1');out=binary('>',a[0],a[1]) and binary('<=',pa,pb) if op=='crossover' else binary('<',a[0],a[1]) and binary('>=',pa,pb)
        elif op=='change':out=binary('-',a[0],self.previous(f'{key}/a0',int(a[1]) if len(a)>1 else 1))
        else:raise PineRuntimeError(f'Unsupported TA {name}')
        return self.remember(f'{key}/out',out)
    def commit(self):
        for name,val in self.scopes[0].items():
            if name in self.history_names:self.remember(f'/g/{name}',val)
        for key,val in self.current.items():
            if '/ta/' not in key and key.rsplit('/',1)[-1] not in self.history_names and not ('/expr/' in key and key.rsplit('/',1)[-1] in self.history_exprs):continue
            # Only scalar series participate in [] in this source. Persistent arrays
            # and UDTs live in the checkpoint store, avoiding unbounded object copies.
            if isinstance(val,(list,dict,Record,Namespace)):continue
            if key not in self.histories:self.histories[key]=deque(maxlen=self.history_limit)
            self.histories[key].append(val)
        self.persistent=self.work_persistent;self.count+=1;self.last_start=self.bar['start']
    def export_state(self):
        return encode({'histories':{k:list(v) for k,v in self.histories.items()},'persistent':self.persistent,'varip':self.varip,'count':self.count,'last_start':self.last_start,'open_start':self.open_start,'last_value':self.last_value})
    def restore_state(self,payload):
        state=decode(payload);self.histories={k:deque(v,maxlen=self.history_limit) for k,v in state['histories'].items()};self.persistent=state['persistent'];self.varip=state['varip'];self.count=state['count'];self.last_start=None if is_na(state['last_start']) else state['last_start'];self.open_start=None if is_na(state['open_start']) else state['open_start'];self.last_value=state['last_value']
