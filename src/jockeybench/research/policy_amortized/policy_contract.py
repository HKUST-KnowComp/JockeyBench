"""Interpreted Python subset: no exec/eval, imports, Python object access or I/O.

Policies select acquisitions from the public card. SDK observations are captured
by the broker, never returned as mutable objects to policy code. The sole output
is an opaque package token. Thus arbitrary policy-authored output is impossible.
"""
import ast
import copy
import hashlib
import time
from jockeybench.errors import Rejected
from jockeybench.canonical import packed, digest
from jockeybench.research.adaptive.domains import SUPPORTED

CONTRACT = '''Define exactly def collect_evidence(sdk, context): ...
context is the basic race card with race_id, day_index, feature_asof, facts,
runners (number, facts, announced_cancelled). No history Frame or outcomes.
Supported Python: local assignment, for loops over card/list/range, if/else,
break/continue, constants, lists/tuples/dicts, indexing, comparisons, and/or/not,
len, range, sorted (no key), dictionary.get(key, default).
No imports, globals, helpers, comprehensions, arithmetic, attributes, mutation,
while, recursion, exception handlers, file/network/process/LLM access.
sdk.call("recent_history", horse_number=runner["number"])
sdk.call("training", horse_number=runner["number"])
sdk.call("condition_stats", horse_number=runner["number"])
sdk.call("track_context")
Calls return None; the trusted SDK retains every complete observation and receipt.
recent_history exposes five prior starts; condition_stats uses the same five;
training bills the original whole-race response, projects only selected horse;
track_context is race-level. Every backend call is charged under original rules.
No query budget is added. Original service, CPU and memory limits still apply.
Finish with return sdk.package(). No other return value is accepted.
Do not construct predictions, ranking, probabilities, scores or winner artifacts.
Only the predictor makes predictions. Never query outcomes or another data domain.
'''

class PolicyInvalid(Rejected): pass
class PolicyRuntimeError(RuntimeError): pass
TOKEN = object()
SDK_MARKER = object()

def source_hash(source): return hashlib.sha256(source.encode('utf-8')).hexdigest()

def parse_source(source):
    if not isinstance(source,str) or len(source.encode())>1024**2: raise PolicyInvalid('Invalid source size')
    try: tree=ast.parse(source)
    except (SyntaxError,RecursionError) as exc: raise PolicyInvalid('Invalid Python syntax') from exc
    if len(tree.body)!=1 or not isinstance(tree.body[0],ast.FunctionDef): raise PolicyInvalid('Exactly one function')
    f=tree.body[0];a=f.args
    if (f.name!='collect_evidence' or [x.arg for x in a.args]!=['sdk','context'] or a.posonlyargs
        or a.kwonlyargs or a.vararg or a.kwarg or a.defaults or f.decorator_list or f.returns
        or any(x.annotation for x in a.args)):
        raise PolicyInvalid('Required signature collect_evidence(sdk, context)')
    allowed=(ast.FunctionDef,ast.arguments,ast.arg,ast.Assign,ast.For,ast.If,ast.Return,
        ast.Expr,ast.Break,ast.Continue,ast.Pass,ast.Name,ast.Load,ast.Store,ast.Constant,
        ast.List,ast.Tuple,ast.Dict,ast.Subscript,ast.Call,ast.Attribute,ast.keyword,
        ast.Compare,ast.Eq,ast.NotEq,ast.Lt,ast.LtE,ast.Gt,ast.GtE,ast.In,ast.NotIn,
        ast.Is,ast.IsNot,ast.BoolOp,ast.And,ast.Or,ast.UnaryOp,ast.Not)
    for n in ast.walk(f):
        if not isinstance(n,allowed): raise PolicyInvalid('Unsupported syntax: '+type(n).__name__)
        if isinstance(n,ast.FunctionDef) and n is not f:raise PolicyInvalid('No helpers or recursion')
        if isinstance(n,ast.Name) and (n.id.startswith('_') or n.id in {'predict','winner','ranking','probabilities','results','submit'}):raise PolicyInvalid('Forbidden identifier')
        if isinstance(n,ast.Assign) and (len(n.targets)!=1 or not isinstance(n.targets[0],ast.Name) or n.targets[0].id in {'sdk','context'}):raise PolicyInvalid('Only local binding')
        if isinstance(n,ast.For) and (not isinstance(n.target,ast.Name) or n.target.id in {'sdk','context'}):raise PolicyInvalid('Only local loop target')
        if isinstance(n,ast.Attribute) and n.attr not in {'call','package','get'}:raise PolicyInvalid('Object access forbidden')
        if isinstance(n,ast.Call):
            if isinstance(n.func,ast.Name):
                if n.func.id not in {'len','range','sorted'} or n.keywords:raise PolicyInvalid('Unknown callable')
            elif isinstance(n.func,ast.Attribute):
                if n.func.attr in {'call','package'}:
                    if not isinstance(n.func.value,ast.Name) or n.func.value.id!='sdk':raise PolicyInvalid('SDK only')
                    if n.func.attr=='call':
                        if len(n.args)!=1 or not isinstance(n.args[0],ast.Constant) or n.args[0].value not in SUPPORTED:raise PolicyInvalid('Only four literal approved domains')
                        names=[k.arg for k in n.keywords]
                        if names != ([] if n.args[0].value=='track_context' else ['horse_number']):raise PolicyInvalid('Tool arguments')
                    elif n.args or n.keywords:raise PolicyInvalid('package has no arguments')
                elif n.keywords or not 1<=len(n.args)<=2:raise PolicyInvalid('dict.get signature')
            else:raise PolicyInvalid('No dynamic callables')
        if isinstance(n,ast.Return) and not (isinstance(n.value,ast.Call) and isinstance(n.value.func,ast.Attribute) and isinstance(n.value.func.value,ast.Name) and n.value.func.value.id=='sdk' and n.value.func.attr=='package'):
            raise PolicyInvalid('Return only the unmodified SDK package')
    return f

class Flow(Exception):
    def __init__(self,kind,value=None):self.kind,self.value=kind,value

def interpret(source,card,sdk,cpu_seconds=30):
    f=parse_source(source);env={'context':copy.deepcopy(card),'sdk':SDK_MARKER}
    started=time.process_time()
    def tick():
        if cpu_seconds is not None and time.process_time()-started>cpu_seconds:raise PolicyRuntimeError('Original strategy CPU limit')
    def bounded(value):
        # Original worker output ceiling, applied to policy-authored temporaries.
        # Evidence observations never enter this heap and are never truncated.
        pending=[(value,0)];size=0
        while pending:
            tick();v,depth=pending.pop()
            if depth>64:raise PolicyRuntimeError('Policy temporary nesting limit')
            if v is TOKEN or v is SDK_MARKER or isinstance(v,range):continue
            size+=32
            if isinstance(v,str):size+=len(v.encode())
            elif isinstance(v,(list,tuple)):pending.extend((x,depth+1) for x in v)
            elif isinstance(v,dict):pending.extend((x,depth+1) for kv in v.items() for x in kv)
            if size>1024**2:raise PolicyRuntimeError('Original worker output-sized temporary ceiling')
        return value
    def expr(n):
        tick()
        if isinstance(n,ast.Constant):return n.value
        if isinstance(n,ast.Name):return env[n.id]
        if isinstance(n,(ast.List,ast.Tuple)):return [expr(v) for v in n.elts]
        if isinstance(n,ast.Dict):return {expr(k):expr(v) for k,v in zip(n.keys,n.values)}
        if isinstance(n,ast.Subscript):return expr(n.value)[expr(n.slice)]
        if isinstance(n,ast.UnaryOp):return not expr(n.operand)
        if isinstance(n,ast.BoolOp):
            for v in n.values:
                x=expr(v)
                if isinstance(n.op,ast.And) and not x:return x
                if isinstance(n.op,ast.Or) and x:return x
            return x
        if isinstance(n,ast.Compare):
            a=expr(n.left)
            for op,bn in zip(n.ops,n.comparators):
                b=expr(bn)
                funcs={ast.Eq:lambda:a==b,ast.NotEq:lambda:a!=b,ast.Lt:lambda:a<b,ast.LtE:lambda:a<=b,ast.Gt:lambda:a>b,ast.GtE:lambda:a>=b,ast.In:lambda:a in b,ast.NotIn:lambda:a not in b,ast.Is:lambda:a is b,ast.IsNot:lambda:a is not b}
                if not funcs[type(op)]():return False
                a=b
            return True
        if isinstance(n,ast.Call):
            args=[expr(x) for x in n.args];kw={x.arg:expr(x.value) for x in n.keywords}
            if isinstance(n.func,ast.Name):
                if n.func.id=='range':return range(*args)
                if n.func.id=='len':return len(*args)
                # Never materialize an attacker-sized range; sorting card/list only.
                if len(args)!=1 or not isinstance(args[0],(list,tuple,dict)):raise PolicyInvalid('sorted requires existing finite container')
                return sorted(args[0])
            if n.func.attr=='get':
                value=expr(n.func.value)
                if not isinstance(value,dict):raise PolicyInvalid('get requires dict')
                return value.get(*args)
            if expr(n.func.value) is not SDK_MARKER:raise PolicyInvalid('SDK binding changed')
            if n.func.attr=='package':return TOKEN
            sdk.call(*args,**kw);return None
        raise PolicyInvalid('Unsupported expression')
    def block(nodes):
        for n in nodes:
            tick()
            if isinstance(n,ast.Assign):env[n.targets[0].id]=bounded(expr(n.value))
            elif isinstance(n,ast.Expr):expr(n.value)
            elif isinstance(n,ast.If):block(n.body if expr(n.test) else n.orelse)
            elif isinstance(n,ast.For):
                seq=expr(n.iter)
                if not isinstance(seq,(list,tuple,dict,range)):raise PolicyInvalid('Loop container')
                broken=False
                for value in seq:
                    tick();env[n.target.id]=value
                    try:block(n.body)
                    except Flow as e:
                        if e.kind=='break':broken=True;break
                        if e.kind!='continue':raise
                if not broken:block(n.orelse)
            elif isinstance(n,ast.Return):raise Flow('return',expr(n.value))
            elif isinstance(n,ast.Break):raise Flow('break')
            elif isinstance(n,ast.Continue):raise Flow('continue')
            elif not isinstance(n,ast.Pass):raise PolicyInvalid('Unsupported statement')
    try:block(f.body)
    except Flow as e:
        if e.kind=='return' and e.value is TOKEN:return sdk.package()
    raise PolicyInvalid('Missing package return')

def synthetic_cards():
    return [{'race_id':'synthetic-'+str(n),'day_index':0,'feature_asof':'2024-01-06T00:00:00+09:00',
             'facts':{'distance':1200 if n%2 else 1800,'surface':'turf' if n%2 else None,'direction':None,'venue':None},
             'runners':[{'number':str(i+1).zfill(2),'facts':{'draw':i+1,'sex':None,'carried_weight_tenths_kg':None},'announced_cancelled':False} for i in range(n)]} for n in (1,5,18)]

class SyntheticSDK:
    def __init__(self,card):self.card=card;self.records=[]
    def call(self,domain,**args):
        if domain not in SUPPORTED or set(args)!=(set() if domain=='track_context' else {'horse_number'}):raise PolicyInvalid('Illegal tool')
        if args and args['horse_number'] not in [r['number'] for r in self.card['runners']]:raise PolicyInvalid('Foreign entrant')
        # Mirrors the existing strategy_calls engineering bound, not a new query budget.
        if len(self.records)>=64:raise PolicyInvalid('Original strategy service call limit')
        self.records.append({'domain':domain,'arguments':args,'observation':{'synthetic':True},'provenance':digest([domain,args]),'receipt':{'charged_cells':1,'cost_micro':255000}})
    def package(self):return {'schema':'evidence-package-v1','race_id':self.card['race_id'],'entrants':[r['number'] for r in self.card['runners']],'observations':copy.deepcopy(self.records)}

def validate_source(source):
    parse_source(source);receipts=[]
    for card in synthetic_cards():
        results=[interpret(source,card,SyntheticSDK(card)) for _ in range(2)]
        if packed(results[0])!=packed(results[1]):raise PolicyInvalid('Nondeterministic execution')
        receipts.append({'field_size':len(card['runners']),'tool_calls':len(results[0]['observations']),'package_digest':digest(results[0])})
    return {'valid':True,'policy_hash':source_hash(source),'validation':'synthetic-only-v1','deterministic':True,'zero_llm':True,'cases':receipts}
