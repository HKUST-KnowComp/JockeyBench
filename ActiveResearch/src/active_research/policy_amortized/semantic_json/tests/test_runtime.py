import sys
import pytest
if sys.platform != 'darwin':
    pytest.skip('Native runtime integration runs on Mini; codec tests are portable', allow_module_level=True)

import copy
from types import FunctionType
from unittest.mock import patch
from jrdb_lh.common import digest
from active_research.policy_amortized.tests import test_policy as legacy
from active_research.policy_amortized.tests.test_policy import source, config, service, active, run, policy, offline
from active_research.policy_amortized import predictor, audit as full_audit
from active_research.adaptive import domains
from active_research.policy_amortized.semantic_json import runtime
from active_research.policy_amortized.semantic_json.view import audit_package

execute = FunctionType(legacy.execute.__code__, {**legacy.execute.__globals__, 'predict': runtime.predict, 'Predictor':runtime.Predictor}, argdefs=legacy.execute.__defaults__)
released = FunctionType(legacy.released.__code__, {**legacy.released.__globals__, 'verify_semantics': runtime.verify_semantics})


def test_18_19_20_21_22_full_source_accounting_frame_and_scorer(run, policy):
    (_, day), requests = execute(run, policy)
    state=run.state
    assert state['query_count']==7 and state['policy_execution']['llm_calls']==0
    assert 'EvidencePackage' in state['prediction_context']
    from active_research.policy_amortized.semantic_json.view import serialize, semantic_view
    assert state['prediction_context']['EvidencePackage']==semantic_view(state['evidence_package'])
    assert requests[0]['messages'][1]['content']==serialize(state['prediction_context'])
    assert requests[0]['messages'][0]['content']==predictor.PROMPT
    before=copy.deepcopy([state['evidence_package'],state['ledger'],state['active_trace'],state['semantic_provenance']])
    assert runtime.verify_semantics(state)['semantic_json_equality']=='PASS'
    assert before==[state['evidence_package'],state['ledger'],state['active_trace'],state['semantic_provenance']]
    assert audit_package(state['evidence_package'])['full_evidence_digest']==state['evidence_package_digest']
    row=released(run,policy)
    from active_research.policy_amortized.frame import make_frame
    from jrdb_lh.common import release
    frame=make_frame([row],[],day,policy,release(day),[row['race_id']])
    assert row['EvidencePackage_bytes']==frame['acquisition']['EvidencePackage_bytes']['mean']
    assert row['tool_calls']==7


@pytest.mark.parametrize('mode,repair,delivery',[('invalid','valid','repaired_predictor'),('provider','fail','execution_fallback')])
def test_existing_graceful_paths(run,policy,mode,repair,delivery):
    (race,_),requests=execute(run,policy,mode,repair)
    assert run.state['aligned_delivery'][race]['delivery']==delivery
    assert runtime.verify_semantics(run.state)['delivery']=='PASS'
    assert runtime.graceful_function().__code__ is predictor.graceful_function().__code__


def test_25_26_actual_compact_oversize_uses_existing_fallback(run,policy):
    original=domains.project
    def large(action, answer):
        view=original(action,answer)
        if action['domain']=='recent_history':view['scope']+='x'*270000
        return view
    with patch.object(domains,'project',large),patch.object(full_audit,'project',large):
        (race,_),requests=execute(run,policy)
    assert not requests
    assert run.state['graceful_trigger']=='context_limit'
    assert run.state['aligned_delivery'][race]['delivery']=='execution_fallback'
    assert all(not x['sent'] for x in run.state['request_sizes'])


def test_no_prompt_or_control_loop_change():
    assert runtime.Predictor.call.__code__ is predictor.Predictor.call.__code__
    assert runtime.normal_predict.__code__ is predictor.normal_predict.__code__
    assert runtime.predict.__code__ is predictor.predict.__code__


