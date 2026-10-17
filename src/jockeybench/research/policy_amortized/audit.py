import copy
import json
from jockeybench.errors import Rejected
from jockeybench.clock import cutoff
from jockeybench.canonical import digest
from jockeybench.audit.replay import replay as replay_economic
from jockeybench.ledger import reconcile
from jockeybench.research.adaptive.domains import backend,project,tools
from jockeybench.research.aligned.audit import verify_delivery
from jockeybench.research.aligned.integrity import IntegrityFailure
from jockeybench.research.aligned.scoring import checkpoint_components
from jockeybench.research.aligned.model import guard_body, FINALIZATION_INSTRUCTION

def verify_semantics(s):
    from .predictor import PROMPT
    from .transport import body
    ops={r['operation']:r for r in s.get('audit',[])}
    events=[e for e in s.get('active_trace',[]) if e.get('evidence_id')]
    prov=s.get('semantic_provenance',[])
    if len(events)!=len(prov):raise Rejected('Evidence provenance count mismatch')
    for e,p in zip(events,prov):
        name,args=backend(e['action'],s['schedule'][0]['races'][0]);r=ops[p['service_operation']]
        v=project(e['action'],p['backend_response'])
        if (r['tool']!=name or r['arguments']!=args or p['backend_tool']!=name or p['backend_arguments']!=args
            or p['race_id']!=s['schedule'][0]['races'][0] or p['clock']!=cutoff(s['schedule'][0]['day'])
            or p['source_manifest']!=s['binding']['source'] or r['response_digest']!=digest(p['backend_response'])
            or p['response_digest']!=r['response_digest'] or p['semantic_view_digest']!=digest(v)
            or e['observation_digest']!=digest(v) or e['cost']!=r['_cost']
            or e['evidence_id']!=digest([p['service_operation'],e['action'],v]) or e['evidence_id']!=p['evidence_id']):
            raise Rejected('Evidence binding changed')
    if 'evidence_package' in s:
        from .policy_executor import EvidenceSDK
        from types import SimpleNamespace
        expected=EvidenceSDK(SimpleNamespace(state=s)).package()
        if expected!=s['evidence_package'] or digest(expected)!=s.get('evidence_package_digest'):raise Rejected('EvidencePackage modified/pruned')
    phases=[t['phase'] for t in s.get('llm_trajectory',[])]
    if phases not in ([],['prediction-1'],['finalization-1'],['prediction-1','finalization-1']):raise Rejected('More than one normal/repair request')
    for t in s.get('llm_trajectory',[]):
        b=t['request'];guard_body(b)
        obs=json.loads(b['messages'][1]['content'])
        context=s['finalization_context'] if t['phase']=='finalization-1' else s['prediction_context']
        if set(obs)!={'mode','basic_race_card','EvidencePackage','ledger','safety_remaining_wall_seconds'} or obs!=context:
            raise Rejected('Predictor isolation/context mismatch')
        if obs['basic_race_card']!=s['active_initial']['card'] or obs['EvidencePackage']!=s['evidence_package']:
            raise Rejected('Predictor evidence mismatch')
        numbers=[r['number'] for r in obs['basic_race_card']['runners']]
        expected=body(s['policy_provider_settings'],[{'role':'system','content':PROMPT+(FINALIZATION_INSTRUCTION if t['phase']=='finalization-1' else '')},
            {'role':'user','content':json.dumps(obs,ensure_ascii=False)}],[tools(numbers)[-1]])
        expected['tool_choice']={'type':'function','function':{'name':'submit'}}
        if b!=expected or t['request_id']!=digest([s['run_id'],t['phase'],b]):raise Rejected('Request digest/profile mismatch')
    if s.get('policy_execution',{}).get('llm_calls',0)!=0:raise Rejected('LLM inside policy')
    if s.get('policy_execution',{}).get('success'):
        from .policy_contract import interpret,source_hash
        if source_hash(s['policy_source'])!=s['policy_identity']['policy_hash']:raise Rejected('Policy source digest')
        actions=[]
        class ReplaySDK:
            def call(self,domain,**args):actions.append({'type':'query','domain':domain,**args})
            def package(self):return s['evidence_package']
        interpret(s['policy_source'],s['active_initial']['card'],ReplaySDK())
        if actions!=[e['action'] for e in events]:raise Rejected('Policy acquisition replay changed')
    verify_delivery(s)
    if s.get('aligned_scores') and s['aligned_scores']!=checkpoint_components(s):raise Rejected('Aligned score mismatch')
    return {'provenance':'PASS','stateless_predictor':'PASS','one_normal_request':'PASS','delivery':'PASS','scorer':'PASS'}

def verify_integrity(run,memory=None,require_card=True):
    s=run.state
    try:
        if len(s['schedule'])!=1 or len(s['schedule'][0]['races'])!=1:raise Rejected('Race schedule')
        batch=s['schedule'][0];race=batch['races'][0]
        if s['clock']!=cutoff(batch['day']) or s['scores'] or s['release_ledger']:raise Rejected('Temporal/release state')
        if s.get('strategy_job') not in (None,race):raise Rejected('Race identity')
        if hasattr(run,'storage'):
            saved=run.storage.load()
            if saved and (saved['run_id']!=s['run_id'] or saved['binding']!=s['binding']):raise Rejected('Checkpoint binding')
        elif s.get('checkpoint_digest') and s['checkpoint_digest']!=digest({k:v for k,v in s.items() if k!='checkpoint_digest'}):raise Rejected('Checkpoint digest')
        card=s.get('active_initial',{}).get('card')
        if require_card or card:
            if not card or card['race_id']!=race:raise Rejected('Card identity')
            numbers=[r['number'] for r in card['runners']]
            if not numbers or len(numbers)!=len(set(numbers)):raise Rejected('Entrant set')
            from jockeybench.research.runner import basic_card
            from jockeybench.protocol.service import Service as EconomicService
            rows=[r for r in s['audit'] if r['tool']=='card' and r.get('status')=='ok' and r['arguments'].get('race_id')==race]
            if not rows:raise Rejected('Missing charged card')
            raw=EconomicService(run.source,run.config,s).dispatch('card',rows[0]['arguments'],[])
            if basic_card(raw)!=card or s['active_initial']['receipt']!=rows[0]['_cost']:raise Rejected('Card projection/receipt')
        reconcile(s);replay_economic(run.config,s,source=run.source);verify_semantics(s)
        return True
    except IntegrityFailure:raise
    except Exception as exc:raise IntegrityFailure('Policy integrity: '+type(exc).__name__+' '+str(exc)) from exc
