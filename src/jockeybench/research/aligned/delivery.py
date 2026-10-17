"""One bounded finalization request, then honest framework uniform delivery."""
import copy
from jockeybench.errors import Rejected, Pending, ProviderCostLimit
from jockeybench.clock import cutoff
from jockeybench.canonical import digest
from jockeybench.ledger import summary
from jockeybench.scoring import validate
from jockeybench.research.runner import seal
from jockeybench.research.adaptive.domains import tools, parse, project, trajectory, SUPPORTED, MISSING
from jockeybench.research.adaptive.runner import SafetyLimit
from .model import ContextLimit
from .integrity import verify_integrity, IntegrityFailure
from .audit import classify_failure
from .scoring import is_uniform, ELIGIBLE

MAX_FINALIZATION_REPAIR_REQUESTS=1

def uniform_prediction(numbers):
    order=sorted(numbers)
    if not order or len(order)!=len(set(order)):
        raise IntegrityFailure('Impossible entrant set')
    return validate({'ranking':order,'probabilities':{n:1/len(order) for n in order}},order)

def observation(run,memory):
    s=run.state;knowledge={}
    provenance={p['evidence_id']:p for p in s.get('semantic_provenance',[])}
    for event in s.get('active_trace',[]):
        if event.get('evidence_id'):
            action=event['action'];p=provenance[event['evidence_id']]
            knowledge[action['domain']+':'+action.get('horse_number','race')]=project(action,p['backend_response'])
    return {'mode':'prediction','basic_race_card':copy.deepcopy(s['active_initial']['card']),
            'research_policy':copy.deepcopy(memory),'evidence':knowledge,'trajectory':trajectory(s),
            'available_domains':list(SUPPORTED),'unavailable_domains':copy.deepcopy(MISSING),
            'model_turn':s.get('model_turns',0),'max_model_turns':40,'query_budget':None,
            'ledger':summary(s,run.config),'safety_remaining_wall_seconds':None}

def finalization_observation(run,memory):
    obs=observation(run,memory)
    for k in ('available_domains','unavailable_domains','model_turn','max_model_turns','query_budget'):
        obs.pop(k)
    return obs

def mark_delivery(run,race,prediction,origin,reason=None):
    category='execution_fallback' if origin=='framework' else (
        'uniform_submit' if is_uniform(prediction) else
        'direct_predictor' if origin=='research' else 'repaired_predictor')
    metadata={'delivery':category,'generated_by':'framework' if origin=='framework' else 'agent',
              'submit_phase':origin,'reason':reason,'bonus_eligible':category in ELIGIBLE}
    run.state.setdefault('aligned_delivery',{})[race]=metadata
    run.state['submissions'][race].update(delivery=category,format_fallback=origin=='framework')
    run.persist()
    return metadata

def predict(run,agent,memory,research_predict):
    s=run.state;batch=s['schedule'][0];race=batch['races'][0];day=batch['day']
    if s.get('research_closed') or s.get('finalization_repair_requests'):
        raise IntegrityFailure('Cannot restart an already finalized research session')
    # Persisted inputs are verified before research can overwrite them.
    try:
        verify_integrity(run,memory,require_card=False)
    except IntegrityFailure:
        s['delivery_failure_kind']='hard_integrity_failure'
        raise
    try:
        got=research_predict(run,agent,memory)
        verify_integrity(run,memory)
        if got!=(race,day) or race not in s['submissions']:
            raise IntegrityFailure('Invalid direct delivery binding')
        mark_delivery(run,race,s['submissions'][race]['prediction'],'research')
        return got
    except IntegrityFailure:
        s['delivery_failure_kind']='hard_integrity_failure'
        raise
    except Exception as exc:
        # Rejected is fail-closed unless it is a verified terminal provider
        # error. Validation/provenance/temporal errors are never masked.
        if isinstance(exc,Rejected) and not isinstance(exc,(ContextLimit,ProviderCostLimit)):
            if classify_failure(exc,s)!='provider_failure':
                raise IntegrityFailure('hard_integrity_failure: '+str(exc)) from exc
        verify_integrity(run,memory)
        if s['submissions']:
            raise IntegrityFailure('Execution exception after a partial/ambiguous seal') from exc
        reason=classify_failure(exc,s)
        s['delivery_failure_kind']='execution_failure'
        s['research_closed']=True
        s['graceful_trigger']=reason
        # Terminal normal requests are abandoned explicitly, not resumed.
        if s.get('pending'):
            s.setdefault('abandoned_provider_requests',[]).append({**s['pending'],'reason':'research_closed'})
            s['pending']=None
        run.persist()
    numbers=[r['number'] for r in s['active_initial']['card']['runners']]
    context=finalization_observation(run,memory)
    s['finalization_context']=copy.deepcopy(context)
    s['finalization_repair_considered']=1
    immutable=digest([s['active_trace'],s.get('semantic_provenance',[]),s['ledger']])
    action=None
    repair_stage='model_request'
    try:
        response=agent.finalize(run.service,context,tools(numbers)[-1])
        if digest([s['active_trace'],s.get('semantic_provenance',[]),s['ledger']])!=immutable:
            raise IntegrityFailure('Repair changed research evidence or tool ledger')
        repair_stage='submission_validation'
        action=parse(response,numbers,race)
        if action['type']!='submit':raise Rejected('Finalization permits only submit')
        s['finalization_repair_success']=True
    except IntegrityFailure:
        raise
    except Exception as exc:
        action=None
        # Validate again before any fallback; a failure during repair cannot
        # excuse mutated legal context or corrupt provenance.
        verify_integrity(run,memory)
        if digest([s['active_trace'],s.get('semantic_provenance',[]),s['ledger']])!=immutable:
            raise IntegrityFailure('Repair mutated immutable research state') from exc
        s['finalization_repair_success']=False
        s['finalization_failure']='invalid_terminal_action' if repair_stage=='submission_validation' else classify_failure(exc,s)
        if s['finalization_failure']=='hard_integrity_failure':
            raise IntegrityFailure('Unclassified finalization rejection: '+str(exc)) from exc
        if s.get('pending'):
            s.setdefault('abandoned_provider_requests',[]).append({**s['pending'],'reason':'finalization_failed'})
            s['pending']=None
    verify_integrity(run,memory)
    if action is None:
        action={'type':'submit','prediction':uniform_prediction(numbers)}
        origin='framework'
    else:
        origin='finalization'
    receipt=seal(run,race,action,numbers)
    event={'phase':origin,'clock':s['clock'],'action':copy.deepcopy(action),
           'cost':receipt['_cost'],'sealed':True,'generated_by':'framework' if origin=='framework' else 'agent'}
    if origin=='finalization':event['request_id']=s['llm_trajectory'][-1]['request_id']
    s.setdefault('delivery_events',[]).append(event)
    s['submission_mode']= 'execution_fallback' if origin=='framework' else 'finalization_repair'
    mark_delivery(run,race,action['prediction'],origin,reason)
    return race,day
