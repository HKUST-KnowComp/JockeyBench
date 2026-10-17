import sys
import pytest
if sys.platform!='darwin':pytest.skip('Native integration on Mini',allow_module_level=True)
from jockeybench.research.policy_amortized.semantic_json.report import provider, representation


def test_complete_session_cost_includes_finish_and_retries():
    state={'run_id':'updater','model_outputs':[{'prompt_tokens':10,'completion_tokens':20,'cost_usd':.01} for _ in range(4)],
           'llm_trajectory':[{'logical_request_elapsed_seconds':10} for _ in range(4)],
           'provider_attempts':[{'request_id':str(i),'attempt_index':i,'status':'complete'} for i in range(4)]}
    state['provider_attempts'].insert(3,{'request_id':'3','attempt_index':3,'status':'http_error','reserved_cost_usd':.2})
    got=provider([state])
    assert got['logical_requests']==4 and got['provider_attempts']==5 and got['retries']==1
    assert got['estimated_cost_usd']==.04 and len(got['failed_attempts_actual_charge_unknown'])==1


def test_context_failure_records_unsent_actual_size_without_truncation():
    s={'graceful_trigger':'context_limit','request_sizes':[{'phase':'prediction-1','bytes':300000,'sent':False}]}
    r=representation([s])
    assert r['context_limit_races']==1 and r['request_bytes']['max']==300000 and r['headroom_bytes']['max']==-37856
