"""One stateless submit request; original graceful control flow bound to new audit/context."""
import copy
import json
import time
from types import FunctionType
from jrdb_lh.common import Rejected, cutoff
from jrdb_lh.runner import usage
from jrdb_lh.ledger import summary
from active_research.runner import basic_card, seal
from active_research.adaptive.domains import tools, parse
from active_research.aligned import delivery
from active_research.aligned.model import FINALIZATION_INSTRUCTION, validate_settings
from active_research.aligned.integrity import IntegrityFailure
from .transport import request, body
from .policy_executor import execute_policy
from .policy_contract import PolicyRuntimeError

PROMPT = '''You predict exactly one horse race from its legal basic race card and EvidencePackage.
Use exactly one offered native submit function. Never return prose or a batch of calls.
All evidence is as-of the frozen pre-race cutoff. Never request future results or another race.
Recent history and condition stats use the native latest-five-start history. They are not lifetime statistics.
Null/missing means unknown, not zero. Opaque categorical codes have no assumed codebook.
Submit a complete ranking and finite probabilities for every entrant summing to 1.
The aligned score is 100*ln(n*(0.98*p_winner+0.02/n)) plus
100*(Top1_hit-1/n) for a valid non-uniform prediction, minus 0.5 times raw research cost.
Agent uniform and framework fallback have zero Top1 bonus. Service fees already incurred remain payable.
Research is complete. Only submit is available. Return the native function call only.'''

def observation(run,memory=None):
    s=run.state
    return {'mode':'prediction','basic_race_card':copy.deepcopy(s['active_initial']['card']),
            'EvidencePackage':copy.deepcopy(s['evidence_package']),
            'ledger':summary(s,run.config),'safety_remaining_wall_seconds':None}

class Predictor:
    identity={'kind':'policy-amortized-stateless-predictor','version':1}
    def __init__(self,settings,policy):self.settings=validate_settings(settings);self.policy=copy.deepcopy(policy)
    def call(self,service,obs,phase):
        numbers=[r['number'] for r in obs['basic_race_card']['runners']]
        b=body(self.settings,[{'role':'system','content':PROMPT+(FINALIZATION_INSTRUCTION if phase=='finalization-1' else '')},
            {'role':'user','content':json.dumps(obs,ensure_ascii=False)}],[tools(numbers)[-1]])
        b['tool_choice']={'type':'function','function':{'name':'submit'}}
        return request(service,self.settings,b,phase)
    def finalize(self,service,obs,submit_tool):
        if service.state.get('finalization_repair_requests',0)>=1:raise IntegrityFailure('At most one repair')
        service.state['finalization_repair_requests']=1;service.persist()
        return self.call(service,obs,'finalization-1')

def normal_predict(run,agent,memory):
    s=run.state;batch=s['schedule'][0];race=batch['races'][0]
    s.update(phase='predict',clock=cutoff(batch['day']),usage=usage(),strategy_job=race,
        strategy_job_usage={'calls':0,'evidence_cells':0},active_trace=[],protocol_errors=[],query_count=0,model_turns=0,
        policy_identity={k:agent.policy[k] for k in ('policy_hash','policy_version','parent_policy_hash','created_after_day')},
        policy_source=agent.policy['source'])
    initial=run.service.call('card',{'race_id':race})
    s['active_initial']={'card':basic_card(initial),'receipt':initial['_cost']};run.persist()
    s['policy_provider_settings']=copy.deepcopy(agent.settings)
    run.service.policy_job=(agent.policy['source'],lambda:execute_policy(run,agent.policy))
    try:
        run.service.call('compute',{'code':agent.policy['source']})
    except Rejected as exc:
        # The original service sanitizes ordinary compute exceptions to Rejected.
        # Only our typed interpreter failure is unwrapped; all other rejections
        # remain fail-closed under the unchanged graceful integrity policy.
        if isinstance(exc.__cause__,PolicyRuntimeError):raise exc.__cause__
        raise
    finally:run.service.policy_job=None
    s['model_turns']=1;s['usage']['model_calls']=1;run.persist()
    s['prediction_context']=observation(run);run.persist()
    message=agent.call(run.service,s['prediction_context'],'prediction-1')
    try:
        action=parse(message,[r['number'] for r in s['active_initial']['card']['runners']],race)
        if action['type']!='submit':raise Rejected('Only submit')
    except Rejected as exc:
        s['protocol_errors'].append({'code':'invalid_terminal_action'});run.persist()
        raise RuntimeError('invalid_terminal_action') from exc
    receipt=seal(run,race,action,[r['number'] for r in s['active_initial']['card']['runners']])
    s['active_trace'].append({'turn':1,'clock':s['clock'],'action':action,'cost':receipt['_cost'],'sealed':True})
    s['submission_mode']='voluntary';run.persist();return race,batch['day']

def graceful_function():
    # Bind the ORIGINAL function code to explicit protocol adapters. No mutation
    # of legacy module globals, no copied recovery state machine, no fake trace.
    from .audit import verify_integrity
    scope={**delivery.predict.__globals__,'verify_integrity':verify_integrity,
           'finalization_observation':observation}
    return FunctionType(delivery.predict.__code__,scope,'policy_graceful_predict')

def predict(run,agent):
    start=time.monotonic()
    try:return graceful_function()(run,agent,None,normal_predict)
    finally:
        run.state['aligned_race_elapsed_seconds']=time.monotonic()-start
        run.state['aligned_tool_and_local_seconds']=max(0,run.state['aligned_race_elapsed_seconds']-sum(t.get('logical_request_elapsed_seconds',0) for t in run.state.get('llm_trajectory',[])))
        run.persist()
