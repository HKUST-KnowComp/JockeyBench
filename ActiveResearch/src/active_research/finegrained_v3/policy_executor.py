"""Trusted broker keeps full observations; returns only detached ControlViews."""
import copy
import time
from jrdb_lh.common import digest, cutoff, BudgetExceeded, BudgetLimit, Rejected
from jrdb_lh.ledger import book
from active_research.adaptive.domains import SUPPORTED
from .domains import fetch, project
from .control import information_state
from .policy_contract import interpret, source_hash, PolicyInvalid, PolicyRuntimeError

def validate_action(domain,args,card,records,stops=(),domain_stops=()):
    numbers=[r['number'] for r in card['runners']]
    if domain not in SUPPORTED:raise PolicyInvalid('Four supported domains only')
    keys=set(args)
    if keys != (set() if domain=='track_context' else {'horse_number'}) and not (domain=='recent_history' and keys=={'horse_number','depth'}):raise PolicyInvalid('Action arguments')
    n=args.get('horse_number')
    if n is not None and (type(n) is not str or n not in numbers):raise PolicyInvalid('Current runner only')
    if n in stops or (domain,n) in domain_stops:raise PolicyInvalid('Acquisition already stopped')
    if domain=='track_context' and any(r['domain']==domain for r in records):raise PolicyInvalid('Track context exactly once')
    if domain=='recent_history':
        depth=args.get('depth',5)
        if type(depth) is not int or depth not in (1,3,5):raise PolicyInvalid('Bounded history depth')
        earlier=[r['arguments'].get('depth',5) for r in records if r['domain']==domain and r['arguments']['horse_number']==n]
        if earlier and depth<earlier[-1]:raise PolicyInvalid('History expansion must be monotone')
    return {'type':'query','domain':domain,**args,**({'depth':depth} if domain=='recent_history' else {})}

def records(state):
    by_op={p['service_operation']:p for p in state.get('semantic_provenance',[])}
    result=[]
    for e in state.get('active_trace',[]):
        if not e.get('evidence_id'):continue
        p=by_op[e['service_operation']];a=e['action']
        result.append({'domain':a['domain'],'arguments':{k:v for k,v in a.items() if k=='horse_number'},
            'observation':project(a,p['backend_response']),
            'provenance':{'evidence_id':e['evidence_id'],'service_operation':e['service_operation']},
            'receipt':copy.deepcopy(e['cost'])})
    return result

class EvidenceSDK:
    def __init__(self,run,replay_only=False):
        self.run=run;self.backend_cpu=0;self.cursor=0;self.entries=[];self.events=[];self.seen=[]
        self.stops=set();self.domain_stops=set();self.stop_events=[];self.replay_only=replay_only
        self.original=[e for e in run.state.get('active_trace',[]) if e.get('evidence_id')]
    def state(self,number=None):
        result=information_state(self.run.state['active_initial']['card'],self.entries,self.events,self.stops)
        if number is not None and number not in result['runners']:raise PolicyInvalid('Foreign runner state')
        return result if number is None else result['runners'][number]
    def call(self,domain,**args):
        run=self.run;s=run.state;race=s['schedule'][0]['races'][0];card=s['active_initial']['card']
        if s['clock']!=cutoff(s['schedule'][0]['day']) or s.get('scores') or s.get('release_ledger'):raise PolicyInvalid('Illegal acquisition release state')
        action=validate_action(domain,args,card,self.seen,self.stops,self.domain_stops)
        before=self.state(args.get('horse_number'))
        if self.cursor<len(self.original):
            e=self.original[self.cursor]
            if e['action']!=action or e.get('control_before')!=before:raise PolicyInvalid('Checkpoint acquisition replay mismatch')
            p=next(p for p in s['semantic_provenance'] if p['service_operation']==e['service_operation'])
            view=project(action,p['backend_response']);native=p['backend_response']
        else:
            if self.replay_only:raise PolicyInvalid('Unexpected replay query')
            if len(self.seen)>=run.config.limits.strategy_calls:raise BudgetExceeded('Original strategy calls limit')
            started=time.process_time()
            try:view,cost,eid,operation=fetch(run,action,race,logical_id="policy-"+str(self.cursor+1))
            finally:self.backend_cpu+=time.process_time()-started
            native=s['semantic_provenance'][-1]['backend_response']
            e={'turn':len(s['active_trace'])+1,'clock':s['clock'],'action':action,'cost':cost,
                'evidence_id':eid,'service_operation':operation,'observation_digest':digest(view),
                'control_before':before,'control_before_digest':digest(before),
                **{k:s['semantic_provenance'][-1][k] for k in ('native_acquisition_operations','newly_acquired_cells','local_projection','local_projection_operations')}}
            s['active_trace'].append(e);s['query_count']+=1
        self.entries.append((action,view,native));self.events.append(e);self.seen.append({'domain':domain,'arguments':args});self.cursor+=1
        after=self.state(args.get('horse_number'))
        if self.cursor<=len(self.original):
            if e.get('control_after')!=after or e.get('control_before_digest')!=digest(before) or e.get('control_after_digest')!=digest(after):raise PolicyInvalid('ControlView replay/digest mismatch')
        else:e.update(control_after=after,control_after_digest=digest(after))
        if not self.replay_only:s['evidence_package']=self.package();run.persist()
        return copy.deepcopy(after)
    def stop_horse(self,number):
        if number not in self.state()['runners']:raise PolicyInvalid('Foreign runner stop')
        self.stops.add(number);self.stop_events.append({'kind':'horse','horse_number':number,'after_queries':self.cursor,'state':self.state(number)})
        self.commit_stops()
    def stop_domain(self,domain,number):
        if domain not in SUPPORTED or domain=='track_context' or number not in self.state()['runners']:raise PolicyInvalid('Invalid domain stop')
        self.domain_stops.add((domain,number));self.stop_events.append({'kind':'domain','domain':domain,'horse_number':number,'after_queries':self.cursor,'state':self.state(number)})
        self.commit_stops()
    def commit_stops(self):
        if not self.replay_only:
            self.run.state['acquisition_stops']=copy.deepcopy(self.stop_events);self.run.persist()
    def package(self):
        s=self.run.state;card=s['active_initial']['card']
        return {'schema':'evidence-package-v1','race_id':card['race_id'],'entrants':[r['number'] for r in card['runners']],
            'observations':records(s)}

def execute_policy(run,policy):
    if source_hash(policy['source'])!=policy['policy_hash']:raise PolicyInvalid('Policy digest')
    from .source_binding import ensure,metrics
    ensure(run)
    s=run.state;sdk=EvidenceSDK(run);s['evidence_package']=sdk.package()
    before=len(s.get('llm_trajectory',[]));start=time.monotonic();cpu=time.process_time()
    s['policy_execution']={'success':False,'policy_hash':policy['policy_hash'],'policy_version':policy['policy_version']}
    try:
        result=interpret(policy['source'],s['active_initial']['card'],sdk,run.config.limits.strategy_cpu_seconds)
        if result!=sdk.package() or sum(e['action']['domain']=='track_context' for e in sdk.events)!=1:raise PolicyInvalid('Incomplete acquisition/track_context')
        s['information_state_final']=sdk.state();s['information_state_digest']=digest(s['information_state_final'])
        s['acquisition_complete']=True;s['policy_execution']['success']=True
    except (BudgetExceeded,BudgetLimit,PolicyInvalid,PolicyRuntimeError,KeyError,IndexError,TypeError,ValueError) as e:
        s['policy_execution']['error']=type(e).__name__
        raise PolicyRuntimeError('Policy acquisition failed') from e
    finally:
        timing=getattr(sdk,'interpreter_timing',{})
        own_cpu=timing.get('policy_cpu_seconds',0.)
        book(s,'policy-interpreter:cpu','compute',run.service.charge('cpu_yen',own_cpu),basis='trusted_process_cpu_excluding_tool_backend')
        s.setdefault('compute_jobs',[]).append({'id':'policy-interpreter','status':'settled','cpu_seconds':own_cpu,'sdk_misuse':[]})
        s['usage']['cpu_seconds']=s['usage'].get('cpu_seconds',0)+own_cpu
        s['policy_execution'].update(elapsed_seconds=time.monotonic()-start,cpu_seconds=own_cpu,llm_calls=len(s.get('llm_trajectory',[]))-before,
            timing=timing,source_verification=metrics(run),backend_cpu_seconds=sdk.backend_cpu,
            native_tool_cpu_seconds=getattr(run,'_native_acquisition_cpu_seconds',0.))
        s['evidence_package']=sdk.package();s['evidence_package_digest']=digest(s['evidence_package']);run.persist()
    return s['evidence_package']

def replay_acquisition(s,partial=False):
    from types import SimpleNamespace
    from .marginal_cost import audit_owned_cache
    try:audit_owned_cache(s,complete=s.get('policy_execution',{}).get('success',False))
    except Rejected as exc:raise PolicyInvalid('Marginal replay audit failed') from exc
    original_clock=s['clock'];original_scores=s.get('scores');original_releases=s.get('release_ledger')
    state=copy.deepcopy(s);state.update(clock=cutoff(state['schedule'][0]['day']),scores={},release_ledger={})
    sdk=EvidenceSDK(SimpleNamespace(state=state),replay_only=True)
    interpret(state['policy_source'],state['active_initial']['card'],sdk)
    if sdk.cursor!=len(sdk.original) or sdk.stop_events!=state.get('acquisition_stops',[]):raise PolicyInvalid('Acquisition/stop replay mismatch')
    if sdk.state()!=state.get('information_state_final') or digest(sdk.state())!=state.get('information_state_digest'):raise PolicyInvalid('Final ControlView changed')
    return {'zero_llm':True,'calls':sdk.cursor,'control_view':'PASS','stopping':'PASS'}
