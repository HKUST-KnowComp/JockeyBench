import copy
import time
from jockeybench.canonical import digest
from jockeybench.clock import cutoff
from jockeybench.errors import BudgetExceeded, BudgetLimit
from jockeybench.ledger import book, summary
from jockeybench.research.adaptive.domains import fetch, SUPPORTED
from .policy_contract import interpret, PolicyInvalid, PolicyRuntimeError

class EvidenceSDK:
    def __init__(self,run): self.run=run;self.backend_cpu=0
    def call(self,domain,**args):
        run=self.run;s=run.state;race=s['schedule'][0]['races'][0]
        if s['clock']!=cutoff(s['schedule'][0]['day']) or s['scores'] or s['release_ledger']:raise PolicyInvalid('Illegal evidence clock')
        numbers=[r['number'] for r in s['active_initial']['card']['runners']]
        if domain not in SUPPORTED or set(args)!=(set() if domain=='track_context' else {'horse_number'}):raise PolicyInvalid('Forbidden domain/arguments')
        if args and args['horse_number'] not in numbers:raise PolicyInvalid('Foreign entrant')
        action={'type':'query','domain':domain,**args};started=time.process_time()
        try:view,cost,eid,operation=fetch(run,action,race)
        finally:self.backend_cpu+=time.process_time()-started
        event={'turn':len(s['active_trace'])+1,'clock':s['clock'],'action':action,'cost':cost,
               'evidence_id':eid,'service_operation':operation,'observation_digest':digest(view)}
        s['active_trace'].append(event);s['query_count']+=1
        s['evidence_package']=self.package();run.persist()
    def package(self):
        s=self.run.state;card=s['active_initial']['card']
        from jockeybench.research.adaptive.domains import project
        prov={p['service_operation']:p for p in s.get('semantic_provenance',[])}
        return {'schema':'evidence-package-v1','race_id':card['race_id'],
                'entrants':[r['number'] for r in card['runners']],
                'observations':[{'domain':e['action']['domain'],
                    'arguments':{k:v for k,v in e['action'].items() if k=='horse_number'},
                    'observation':project(e['action'],prov[e['service_operation']]['backend_response']),
                    'provenance':{'evidence_id':e['evidence_id'],'service_operation':e['service_operation']},
                    'receipt':copy.deepcopy(e['cost'])}
                    for e in s['active_trace'] if e.get('evidence_id')]}

def execute_policy(run,policy):
    from .policy_contract import source_hash
    if source_hash(policy['source'])!=policy['policy_hash']:raise PolicyInvalid('Policy digest mismatch')
    sdk=EvidenceSDK(run);s=run.state;s['evidence_package']=sdk.package()
    start=time.monotonic();cpu=time.process_time();before=len(s.get('llm_trajectory',[]))
    s['policy_execution']={'success':False,'policy_hash':policy['policy_hash'],'policy_version':policy['policy_version']}
    try:
        value=interpret(policy['source'],s['active_initial']['card'],sdk,run.config.limits.strategy_cpu_seconds)
        if value!=sdk.package():raise PolicyInvalid('Changed EvidencePackage')
        s['policy_execution']['success']=True
    except (BudgetExceeded,BudgetLimit,KeyError,IndexError,TypeError,ValueError) as exc:
        s['policy_execution']['error']=type(exc).__name__
        raise PolicyRuntimeError('Policy execution could not complete') from exc
    finally:
        own_cpu=max(0,time.process_time()-cpu-sdk.backend_cpu)
        amount=run.service.charge('cpu_yen',own_cpu)
        book(s,'policy-interpreter:cpu','compute',amount,basis='trusted_process_cpu_excluding_tool_backend')
        s.setdefault('compute_jobs',[]).append({'id':'policy-interpreter','status':'settled','cpu_seconds':own_cpu,'sdk_misuse':[]})
        s['usage']['cpu_seconds']=s['usage'].get('cpu_seconds',0)+own_cpu
        s['policy_execution'].update(elapsed_seconds=time.monotonic()-start,cpu_seconds=own_cpu,
            llm_calls=len(s.get('llm_trajectory',[]))-before)
        s['evidence_package']=sdk.package();s['evidence_package_digest']=digest(s['evidence_package'])
        run.persist()
    return s['evidence_package']
