"""End-to-end marginal runtime, audit, release and frozen predictor tests."""
import copy
from unittest.mock import patch
import pytest
from jrdb_lh.common import digest,release,Rejected
from .test_marginal_cost import source,config,service,active,run,prepared,clone,clone_initial,totals,baseline
from .test_v3 import execute,released
from active_research.finegrained_v3.policy_builder import Registry
from active_research.finegrained_v3.policy_executor import EvidenceSDK,replay_acquisition
from active_research.finegrained_v3.marginal_cost import MarginalAcquisition,audit_owned_cache,seal
from active_research.finegrained_v3.native_replay import replay_economic
from active_research.finegrained_v3.audit_live import evidence_lifecycle
from active_research.finegrained_v3.frame import make_frame

@pytest.fixture(autouse=True)
def offline():
    with patch('urllib.request.urlopen',side_effect=AssertionError('NO PROVIDER')):yield

CODE = """def collect_evidence(sdk, context):
    sdk.call("track_context")
    h = context["runners"][0]["number"]
    sdk.call("recent_history", horse_number=h, depth=1)
    sdk.call("recent_history", horse_number=h, depth=3)
    sdk.call("recent_history", horse_number=h, depth=5)
    sdk.call("recent_history", horse_number=h, depth=5)
    sdk.call("condition_stats", horse_number=h)
    for h in context["runners"]:
        sdk.call("training", horse_number=h["number"])
    return sdk.package()
"""

def test_integrated_progressive_predictor_release_frame(prepared,tmp_path):
    # Full original bind and mocked prediction; real tariff on memory fixture.
    prepared.state.clear();prepared.state.update(copy.deepcopy(prepared._test_precard))
    policy=Registry(tmp_path/'policies').save(CODE)
    (_,day),requests=execute(prepared,policy);s=prepared.state
    assert s['policy_execution']['success'] and len(requests)==1 and s['policy_execution']['llm_calls']==0
    h=[e for e in s['active_trace'] if e['action'].get('domain')=='recent_history']
    assert [e['newly_acquired_cells'] for e in h]==[98,196,196,0]
    assert [e['native_acquisition_operations'] for e in h]==[1,1,1,0]
    assert [len(o['observation']['starts']) for o in s['evidence_package']['observations'] if o['domain']=='recent_history']==[1,3,5,5]
    c=next(e for e in s['active_trace'] if e['action'].get('domain')=='condition_stats')
    assert c['cost']['total_micro']==0 and c['local_projection']
    training=[e for e in s['active_trace'] if e['action'].get('domain')=='training']
    assert sum(e['native_acquisition_operations'] for e in training)==1 and all(e['local_projection'] for e in training[1:])
    accounting=audit_owned_cache(s,complete=True)
    assert accounting=={'logical_actions':11,'native_acquisition_operations':5,'newly_acquired_cells':561,'local_projections':11,'cache_only_actions':6}
    before=digest(s);replay_economic(prepared.config,s,source=prepared.source);replay_acquisition(s)
    assert digest(s)==before and evidence_lifecycle(s)['stage']=='sealed'
    row=released(prepared,policy);frame=make_frame([row],[],day,policy,release(day),[row['race_id']])
    assert frame['acquisition']['logical_policy_actions']==11
    assert frame['acquisition']['native_acquisition_operations']==5
    assert frame['acquisition']['newly_acquired_cells']==561
    assert frame['acquisition']['local_projections']==11
    assert frame['acquisition']['cache_only_actions']==6
    assert frame['finegrained']['accounting']==row['finegrained']['accounting']


def test_condition_first_history_shallow_keeps_requested_evidence(prepared):
    a=MarginalAcquisition(prepared);a.call('condition_stats','01');n=len(prepared.state['audit'])
    for d in (1,3,5):
        row=a.call('recent_history','01',d)
        assert len(row['observation']['starts'])==d and row['receipt']['total_micro']==0
    assert len(prepared.state['audit'])==n
    a.verify()


def test_broker_commit_before_event_replay_is_free(prepared):
    s=prepared.state;s.update(active_trace=[],semantic_provenance=[],query_count=0)
    a=MarginalAcquisition(prepared);a.call('recent_history','01',1,logical_id='policy-1')
    ledger=copy.deepcopy(s['ledger']);n=len(s['audit'])
    assert audit_owned_cache(s)['logical_actions']==1
    with patch.object(prepared.service,'call',side_effect=AssertionError('NO REBUY')):
        EvidenceSDK(prepared).call('recent_history',horse_number='01',depth=1)
    assert s['ledger']==ledger and len(s['audit'])==n
    assert audit_owned_cache(s,complete=True)['logical_actions']==1


@pytest.mark.parametrize('damage',['provenance_clock','invoice','source_scope','cursor_proof'])
def test_integrated_audit_rejects_tampering(prepared,tmp_path,damage):
    prepared.state.clear();prepared.state.update(copy.deepcopy(prepared._test_precard))
    policy=Registry(tmp_path/'policies').save(CODE);execute(prepared,policy)
    s=copy.deepcopy(prepared.state)
    if damage=='provenance_clock':s['semantic_provenance'][0]['clock']+=1
    elif damage=='invoice':s['active_trace'][2]['cost']['total_micro']+=1
    elif damage=='source_scope':
        row=s['marginal_acquisition']['logical_actions'][-1]
        row['native_sources']=s['marginal_acquisition']['logical_actions'][1]['native_sources'];seal(row)
    else:
        proof=s['marginal_acquisition']['cursor_rebindings'][0]
        proof['to_arguments']['number']='02';seal(proof)
        with pytest.raises(Rejected):replay_economic(prepared.config,s,source=prepared.source)
        return
    with pytest.raises(Rejected):audit_owned_cache(s,complete=True)
