"""Isolated adapters around unchanged acquisition, predictor and graceful code."""
import copy
import json
from types import FunctionType
from jrdb_lh.common import digest
from active_research.policy_amortized import predictor as base
from active_research.policy_amortized import audit as full_audit
from active_research.aligned import delivery
from active_research.aligned.model import guard_body, FINALIZATION_INSTRUCTION
from active_research.aligned.integrity import IntegrityFailure
from active_research.adaptive.domains import tools
from .view import audit_package, project_observation, serialize
from .view import exact


def observation(run, memory=None):
    try:
        value = project_observation(base.observation(run, memory))
        run.state.update(audit_package(run.state['evidence_package'], value['EvidencePackage']))
        run.persist()
        return value
    except Exception as exc:
        raise IntegrityFailure('Semantic JSON evidence: ' + str(exc)) from exc


def verify_semantics(state):
    """Validate actual compact requests, then reuse all original full-source checks.

Only an ephemeral audit copy gets reconstructed full request payloads. The actual
compact body is size-checked below; a reconstructed, unsent full body is not
subject to a provider size limit. Delivery IDs are remapped only in that copy.
"""
    legacy = copy.deepcopy(state)
    if 'evidence_package' in state:
        expected_digests = audit_package(state['evidence_package'])
        if any(k in state for k in expected_digests) or 'prediction_context' in state or 'finalization_context' in state:
            if any(state.get(k) != v for k, v in expected_digests.items()):
                raise IntegrityFailure('Semantic JSON/full digest linkage changed')
    for row in legacy.get('llm_trajectory', []):
        actual = row['request']; guard_body(actual)
        obs = json.loads(actual['messages'][1]['content'])
        phase = row['phase']; key = 'finalization_context' if phase == 'finalization-1' else 'prediction_context'
        full_obs = {k: copy.deepcopy(v) for k, v in obs.items() if k not in ('EvidencePackage',)}
        full_obs['EvidencePackage'] = copy.deepcopy(state['evidence_package'])
        if exact(obs) != exact(project_observation(full_obs)) or exact(obs) != exact(state[key]):
            raise IntegrityFailure('Semantic JSON prediction context changed')
        audit_package(state['evidence_package'], obs['EvidencePackage'])
        numbers = [r['number'] for r in state['active_initial']['card']['runners']]
        prompt = base.PROMPT + (FINALIZATION_INSTRUCTION if phase == 'finalization-1' else '')
        expected = base.body(state['policy_provider_settings'], [{'role': 'system', 'content': prompt},
            {'role': 'user', 'content': serialize(obs)}], [tools(numbers)[-1]])
        expected['tool_choice'] = {'type': 'function', 'function': {'name': 'submit'}}
        old_id = digest([state['run_id'], phase, actual])
        if actual != expected or row['request_id'] != old_id:
            raise IntegrityFailure('Semantic JSON request/profile binding')
        legacy[key] = full_obs
        row['request']['messages'][1]['content'] = json.dumps(full_obs, ensure_ascii=False)
        row['request_id'] = digest([state['run_id'], phase, row['request']])
        for event in legacy.get('delivery_events', []):
            if event.get('request_id') == old_id:event['request_id'] = row['request_id']
    bound = FunctionType(full_audit.verify_semantics.__code__,
                         {**full_audit.verify_semantics.__globals__, 'guard_body': lambda unsent: None})
    result = bound(legacy)
    result['semantic_json_equality'] = 'PASS'
    return result


def verify_integrity(run, memory=None, require_card=True):
    bound = FunctionType(full_audit.verify_integrity.__code__,
                         {**full_audit.verify_integrity.__globals__, 'verify_semantics': verify_semantics})
    return bound(run, memory, require_card)


normal_predict = FunctionType(base.normal_predict.__code__, {**base.normal_predict.__globals__, 'observation': observation})


def graceful_function():
    return FunctionType(delivery.predict.__code__, {**delivery.predict.__globals__,
        'verify_integrity': verify_integrity, 'finalization_observation': observation})


predict = FunctionType(base.predict.__code__, {**base.predict.__globals__,
    'normal_predict': normal_predict, 'graceful_function': graceful_function})
class Predictor(base.Predictor):
    # Reuse the original prompt/tool construction, changing only JSON whitespace.
    from types import SimpleNamespace
    call = FunctionType(base.Predictor.call.__code__, {
        **base.Predictor.call.__globals__,
        'json': SimpleNamespace(dumps=lambda value, **unused: serialize(value))})
