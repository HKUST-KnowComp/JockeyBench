"""Offline fixtures only; all provider requests intercepted in memory."""
import copy
import json
from types import FunctionType,SimpleNamespace
from unittest.mock import patch
import pytest
from jockeybench.canonical import digest, packed
from jockeybench.clock import cutoff, release
from jockeybench.errors import Rejected
from jockeybench.protocol.metering import example_cells
from jockeybench.research.policy_amortized.tests import test_policy as old
from jockeybench.research.policy_amortized.tests.test_policy import source,config,service,active,run,offline
from jockeybench.research.policy_amortized.semantic_json import runtime as v2
from jockeybench.research.finegrained_v3 import runtime,control,domains,FRAME
from jockeybench.research.finegrained_v3.native_replay import replay_economic
from jockeybench.research.finegrained_v3.policy_contract import validate_source,parse_source,interpret,SyntheticSDK,synthetic_cards,PolicyInvalid,CONTRACT
from jockeybench.research.finegrained_v3.policy_builder import Registry
from jockeybench.research.finegrained_v3.policy_executor import EvidenceSDK,execute_policy,replay_acquisition
from jockeybench.research.finegrained_v3.statistics import acquisition_statistics,aggregate
from jockeybench.research.finegrained_v3.frame import extract,make_frame
from jockeybench.research.finegrained_v3.gate import evaluate

execute=FunctionType(old.execute.__code__,{**old.execute.__globals__,'predict':runtime.predict,'Predictor':runtime.Predictor},argdefs=old.execute.__defaults__)
execute_v2=FunctionType(old.execute.__code__,{**old.execute.__globals__,'predict':v2.predict,'Predictor':v2.Predictor},argdefs=old.execute.__defaults__)
released=FunctionType(old.released.__code__,{**old.released.__globals__,'verify_semantics':runtime.verify_semantics,'extract':extract,'replay_economic':replay_economic},argdefs=old.released.__defaults__)
CODE='''def collect_evidence(sdk, context):
    sdk.call("track_context")
    for h in context["runners"]:
        a = sdk.call("recent_history", horse_number=h["number"], depth=1)
        if a["domains"]["recent_history"]["deeper_available"]:
            a = sdk.call("recent_history", horse_number=h["number"], depth=3)
        if a["domains"]["recent_history"]["missing_count"] > 0:
            sdk.call("training", horse_number=h["number"])
        elif a["domains"]["recent_history"]["same_distance_records"] == 0:
            sdk.call("condition_stats", horse_number=h["number"])
        sdk.stop_horse(h["number"])
    return sdk.package()
'''

@pytest.fixture
def policy(tmp_path):return Registry(tmp_path/'v3-policies').save(CODE)

def test_control_projection_does_not_leak_predictive_content():
    card=synthetic_cards()[1];a={'domain':'recent_history','horse_number':'01','depth':1}
    o={'starts':[{'day_index':-1,'race_facts':{'surface':1,'distance':1600},
        'runner_facts':{'earned_prize':99999},'values':{'finish':1,'corner4_rank':1,'last3f_tenths_sec':350}}]}
    x=control.quality(a,o,{'next_cursor':'opaque'},card)
    changed=copy.deepcopy(o);changed['starts'][0]['values'].update(finish=5,corner4_rank=9,last3f_tenths_sec=900)
    changed['starts'][0]['runner_facts']['earned_prize']=1
    assert x==control.quality(a,changed,{'next_cursor':'opaque'},card)
    assert set(x)==set(control.DOMAIN_FIELDS)
    text=json.dumps(x);assert all(k not in text for k in ('finish','rank','wins','top3','prize','350','99999'))
    assert set(control.context(card)['runners'][0])=={'number'}

def test_condition_counts_exclude_wins_top3():
    card=synthetic_cards()[1];o={'groups':[{'surface':1,'distance':1600,'starts':3,'wins':2,'top3':3}], 'sample_starts':5,'labelled_starts':3}
    x=control.quality({'domain':'condition_stats'},o,{},card)
    o['groups'][0].update(wins=0,top3=0)
    assert x==control.quality({'domain':'condition_stats'},o,{},card)

@pytest.mark.parametrize('depth',[0,2,4,6,100,True,-1])
def test_bounded_history_depth_rejected(depth):
    with pytest.raises(PolicyInvalid):validate_source(CODE.replace('depth=1','depth='+repr(depth)))

def test_native_depth_cells_cost_serialization(run):
    race=run.state['schedule'][0]['races'][0];observed=[]
    for depth in (1,3,5):
        action={'type':'query','domain':'recent_history','horse_number':'01','depth':depth}
        v,c,_,_=domains.legacy_fetch(run,action,race);p=run.state['semantic_provenance'][-1];r=run.state['audit'][-1]
        assert p['backend_arguments']['limit']==depth
        assert len(v['starts'])==min(depth,2)
        assert c['evidence_cells']==sum(example_cells(x) for x in p['backend_response']['examples'])
        assert c['charged_cells']==c['evidence_cells'] and not c['repeat']
        assert c['total_micro']==run.service.charge('call_yen')+run.service.charge('field_yen',c['charged_cells'])
        assert json.loads(packed(v))==v
        observed.append((c['total_micro'],len(packed(v))))
    assert observed[0][0]<observed[2][0] and observed[0][1]<observed[2][1]
    # Depth 3 and 5 can return the same sparse prefix; arguments still differ.
    assert observed[1]==observed[2]

def test_full_evidence_and_zero_acquisition_one_prediction(run,policy):
    (race,day),requests=execute(run,policy);s=run.state
    assert s['policy_execution']['llm_calls']==0 and len(requests)==1
    assert [r['phase'] for r in s['llm_trajectory']]==['prediction-1']
    assert s['acquisition_complete'] and s['policy_execution']['success']
    assert all('starts' in o['observation'] for o in s['evidence_package']['observations'] if o['domain']=='recent_history')
    assert len(s['evidence_package']['observations'])==len(s['semantic_provenance'])
    assert runtime.verify_semantics(s)['semantic_json_equality']=='PASS'
    assert replay_acquisition(s)['control_view']=='PASS'

def test_conditional_training_and_condition_stats():
    card=synthetic_cards()[1]
    sparse=SyntheticSDK(card,0);rich=SyntheticSDK(card,1)
    interpret(CODE,card,sparse);interpret(CODE,card,rich)
    assert sum(r['domain']=='training' for r in sparse.records)==0
    assert sum(r['domain']=='training' for r in rich.records)==5
    assert sum(r['domain']=='condition_stats' for r in rich.records)==0
    assert sum(r['domain']=='condition_stats' for r in sparse.records)==5
    assert all(v['stopped'] for v in rich.state()['runners'].values())

def test_horse_and_domain_stopping_block_further_calls():
    for stop in ['sdk.stop_horse(h["number"])','sdk.stop_domain("training", h["number"])']:
        code='def collect_evidence(sdk, context):\n    sdk.call("track_context")\n    for h in context["runners"]:\n        '+stop+'\n        sdk.call("training", horse_number=h["number"])\n    return sdk.package()'
        with pytest.raises(PolicyInvalid):validate_source(code)

def test_race_completion_requires_track_once():
    for code in ['def collect_evidence(sdk, context):\n    return sdk.package()',
        'def collect_evidence(sdk, context):\n    sdk.call("track_context")\n    sdk.call("track_context")\n    return sdk.package()']:
        with pytest.raises(PolicyInvalid):validate_source(code)

@pytest.mark.parametrize('body',[
    'x = sdk.run', 'x = sdk.__class__', 'x = sdk.state()["finish"]',
    'x = sdk.state()["wins"]', 'x = sdk.state()["top3"]',
    'probabilities = {}', 'ranking = [1]', 'import os',
    'x = open("anything")', 'x = getattr(sdk, "run")',
    'sdk.call("results")', 'sdk.call("provider_signals")',
    'sdk.call = 1', 'while True:\n        pass',
    'for x in range(100000000):\n        pass',
    'x = context["runners"][0]["facts"]',
    'return {"winner": "01"}',
])
def test_validator_blocks_prediction_escape_and_unbounded_loops(body):
    code='def collect_evidence(sdk, context):\n    sdk.call("track_context")\n    '+body+'\n    return sdk.package()'
    with pytest.raises((PolicyInvalid,KeyError)):validate_source(code)

def test_helpers_and_arithmetic_but_not_recursion():
    p='def fraction(x, y):\n    return x / max(y, 1)\n\ndef collect_evidence(sdk, context):\n    sdk.call("track_context")\n    x = fraction(2, 3)\n    if x > 0:\n        pass\n    return sdk.package()'
    assert validate_source(p)['valid']
    with pytest.raises(PolicyInvalid):parse_source(p.replace('return x / max(y, 1)','return fraction(x, y)'))

def test_v2_compatibility_same_calls_evidence_ledger_request_and_scorer(run,tmp_path):
    p=Registry(tmp_path/'policies').save(old.SOURCE)
    # Identical in-memory source/state and deterministic interpreter CPU clock.
    initial=copy.deepcopy(run.state);initial_class=run.service.__class__
    with patch('time.process_time',return_value=1.0):
        execute_v2(run,p)
    old_state=copy.deepcopy(run.state)
    run.state.clear();run.state.update(initial);run.service.state=run.state;run.service.__class__=initial_class
    with patch('time.process_time',return_value=1.0):
        execute(run,p)
    s=run.state
    # Logical provenance has a new namespace; model-visible semantic evidence is identical.
    from jockeybench.research.policy_amortized.semantic_json.view import semantic_view
    assert semantic_view(old_state['evidence_package'])==semantic_view(s['evidence_package'])
    for before,after in zip(old_state['evidence_package']['observations'],s['evidence_package']['observations']):
        assert before['observation']==after['observation']
        assert {k:before['receipt'][k] for k in ('call_micro','data_micro','total_micro','charged_cells')}=={k:after['receipt'][k] for k in ('call_micro','data_micro','total_micro','charged_cells')}
    assert [{k:v for k,v in e.items() if k!='id'} for e in old_state['ledger']['entries']]==[{k:v for k,v in e.items() if k!='id'} for e in s['ledger']['entries']]
    assert [p['backend_arguments'] for p in old_state['semantic_provenance']]==[p['backend_arguments'] for p in s['semantic_provenance']]
    assert [t['request'] for t in old_state['llm_trajectory']]==[t['request'] for t in s['llm_trajectory']]
    assert old_state['submissions']==s['submissions']

def test_checkpoint_replay_no_new_tool_model_calls(run,policy):
    execute(run,policy);s=copy.deepcopy(run.state);before=packed(s)
    assert replay_acquisition(s)['calls']==len(s['semantic_provenance'])
    assert packed(s)==before
    s['active_trace'][0]['control_after']['field_size']=999
    with pytest.raises(PolicyInvalid):replay_acquisition(s)

def test_partial_checkpoint_broker_replay_does_not_rebuy(run):
    from jockeybench.research.aligned.tests.test_graceful import bind
    from jockeybench.research.policy_amortized.service import PolicyService
    from jockeybench.research.runner import basic_card
    bind(run);run.service.__class__=PolicyService
    s=run.state;s.update(active_trace=[],semantic_provenance=[],query_count=0)
    raw=run.service.call('card',{'race_id':s['schedule'][0]['races'][0]})
    s['active_initial']={'card':basic_card(raw),'receipt':raw['_cost']}
    from jockeybench.research.finegrained_v3.source_binding import ensure
    ensure(run)
    initial=copy.deepcopy(s)
    class InterruptedSDK(EvidenceSDK):
        def call(self,*a,**kw):
            value=super().call(*a,**kw)
            if self.cursor==2:raise RuntimeError('simulated interruption after durable commit')
            return value
    with pytest.raises(RuntimeError):interpret(old.SOURCE,s['active_initial']['card'],InterruptedSDK(run))
    prior_calls=len(s['audit']);sdk=EvidenceSDK(run)
    resumed=interpret(old.SOURCE,s['active_initial']['card'],sdk)
    assert len(s['audit'])==prior_calls+5  # seven queries total, first two replayed
    resumed_state=copy.deepcopy(s)
    s.clear();s.update(initial);run.service.state=s
    continuous=interpret(old.SOURCE,s['active_initial']['card'],EvidenceSDK(run))
    assert resumed==continuous and resumed_state['ledger']==s['ledger']

def test_live_audit_accepts_collecting_and_finalized_commits(run,policy):
    from jockeybench.research.finegrained_v3.audit_live import evidence_lifecycle
    from jockeybench.research.aligned.integrity import IntegrityFailure
    execute(run,policy);s=copy.deepcopy(run.state)
    assert evidence_lifecycle(s)['stage']=='sealed'
    s['evidence_package']['observations'][0]['observation']['facts']['invented']=1
    with pytest.raises(IntegrityFailure):evidence_lifecycle(s)

def test_result_release_boundary_frame_and_common_scorer(run,policy):
    (_,day),_=execute(run,policy)
    with pytest.raises(Rejected):run.service.call('results',{'race_id':run.state['schedule'][0]['races'][0]})
    with pytest.raises(Rejected):extract(run.state,policy,cutoff(day))
    row=released(run,policy)
    f=make_frame([row],[],day,policy,release(day),[row['race_id']])
    assert f==make_frame(copy.deepcopy([row]),[],day,policy,release(day),[row['race_id']])
    assert f['finegrained']['domains']['recent_history']['calls']>0
    assert f['observed_association']['interpretation'].startswith('descriptive association')
    from jockeybench.research.common_scoring import VERSION,B,MULTIPLIER
    assert VERSION=='common-score-yen-v2-b100-ungated-v1' and B==100 and MULTIPLIER==.5

def test_depth_statistics_and_trigger_states(run,policy):
    execute(run,policy);stats=acquisition_statistics(run.state);a=aggregate([stats])
    h=a['domains']['recent_history']
    assert sum(h['final_depth_distribution'].values())==5
    assert h['expand_1_to_3_count']==5
    assert h['raw_cost']==h['weighted_cost']/.5
    assert aggregate([stats])==a
    assert 'evidence_completeness_at_stopping' in a['stopping']

def test_gate_quality_not_used():
    rows=[{'race_id':str(i),'sealed':True,'response_models':['deepseek-ai/DeepSeek-V4-Flash-0731'],
        'acquisition_llm_calls':0,'normal_prediction_calls':1,'request_bytes':1000,'context_limit':False,
        'delivery':['direct_predictor'],'policy_success':True,'control_audit':'PASS','ledger_correct':True,
        'policy_cpu_seconds':.05,'policy_cpu_limit':30,'source_binding_valid':True,'source_full_verifications':1,'source_reuse_checks':2,'marginal_accounting':{'logical_actions':2},'acquisition_runtime_failure':False,
        'release_count':0,'cost_safety_stop':False,'winner':'UNREAD','score':'UNREAD'} for i in range(5)]
    a=evaluate(rows,{'race_ids':[str(i) for i in range(5)]})
    assert a['passed'] and a['outcomes_inspected'] is False

def test_frozen_predictor_and_release_code():
    from jockeybench.research.policy_amortized import predictor
    assert runtime.normal_predict.__code__ is predictor.normal_predict.__code__
    assert runtime.predict.__code__ is predictor.predict.__code__
    assert runtime.Predictor.call.__code__ is predictor.Predictor.call.__code__
    from jockeybench.research.finegrained_v3.lifecycle import coordinate
    from jockeybench.research.policy_amortized.lifecycle import coordinate as frozen
    assert coordinate is frozen

def test_updater_release_validation_and_save_behavior(tmp_path):
    from jockeybench.research.finegrained_v3.policy_builder import update_input
    p=Registry(tmp_path/'policies').save(old.SOURCE)
    f={'protocol':FRAME,'day':'2024-01-06',
       'release_clock':release('2024-01-06'),'policy_identity':{'policy_hash':p['policy_hash']}}
    f['frame_digest']=digest(f)
    assert update_input(p,f,release(f['day']))['current_policy']==p['source']
    with pytest.raises(Rejected):update_input(p,f,cutoff(f['day']))
    with pytest.raises(PolicyInvalid):Registry(tmp_path/'invalid').save('import os')
    assert 'not which horse is likely to win' in CONTRACT

def test_final_reporting_counts_warmup_once_and_keeps_api_separate(run,policy):
    from jockeybench.research.finegrained_v3.report import economic_totals
    from jockeybench.research.common_scoring import checkpoint_components
    execute(run,policy);released(run,policy)
    c=checkpoint_components(run.state);warm={'weighted_ledger_cost':3.0}
    result=economic_totals([c],warm)
    assert result['common_net']==c['common_Net']-3.0
    assert result['weighted_cost']==c['cost']['weighted_research_cost']+3.0
    assert result['API_USD_included_in_common_net'] is False

def test_phase_price_float_tail_is_not_a_configuration_edit():
    from jockeybench.research.finegrained_v3.prepare import same_phase_parameter
    assert same_phase_parameter('input_usd_per_million',.06,.060000000000000005)
    assert not same_phase_parameter('input_usd_per_million',.06,.06000001)
    assert not same_phase_parameter('model','deepseek','glm')
