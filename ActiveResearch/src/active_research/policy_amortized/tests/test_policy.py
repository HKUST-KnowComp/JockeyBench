import copy
import json
from dataclasses import replace
from unittest.mock import patch
import pytest
from jrdb_lh.common import Rejected,release,cutoff,digest,packed
from jrdb_lh.audit import replay_economic
from active_research.adaptive.test_adaptive import source,config,service,active,run
from active_research.aligned.tests.test_graceful import bind,nonuniform
from active_research.aligned.tests.test_aligned import SETTINGS,reply
from active_research.test_daily_session import Response
from active_research.runner import settle
from active_research.aligned.scoring import checkpoint_components
from active_research.aligned.integrity import IntegrityFailure
from active_research.policy_amortized.policy_contract import *
from active_research.policy_amortized.policy_builder import Registry,matched_pair,construct,revise,update_input
from active_research.policy_amortized.predictor import Predictor,predict,graceful_function
from active_research.policy_amortized.service import PolicyService
from active_research.policy_amortized.audit import verify_semantics
from active_research.policy_amortized.frame import extract,make_frame,association
from active_research.policy_amortized.lifecycle import coordinate
from active_research.policy_amortized.report import usage,split

SOURCE='''def collect_evidence(sdk, context):
    for horse in context["runners"]:
        sdk.call("recent_history", horse_number=horse["number"])
    sdk.call("training", horse_number=context["runners"][0]["number"])
    sdk.call("track_context")
    return sdk.package()
'''
SOURCE2='''def collect_evidence(sdk, context):
    sdk.call("track_context")
    return sdk.package()
'''

@pytest.fixture(autouse=True)
def offline():
    with patch('socket.socket.connect',side_effect=AssertionError('NO NETWORK')),patch('jrdb_lh.provider.load_credential',side_effect=AssertionError('NO KEYS')),patch('jrdb_lh.provider.time.sleep'):
        yield

@pytest.fixture
def policy(tmp_path):return Registry(tmp_path/'policies').save(SOURCE)

def execute(run,policy,mode='direct',repair='valid'):
    bind(run);run.service.__class__=PolicyService
    requests=[]
    def http(req,timeout):
        b=json.loads(req.data);requests.append(b);s=run.state
        obs=json.loads(b['messages'][1]['content']);numbers=[r['number'] for r in obs['basic_race_card']['runners']]
        isrepair='Research is now closed.' in b['messages'][0]['content']
        if (not isrepair and mode=='provider') or (isrepair and repair=='fail'):raise OSError('offline failure')
        if not isrepair and mode=='invalid':return Response(reply('get_training',{'horse_number':numbers[0]}))
        if isrepair and repair=='invalid':return Response(reply('submit',{'ranking':[]}))
        uniform=mode=='uniform' or (isrepair and repair=='uniform')
        pred={'ranking':numbers,'probabilities':{n:1/len(numbers) for n in numbers}} if uniform else nonuniform(numbers)
        return Response(reply('submit',pred))
    with patch('urllib.request.urlopen',side_effect=http):got=predict(run,Predictor(SETTINGS,policy))
    return got,requests

def released(run,policy):
    from jrdb_lh.ledger import summary
    s=run.state;day=s['schedule'][0]['day'];race=s['schedule'][0]['races'][0]
    s['prediction_phase_cost']=summary(s,run.config);s.pop('strategy_job',None);s.pop('strategy_job_usage',None)
    settle(run,race,day);s['scores'][race]['format_fallback']=s['submissions'][race]['format_fallback']
    s['aligned_scores']=checkpoint_components(s)
    replay_economic(run.config,s,source=run.source);verify_semantics(s)
    return extract(s,policy,release(day))

def test_01_zero_llm():
    with patch('urllib.request.urlopen',side_effect=AssertionError('NO LLM')):
        assert validate_source(SOURCE)['zero_llm']

@pytest.mark.parametrize('name',['results','submit','unknown','provider_signals','reference_odds'])
def test_02_only_tools(name):
    with pytest.raises(PolicyInvalid):validate_source('def collect_evidence(sdk, context):\n    sdk.call('+repr(name)+')\n    return sdk.package()')

@pytest.mark.parametrize('value',["{'ranking': ['01']}","{'probabilities': {'01':1}}","{'winner':'01'}",'predict(context)'])
def test_03_no_prediction_output(value):
    with pytest.raises(PolicyInvalid):validate_source('def collect_evidence(sdk, context):\n    return '+value)

def test_04_05_06_16_package_one_request_accounting_isolation(run,policy):
    (rid,day),requests=execute(run,policy)
    s=run.state;p=s['evidence_package'];assert s['policy_execution']['llm_calls']==0
    assert len(requests)==1 and len(s['llm_trajectory'])==1 and s['aligned_delivery'][rid]['delivery']=='direct_predictor'
    assert len(p['observations'])==len(s['semantic_provenance'])==7
    assert all(x['receipt']['call_micro']>0 for x in p['observations'])
    assert sum(x['receipt']['charged_cells'] for x in p['observations'])>0
    assert p['entrants']==[r['number'] for r in s['active_initial']['card']['runners']]
    assert json.loads(packed(p))==p
    text=json.dumps(requests[0]);assert 'Historical Research Experience' not in text and 'Research-Program Experience Frame' not in text
    assert set(json.loads(requests[0]['messages'][1]['content']))=={'mode','basic_race_card','EvidencePackage','ledger','safety_remaining_wall_seconds'}
    assert [t['function']['name'] for t in requests[0]['tools']]==['submit']
    assert requests[0]['max_tokens'] is None and requests[0]['parallel_tool_calls'] is False
    row=released(run,policy);assert row['tool_calls']==7 and row['research_cost']>0

def scripted(actions):
    actions=iter(actions)
    def call(store,settings,b,phase):
        from active_research.policy_amortized.transport import request
        name,args=next(actions)
        with patch('urllib.request.urlopen',return_value=Response(reply(name,args))):return request(store,settings,b,phase)
    return call

def build_actions(source):return [('inspect_contract',{}),('validate_research_policy',{'source':source}),('save_research_policy',{'source':source}),('finish',{})]

def test_09_12_initial_builder_pair(tmp_path):
    p,s=construct(tmp_path,SETTINGS,Registry(tmp_path/'p'),caller=scripted(build_actions(SOURCE)))
    assert len(s['llm_trajectory'])==4 and p['validation_result']['valid']
    assert len(set(matched_pair(p,tmp_path/'pair').values()))==1

@pytest.mark.parametrize('code',[
 'import os\ndef collect_evidence(sdk, context):\n    return sdk.package()',
 'def collect_evidence(sdk, context):\n    open("/tmp/a", "w")\n    return sdk.package()',
 'def collect_evidence(sdk, context):\n    sdk.__class__\n    return sdk.package()',
 'def collect_evidence(sdk, context):\n    while True: pass\n    return sdk.package()',
 'def collect_evidence(sdk, context):\n    sdk.call("recent_history", horse_number="foreign")\n    return sdk.package()',
 'def collect_evidence(sdk, context):\n    x=__import__("socket")\n    return sdk.package()',
 'def collect_evidence(sdk, context):\n    context["runners"] = []\n    return sdk.package()',
 'def collect_evidence(sdk, context):\n    return None',
 'def wrong(sdk, context):\n    return sdk.package()',
 'not python at all'])
def test_12_validation_security(code):
    with pytest.raises((PolicyInvalid,KeyError)):validate_source(code)

def test_initial_failure_no_framework_policy(tmp_path):
    with pytest.raises(PolicyInvalid,match='initial_program_failure'):
        construct(tmp_path,SETTINGS,Registry(tmp_path/'p'),caller=scripted([('inspect_contract',{}),('finish',{})]))
    assert not list((tmp_path/'p').glob('*.py'))

def get_frame(run,policy):
    execute(run,policy);row=released(run,policy);day=row['day']
    return make_frame([row],[],day,policy,release(day),[row['race_id']])

def test_07_08_11_updater_frame_invalid_retention(run,policy,tmp_path):
    frame=get_frame(run,policy)
    with pytest.raises(Rejected):update_input(policy,frame,cutoff(frame['day']))
    actions=[('inspect_contract',{}),('save_research_policy',{'source':'bad'}),('finish',{})]
    p,s=revise('adaptive_research_program',policy,frame,release(frame['day']),tmp_path,SETTINGS,Registry(tmp_path/'p'),scripted(actions))
    assert p['policy_hash']==policy['policy_hash']
    obs=json.loads(s['llm_trajectory'][0]['request']['messages'][1]['content'])
    assert obs['Research-Program Experience Frame']==frame and obs['current_policy']==policy['source']
    assert 'winners' not in json.dumps(frame) and 'scoring_outcome' not in json.dumps(frame)
    with patch('active_research.policy_amortized.policy_builder.construct',side_effect=AssertionError('Frozen has no updater')):
        assert revise('frozen_research_program',policy,frame,release(frame['day']),tmp_path,SETTINGS,None)==(policy,None)

def test_13_frame_deterministic_and_early_rejected(run,policy):
    execute(run,policy);s=run.state;day=s['schedule'][0]['day'];race=s['schedule'][0]['races'][0]
    with pytest.raises(Rejected):extract(s,policy,cutoff(day))
    row=released(run,policy)
    with pytest.raises(Rejected):make_frame([row],[],day,policy,cutoff(day),[race])
    f=make_frame([row],[],day,policy,release(day),[race]);assert f==make_frame([copy.deepcopy(row)],[],day,policy,release(day),[race])
    assert f['observed_association']['tool_calls_vs_aligned_reward']['status']=='insufficient_sample'
    assert association([1]*5,[2]*5)['status']=='unavailable_constant_input'

@pytest.mark.parametrize('mode,repair,category,bonus',[('direct','valid','direct_predictor',80),('invalid','valid','repaired_predictor',80),('uniform','valid','uniform_submit',0),('invalid','uniform','uniform_submit',0),('provider','fail','execution_fallback',0)])
def test_14_15_delivery_bonus_fallback_population(run,policy,mode,repair,category,bonus):
    (rid,day),requests=execute(run,policy,mode,repair)
    row=released(run,policy);assert row['delivery']==category and row['B_contribution']==bonus
    frame=make_frame([row],[],day,policy,release(day),[rid])
    assert frame['races_executed']==1 and frame['delivery'][category]==1
    if category=='execution_fallback':
        assert row['aligned_reward']<0 and abs(row['log_reward'])<1e-8 and len(requests)==20
    assert sum(t['phase']=='prediction-1' for t in run.state['llm_trajectory'])==1
    assert run.state['query_count']==7

def test_17_usage_decomposition(run,policy,tmp_path):
    execute(run,policy,'invalid','valid')
    p,s=construct(tmp_path,SETTINGS,Registry(tmp_path/'p'),caller=scripted(build_actions(SOURCE)))
    parts=[usage(split([s],'policy_build-')),usage(split([run.state],'prediction-')),usage(split([run.state],'finalization-'))]
    assert [v['logical_requests'] for v in parts]==[4,1,1]
    assert sum(v['estimated_cost_usd'] for v in parts)==pytest.approx(.006)


def test_save_snapshot_immutable_and_complete_builder_report(tmp_path):
    from active_research.policy_amortized.report import build_report
    reg=Registry(tmp_path/'p')
    p,s=construct(tmp_path,SETTINGS,reg,caller=scripted(build_actions(SOURCE)))
    assert p['model_usage']==p['model_usage_at_save']
    assert p['model_usage_scope']=='through_save_request'
    assert p['model_usage_at_save']['logical_requests']==3
    assert p['model_usage_at_save']['cost_usd']==pytest.approx(.003)
    assert s['builder_session_usage']['logical_requests']==4
    assert s['builder_session_usage']['estimated_cost_usd']==pytest.approx(.004)
    assert s['builder_session_usage']['finish_received']
    saved=(reg.folder/'research_policy_v000.json').read_bytes()
    # Returned alias and completed session cannot mutate persisted provenance.
    p['model_usage']['logical_requests']=999
    assert p['model_usage_at_save']['logical_requests']==3
    (tmp_path/'initial-policy.json').write_text(json.dumps(p))
    (tmp_path/'execution-status.json').write_text(json.dumps({'errors':[]}))
    build_report(tmp_path,[],[s])
    report=json.loads((tmp_path/'smoke-report.json').read_text())
    assert report['total_logical_requests']==4
    assert report['total_api_estimate']==pytest.approx(.004)
    assert (reg.folder/'research_policy_v000.json').read_bytes()==saved


def test_multiple_saves_keep_independent_prefixes(tmp_path):
    steps=build_actions(SOURCE)[:-1]+build_actions(SOURCE2)[1:]
    reg=Registry(tmp_path/'p')
    p,s=construct(tmp_path,SETTINGS,reg,caller=scripted(steps))
    first=json.loads((reg.folder/'research_policy_v000.json').read_text())
    assert first['model_usage_at_save']['logical_requests']==3
    assert p['model_usage_at_save']['logical_requests']==5
    assert s['builder_session_usage']['logical_requests']==6


def test_updater_keep_records_full_session_without_rewriting_policy(run,policy,tmp_path):
    frame=get_frame(run,policy);before=copy.deepcopy(policy)
    p,s=revise('adaptive_research_program',policy,frame,release(frame['day']),tmp_path,SETTINGS,
        Registry(tmp_path/'p'),scripted([('inspect_contract',{}),('save_research_policy',{'source':'bad'}),('finish',{})]))
    assert p==before and not list((tmp_path/'p').glob('*.json'))
    assert s['updater_session_usage']['logical_requests']==3
    assert s['updater_session_usage']['estimated_cost_usd']==pytest.approx(.003)
    assert s['updater_session_usage']['termination']=='finish'


def test_interrupted_session_keeps_cost_and_does_not_claim_finish(tmp_path):
    p,s=construct(tmp_path,SETTINGS,Registry(tmp_path/'p'),caller=scripted(build_actions(SOURCE)[:-1]))
    assert p['model_usage_at_save']['logical_requests']==3
    assert s['builder_session_usage']['logical_requests']==3
    assert not s['builder_session_usage']['finish_received']
    assert s['builder_session_usage']['termination']=='terminated_without_finish'


def test_warmup_report_counts_complete_session_once(tmp_path):
    from active_research.policy_amortized.report import build_report
    from active_research.policy_warmup.lifecycle import finalize
    from active_research.policy_amortized.model_usage import capture_session
    state={'llm_trajectory':[{'logical_request_elapsed_seconds':2}]*7,
           'model_outputs':[{'cost_usd':.001}]*7,'provider_attempts':[{'status':'complete'}]*7}
    capture_session(state,'builder','warmup',True)
    (tmp_path/'warmup-session.json').write_text(json.dumps(state))
    (tmp_path/'warmup-accounting.json').write_text(json.dumps({'api_cost_usd_estimate':.007,'weighted_ledger_cost':1}))
    (tmp_path/'execution-status.json').write_text(json.dumps({'errors':[]}))
    build_report(tmp_path,[],[]);finalize(tmp_path)
    report=json.loads((tmp_path/'v2-smoke-report.json').read_text())
    assert report['total_logical_requests']==7
    assert report['total_api_estimate']==pytest.approx(.007)
    assert report['api']['builder']['logical_requests']==7
    # Legacy sessions use the complete transcript, never old policy provenance.
    del state['builder_session_usage']
    (tmp_path/'warmup-session.json').write_text(json.dumps(state))
    build_report(tmp_path,[],[])
    assert json.loads((tmp_path/'smoke-report.json').read_text())['total_logical_requests']==7

def test_18_context_limit_no_pruning(run,policy):
    from active_research.policy_amortized import policy_executor
    original=policy_executor.EvidenceSDK.package
    # Large but legally acquired fixture observation; schema/provenance audit is
    # tested independently. Patch the canonical package identically for audit.
    def large(self):
        p=original(self);p['fixture_large_legal_observation']='x'*270000;return p
    with patch.object(policy_executor.EvidenceSDK,'package',large):
        (rid,_),requests=execute(run,policy)
        assert not requests and run.state['graceful_trigger']=='context_limit'
        assert run.state['aligned_delivery'][rid]['delivery']=='execution_fallback'
        assert len(run.state['evidence_package']['fixture_large_legal_observation'])==270000
        assert all(not x['sent'] for x in run.state['request_sizes'])

@pytest.mark.parametrize('corrupt',['card','ledger','provenance','package','clock'])
def test_integrity_hard_stop(run,policy,corrupt):
    bind(run);run.service.__class__=PolicyService
    def http(req,timeout):
        s=run.state
        if corrupt=='card':s['active_initial']['card']['race_id']='bad'
        if corrupt=='ledger':s['ledger']['cost_micro']+=1
        if corrupt=='provenance':s['semantic_provenance'][0]['race_id']='bad'
        if corrupt=='package':s['evidence_package']['entrants']=[]
        if corrupt=='clock':s['clock']=release(s['schedule'][0]['day'])
        raise RuntimeError('ordinary error cannot hide integrity corruption')
    with patch('urllib.request.urlopen',side_effect=http),pytest.raises(IntegrityFailure):predict(run,Predictor(SETTINGS,policy))
    assert not run.state['submissions'] and not run.state.get('finalization_repair_requests')

def test_graceful_code_reused_exactly():
    from active_research.aligned.delivery import predict as original
    assert graceful_function().__code__ is original.__code__

def test_funding_stop_does_not_raise_cap(run,policy):
    bind(run);run.service.__class__=PolicyService
    settings={**SETTINGS,'reservation_output_tokens':5000000}
    with patch('urllib.request.urlopen',side_effect=AssertionError('Must refuse before network')):
        rid,_=predict(run,Predictor(settings,policy))
    assert run.state['funding_stop']['threshold_usd']==.75
    assert run.state['aligned_delivery'][rid]['delivery']=='execution_fallback'

def test_program_runtime_error_repair_preserves_partial_evidence(run,policy):
    from active_research.policy_amortized import policy_executor
    original=policy_executor.EvidenceSDK.call
    def failing(self,domain,**args):
        if self.run.state['query_count']==2:raise KeyError('ordinary missing field')
        return original(self,domain,**args)
    with patch.object(policy_executor.EvidenceSDK,'call',failing):
        (rid,_),requests=execute(run,policy)
    assert run.state['query_count']==2 and not run.state['policy_execution']['success']
    assert run.state['aligned_delivery'][rid]['delivery']=='repaired_predictor'
    assert [r['phase'] for r in run.state['llm_trajectory']]==['finalization-1']

def test_policy_source_tamper_rejected(run,policy):
    execute(run,policy)
    run.state['policy_source']=SOURCE2
    with pytest.raises(Rejected):verify_semantics(run.state)

def test_interpreter_resource_limits():
    s='def collect_evidence(sdk, context):\n    for x in range(1000000000):\n        pass\n    return sdk.package()'
    with pytest.raises(PolicyRuntimeError):interpret(s,synthetic_cards()[0],SyntheticSDK(synthetic_cards()[0]),.001)
    s='def collect_evidence(sdk, context):\n    x=[0]\n    for i in range(100):\n        x=[x,x]\n    return sdk.package()'
    with pytest.raises(PolicyRuntimeError):validate_source(s)

def test_manifest_config_provider_freeze(tmp_path,monkeypatch):
    from active_research.policy_amortized import prepare
    from active_research.policy_amortized.specification import validate_manifest,PROFILE
    from types import SimpleNamespace
    (tmp_path/'active_research/policy_amortized').mkdir(parents=True)
    (tmp_path/'active_research/policy_amortized/example.py').write_text('value=1')
    out=tmp_path/'output';out.mkdir()
    for name,value in [('provider.json',SETTINGS),('config.json',{}),('offline-gate.json',{'passed':True})]:
        (out/name).write_text(json.dumps(value))
    monkeypatch.setattr(prepare,'PROJECT',tmp_path)
    monkeypatch.setattr(prepare.Config,'load',lambda p:SimpleNamespace(managed_root=str(out/'runs'),limits=SimpleNamespace(model_output_tokens=None)))
    monkeypatch.setattr(prepare,'check_config',lambda c:None)
    m=prepare.freeze(out,'adaptive_research_program',[{'day':'2024-01-06','races':['s1']}]);validate_manifest(m)
    assert m['initial_policy_matched'] and m['research_program_llm_calls']==0
    assert m['normal_prediction_requests_per_race']==1 and m['scoring_profile']=='common-aligned-delivery-v1'
    with pytest.raises(Rejected):prepare.freeze(out,'adaptive_research_program',m['schedule'])
    m['research_program_llm_calls']=1
    with pytest.raises(Rejected):validate_manifest(m)

def test_10_20_multiday_matched_frozen_adaptive(run,policy,tmp_path):
    # Actual paid in-memory service, predictor, scoring and frame on Day1; then
    # coordinator invokes the same callbacks over two synthetic daily populations.
    f=get_frame(run,policy);base=f['per_race'][0];reg=Registry(tmp_path/'p');changes=[]
    def one(day,race,p):
        changes.append((day,p['policy_hash']));return {'race':race,'policy':p,'sealed':True}
    def released_day(day,sealed,p):
        assert all(x['sealed'] for x in sealed)
        return [{**copy.deepcopy(base),'race_id':x['race'],'day':day,'release_clock':release(day),
            'policy_version':p['policy_version'],'policy_hash':p['policy_hash']} for x in sealed]
    def update(p,frame,clock):
        folder=tmp_path/frame['day'];folder.mkdir()
        result,_=revise('adaptive_research_program',p,frame,clock,folder,SETTINGS,reg,scripted(build_actions(SOURCE2)))
        return result
    schedule=[{'day':'2024-01-06','races':['s1','s2']},{'day':'2024-01-07','races':['s3']}]
    for arm in ('adaptive_research_program','frozen_research_program'):
        changes.clear();events=[]
        final,history=coordinate(schedule,policy,arm,one,released_day,make_frame,update,lambda k,**v:events.append((k,v)))
        assert changes[0][1]==changes[1][1]==policy['policy_hash']
        assert (changes[2][1]!=policy['policy_hash'])==(arm=='adaptive_research_program')
        assert len(history)==3 and [k for k,v in events].count('release_gate')==2
        for day in ('2024-01-06','2024-01-07'):
            kinds=[k for k,v in events if v['day']==day]
            assert max(i for i,k in enumerate(kinds) if k=='sealed')<kinds.index('release_gate')<kinds.index('frame_commit')<kinds.index('day_complete')
