"""Parser for the Pine v6 subset used by the pinned Scalping_SMA source.

No eval/exec of user text. Every node retains its source line. Unsupported syntax
is a hard error rather than a substituted indicator or skipped calculation.
"""
from __future__ import annotations
import ast
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[2] / 'reference/Scalping_SMA_1.15.2_price_scale_levels.pine'

@dataclass
class Expr:
    kind: str
    value: object = None
    args: list = field(default_factory=list)
    uid: str = ''

@dataclass
class Statement:
    kind: str
    text: str
    line: int
    expr: Expr | None = None
    body: list = field(default_factory=list)
    otherwise: list = field(default_factory=list)
    meta: dict = field(default_factory=dict)

TOKEN = re.compile(r'''\s*(?:("(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*')|(\d+(?:\.\d*)?(?:[eE][+-]?\d+)?|\.\d+)|([A-Za-z_]\w*)|(:=|=>|==|!=|>=|<=|\+=|-=|\*=|/=|[+*/%<>=?:.,()\[\]-])|(\#[0-9a-fA-F]{6,8}))''')

class ExpressionParser:
    def __init__(self, text, line):
        text = re.sub(r'(array\.new)<([\w]+)>', r'\1_\2', text)
        self.tokens = []
        pos = 0
        while pos < len(text):
            m = TOKEN.match(text, pos)
            if not m:
                if not text[pos:].strip(): break
                raise SyntaxError(f'Pine L{line}: unexpected {text[pos:pos+80]!r}')
            raw = next(v for v in m.groups() if v is not None)
            kind = 'lit' if m.group(1) or m.group(2) or m.group(5) else 'tok'
            value = ast.literal_eval(raw) if m.group(1) or m.group(2) else raw
            self.tokens.append((raw, kind, value))
            pos = m.end()
        self.tokens.append(('<eof>', 'tok', None))
        self.i = 0
        self.line = line
        self.serial = 0
    def node(self, kind, value=None, args=None):
        self.serial += 1
        return Expr(kind, value, args or [], f'{self.line}:{self.serial}')
    def peek(self, ahead=0): return self.tokens[self.i+ahead][0]
    def take(self, expected=None):
        token = self.tokens[self.i]
        if expected is not None and token[0] != expected:
            raise SyntaxError(f'Pine L{self.line}: expected {expected}, got {token[0]}')
        self.i += 1
        return token
    def parse(self, minimum=0):
        raw, kind, value = self.take()
        if kind == 'lit': left = self.node('literal', value)
        elif raw in ('not','-','+'):
            left = self.node('unary',raw,[self.parse(70)])
        elif raw == '(':
            left = self.parse(); self.take(')')
        elif raw == '[':
            items = []
            while self.peek() != ']':
                items.append(self.parse())
                if self.peek() != ',': break
                self.take(',')
            self.take(']'); left = self.node('list',args=items)
        else:
            if raw == '<eof>': raise SyntaxError(f'Pine L{self.line}: empty expression')
            left = self.node('name',raw)
        precedence = {'or':10,'and':20,'==':30,'!=':30,'>':40,'<':40,'>=':40,'<=':40,'+':50,'-':50,'*':60,'/':60,'%':60}
        while True:
            op = self.peek()
            if op == '.' and 90 >= minimum:
                self.take(); left = self.node('attr',self.take()[0],[left]); continue
            if op == '(' and 90 >= minimum:
                self.take(); args=[]
                while self.peek() != ')':
                    if self.peek(1) == '=':
                        key=self.take()[0]; self.take('='); args.append(self.node('kw',key,[self.parse()]))
                    else: args.append(self.parse())
                    if self.peek() != ',': break
                    self.take(',')
                self.take(')'); left=self.node('call',args=[left]+args); continue
            if op == '[' and 90 >= minimum:
                self.take(); ix=self.parse(); self.take(']'); left=self.node('history',args=[left,ix]); continue
            if op == '?' and minimum <= 5:
                self.take(); yes=self.parse(); self.take(':'); no=self.parse(5)
                left=self.node('ternary',args=[left,yes,no]); continue
            bp=precedence.get(op,-1)
            if bp < minimum: break
            self.take(); left=self.node('binary',op,[left,self.parse(bp+1)])
        return left
    def full(self):
        result=self.parse()
        if self.peek() != '<eof>': raise SyntaxError(f'Pine L{self.line}: trailing {self.tokens[self.i:]}')
        return result

def expression(text,line): return ExpressionParser(text,line).full()

def clean_line(line):
    # Strip comments outside strings only.
    quote=None; escape=False
    for i,c in enumerate(line):
        if escape: escape=False; continue
        if c=='\\' and quote: escape=True; continue
        if quote:
            if c==quote: quote=None
        elif c in ('"',"'"): quote=c
        elif line[i:i+2]=='//': return line[:i].rstrip()
    return line.rstrip()

def logical_lines(source):
    records=[]; pending=''; depth=0; first=0; indent=0
    for number, raw in enumerate(source.splitlines(),1):
        cleaned=clean_line(raw)
        if not cleaned.strip(): continue
        current_indent=len(cleaned)-len(cleaned.lstrip())
        if not pending and current_indent % 4 and records:
            indent, pending, first = records.pop()
        if pending:
            pending+=' '+cleaned.strip()
        else:
            pending=cleaned.strip(); first=number; indent=current_indent
        # Tokenize delimiter balance without string literals.
        without=re.sub(r'''"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*' ''', '', cleaned, flags=re.X)
        depth += sum(without.count(c) for c in '([')-sum(without.count(c) for c in ')]')
        continues=bool(re.search(r'(?:[=+*/?:,\-]|\b(?:and|or))\s*$',pending)) and not pending.endswith('=>')
        if depth > 0 or continues: continue
        records.append((indent,pending,first)); pending=''
    if pending: raise SyntaxError(f'Unfinished Pine statement at {first}')
    return records

DECL=re.compile(r'^(?:(varip|var|const)\s+)?(?:(array<\w+>|int|float|bool|string|color|box|label|table|ResearchSignal|ResearchStats)\s+)?(\[[\w\s,]+\]|[A-Za-z_]\w*(?:\.\w+)?)\s*(:=|\+=|-=|\*=|/=|=(?!=))\s*(.+)$')

class Program:
    def __init__(self,source):
        self.source=source; self.records=logical_lines(source); self.functions={}; self.types={}
        self.statements,_=self.block(0,0)
    def block(self,index,indent):
        result=[]
        while index<len(self.records):
            level,text,line=self.records[index]
            if level<indent or (level==indent and text.startswith('else')): break
            if level!=indent: raise SyntaxError(f'Pine L{line}: unexpected indent {level}, expected {indent}: {text[:80]}')
            index+=1
            def children():
                nonlocal index
                if index>=len(self.records) or self.records[index][0]<=level: return []
                nodes,index=self.block(index,self.records[index][0]); return nodes
            if text.endswith('=>'):
                m=re.fullmatch(r'(\w+)\((.*)\)\s*=>',text)
                if not m: raise SyntaxError(f'Pine function L{line}: {text}')
                params=[p.strip().split()[-1] for p in m[2].split(',') if p.strip()]
                node=Statement('function',text,line,body=children(),meta={'name':m[1],'params':params})
                self.functions[m[1]]=node; continue
            if text.startswith('type '):
                fields=children(); self.types[text.split()[1]]=fields; continue
            if text.startswith('if '):
                node=Statement('if',text,line,expression(text[3:],line),children())
                target=node
                while index<len(self.records) and self.records[index][0]==level and self.records[index][1].startswith('else'):
                    _,et,el=self.records[index]; index+=1
                    if et.startswith('else if '):
                        other=Statement('if',et,el,expression(et[8:],el),children()); target.otherwise=[other]; target=other
                    else: target.otherwise=children(); break
                result.append(node); continue
            if text.startswith('for '):
                m=re.fullmatch(r'for (\w+) = (.+?) to (.+?)(?: by (.+))?',text)
                if not m: raise SyntaxError(f'Pine for L{line}: {text}')
                result.append(Statement('for',text,line,body=children(),meta={'name':m[1],'start':expression(m[2],line),'end':expression(m[3],line+0.01),'step':expression(m[4],line+0.02) if m[4] else None})); continue
            if text in ('break','continue'):
                result.append(Statement(text,text,line)); continue
            match=DECL.match(text)
            if match:
                mode,typ,name,op,rhs=match.groups()
                result.append(Statement('assign',text,line,expression(rhs,line),meta={'mode':mode,'type':typ,'name':name,'op':op})); continue
            # Uninitialized UDT fields.
            if re.fullmatch(r'(int|float|bool|string) \w+',text):
                typ,name=text.split(); result.append(Statement('assign',text,line,expression('na',line),meta={'type':typ,'name':name,'op':'=','mode':None})); continue
            result.append(Statement('expression',text,line,expression(text,line)))
        return result,index

@lru_cache(maxsize=1)
def load_program(): return Program(SOURCE.read_text(encoding='utf-8'))
