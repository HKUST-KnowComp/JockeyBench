"""Validate aligned constraints, then invoke the original experience/replay audits."""
import copy
import json
from jockeybench.errors import Rejected
from jockeybench.canonical import digest
from jockeybench.research.experience.model import verify_semantics as original_audit, PROMPT as ORIGINAL_PROMPT
from .model import PROMPT, guard_body, FINALIZATION_INSTRUCTION

def verify_semantics(state):
    legacy = copy.deepcopy(state)
    finalizations=[]
    for row in legacy.get('llm_trajectory',[]):
        body = row['request']
        guard_body(body)
        finalization=row['phase']=='finalization-1'
        expected_prompt=PROMPT+FINALIZATION_INSTRUCTION if finalization else PROMPT
        if row['request_id']!=digest([state['run_id'],row['phase'],body]):
            raise Rejected('Request identity/body digest mismatch')
        if body['messages'][0]['content'] != expected_prompt or body['max_tokens'] is not None or body['parallel_tool_calls'] is not False:
            raise Rejected('Aligned request constraints changed')
        obs = json.loads(body['messages'][1]['content'])
        if obs.get('safety_remaining_wall_seconds','absent') is not None:
            raise Rejected('Aligned observation must declare no race deadline')
        if finalization:
            finalizations.append(row)
            from jockeybench.research.experience.model import FIELD
            from jockeybench.research.adaptive.domains import tools
            context=copy.deepcopy(state['finalization_context'])
            context[FIELD]=context.pop('research_policy')
            numbers=[r['number'] for r in context['basic_race_card']['runners']]
            if obs!=context or body['tools']!=[tools(numbers)[-1]] or body['tool_choice']!={'type':'function','function':{'name':'submit'}}:
                raise Rejected('Finalization evidence/schema changed')
            if not state.get('research_closed') or len(finalizations)>1:
                raise Rejected('Unbounded or premature finalization')
        body['messages'][0]['content'] = ORIGINAL_PROMPT
        obs['safety_remaining_wall_seconds'] = 0
        body['messages'][1]['content'] = json.dumps(obs)
    legacy['llm_trajectory']=[r for r in legacy.get('llm_trajectory',[]) if r['phase']!='finalization-1']
    result = original_audit(legacy)
    verify_delivery(state)
    if state.get('aligned_scores'):
        from .scoring import checkpoint_components
        if state['aligned_scores']!=checkpoint_components(state):
            raise Rejected('Aligned score integrity mismatch')
    result['aligned_execution_constraints'] = 'PASS'
    return result

def verify_delivery(state):
    from jockeybench.research.adaptive.domains import parse
    from .scoring import is_uniform, ELIGIBLE
    from .delivery import uniform_prediction
    from jockeybench.clock import cutoff
    for race,metadata in state.get('aligned_delivery',{}).items():
        if metadata['delivery']=='no_delivery':
            if race in state['submissions']:raise Rejected('No-delivery race has a sealed prediction')
            continue
        sealed=state['submissions'][race];prediction=sealed['prediction']
        phase=metadata['submit_phase']
        numbers=[r['number'] for r in state['active_initial']['card']['runners']]
        if phase=='research':
            event=state['active_trace'][-1]
            request=next(r for r in state['llm_trajectory'] if r['phase']=='prediction-'+str(event['turn']))
            expected='uniform_submit' if is_uniform(prediction) else 'direct_predictor'
        elif phase=='finalization':
            event=state['delivery_events'][-1]
            request=next(r for r in state['llm_trajectory'] if r['phase']=='finalization-1')
            if event.get('request_id')!=request['request_id']:raise Rejected('Repair event identity mismatch')
            expected='uniform_submit' if is_uniform(prediction) else 'repaired_predictor'
        elif phase=='framework':
            event=state['delivery_events'][-1]
            request=None;expected='execution_fallback'
            if prediction!=uniform_prediction(numbers) or state.get('finalization_repair_success'):
                raise Rejected('Fallback provenance is inconsistent')
        else:raise Rejected('Unknown delivery submit phase')
        if request and parse(request['response']['message'],numbers,race)!=event['action']:
            raise Rejected('Native submission/action mismatch')
        if event.get('action')!={'type':'submit','prediction':prediction} or not event.get('sealed') or event['clock']!=cutoff(state['schedule'][0]['day']):
            raise Rejected('Delivery action/seal/clock mismatch')
        submitted=[r for r in state['audit'] if r['tool']=='submit' and r.get('status')=='ok' and r['arguments']['race_id']==race]
        if len(submitted)!=1 or event['cost']!=submitted[0]['_cost']:
            raise Rejected('Delivery receipt mismatch')
        if metadata['delivery']!=expected or sealed['delivery']!=expected or metadata['bonus_eligible']!=(expected in ELIGIBLE):
            raise Rejected('Delivery taxonomy or bonus provenance mismatch')
        if metadata['generated_by']!=('framework' if phase=='framework' else 'agent') or sealed['format_fallback']!=(phase=='framework'):
            raise Rejected('Framework/agent provenance mismatch')

def classify_failure(exc, state):
    from .integrity import IntegrityFailure
    from jockeybench.errors import Pending, ProviderCostLimit
    from jockeybench.research.adaptive.runner import SafetyLimit
    from .model import ContextLimit
    if isinstance(exc,IntegrityFailure):return 'hard_integrity_failure'
    if isinstance(exc,ContextLimit): return 'context_limit'
    if isinstance(exc,ProviderCostLimit): return 'cost_safety_stop'
    if isinstance(exc,Pending): return 'provider_failure'
    if isinstance(exc,SafetyLimit) and str(exc)=='max_model_turns':
        return 'invalid_action' if state.get('protocol_errors') else 'turn_limit'
    if isinstance(exc,SafetyLimit): return 'service_safety_stop'
    if isinstance(exc,Rejected) and state.get('provider_attempts',[]) and state['provider_attempts'][-1].get('status')=='http_error':
        return 'provider_failure'
    if isinstance(exc,Rejected):return 'hard_integrity_failure'
    return 'runtime_failure'
