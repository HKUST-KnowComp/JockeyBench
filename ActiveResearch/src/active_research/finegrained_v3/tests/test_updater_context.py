"""Offline functional updater tests; no real provider or saved run access."""
import copy
import json
from unittest.mock import patch
import pytest
from jrdb_lh.common import packed, digest, release, cutoff, Rejected
from active_research.aligned.tests.test_aligned import SETTINGS
from active_research.aligned.model import ContextLimit
from active_research.finegrained_v3.policy_builder import Registry, construct, toolset
from active_research.finegrained_v3.policy_contract import validate_source
from active_research.finegrained_v3.updater_context import context, statistics, fit, lifecycle_checkpoint
from .test_integration import source,config,service,active,run,prepared
from .test_v3 import execute,released,CODE
from active_research.finegrained_v3.frame import make_frame


@pytest.fixture(autouse=True)
def offline():
    with patch('urllib.request.urlopen',side_effect=AssertionError('NO REAL PROVIDER')):
        yield


@pytest.fixture
def released_frame(prepared,tmp_path):
    prepared.state.clear();prepared.state.update(copy.deepcopy(prepared._test_precard))
    p=Registry(tmp_path/'policies').save(CODE)
    execute(prepared,p);r=released(prepared,p);day=r['day']
    frame=make_frame([r],[],day,p,release(day),[r['race_id']])
    return p,frame


def fake(actions):
    actions=iter(actions)
    def call(store,settings,req,phase):
        assert len(packed(req)) <= 200000
        name,args=next(actions)
        msg={'role':'assistant','tool_calls':[{'id':'call-'+phase,'type':'function',
            'function':{'name':name,'arguments':json.dumps(args)}}]}
        rid=digest([store.state['run_id'],phase,req])
        store.state['llm_trajectory'].append({'phase':phase,'request_id':rid,'request':copy.deepcopy(req),
            'request_body_bytes':len(packed(req)),'logical_request_elapsed_seconds':0})
        store.state['provider_attempts'].append({'phase':phase,'request_id':rid,'status':'complete'})
        store.state['model_outputs'].append({'phase':phase,'request_id':rid,'message':msg,
            'prompt_tokens':1,'completion_tokens':1,'cost_usd':0,'response_model':settings['model']})
        store.persist();return msg
    return call


@pytest.mark.parametrize('rule',['history','training','stats','stopping'])
def test_candidate_behavior_change_validate_save_finish(released_frame,tmp_path,rule):
    current,frame=released_frame
    if rule=='history':candidate=CODE.replace('depth=1','depth=3')
    elif rule=='training':candidate=CODE.replace('["missing_count"] > 0','["missing_count"] > 1')
    elif rule=='stats':candidate=CODE.replace('["same_distance_records"] == 0','["same_distance_records"] <= 1')
    else:candidate=CODE.replace('sdk.stop_horse(h["number"])','sdk.stop_domain("training", h["number"])')
    assert candidate!=CODE and validate_source(candidate)['valid']
    actions=[('inspect_contract',{}),('validate_research_policy',{'source':candidate}),
        ('save_research_policy',{'source':candidate}),('finish',{})]
    chosen,s=construct(tmp_path,SETTINGS,Registry(tmp_path/'policies'),current,frame,release(frame['day']),fake(actions))
    assert chosen['source']==candidate and s['saved'] and lifecycle_checkpoint(s)['passed']
    assert len(s['llm_trajectory'])==4 and s['updater_decision_validation']['valid']
    assert all(r['headroom']>=40000 for r in s['updater_context_requests'])
    assert s['updater_context_source_audit']['current_frame_digest']==frame['frame_digest']


def test_invalid_candidate_rejected_and_explicit_keep_validated(released_frame,tmp_path):
    current,frame=released_frame
    chosen,s=construct(tmp_path,SETTINGS,Registry(tmp_path/'policies'),current,frame,release(frame['day']),
        fake([('inspect_contract',{}),('validate_research_policy',{'source':'import os'}),('finish',{})]))
    assert chosen==current and not s['saved'] and lifecycle_checkpoint(s)['passed']
    assert any('error' in e for e in s['validation_events'])


def test_unchanged_keep_is_not_failed_updater(released_frame,tmp_path):
    current,frame=released_frame
    chosen,s=construct(tmp_path,SETTINGS,Registry(tmp_path/'policies'),current,frame,release(frame['day']),
        fake([('inspect_contract',{}),('finish',{})]))
    assert chosen==current and not s['saved'] and lifecycle_checkpoint(s)['passed']
    assert s['updater_decision_validation']['policy_hash']==current['policy_hash']
    assert s['updater_decision_parsed'] and len(s['updater_unpruned_requests'])==2


def test_unrun_or_context_blocked_day1_does_not_pass():
    assert not lifecycle_checkpoint({})['passed']
    assert not lifecycle_checkpoint({'terminal_reason':'ContextLimit: context_limit','saved':False})['passed']


def test_release_legality_determinism_future_frame_not_opened(released_frame,tmp_path):
    current,frame=released_frame;before=digest(frame)
    (tmp_path/'frame-2099-01-01.json').write_text('INVALID FUTURE FRAME MUST NEVER BE OPENED')
    payload,audit=context(current,frame,release(frame['day']),tmp_path)
    assert (payload,audit)==context(current,frame,release(frame['day']),tmp_path)
    assert digest(frame)==before
    assert payload['current_policy']['source']==current['source']
    assert payload['cumulative_historical_summary']['overall']['race_count']==1
    assert payload['prior_historical_summary']['overall']['race_count']==0
    assert payload['current_day_frame']['domains']['recent_history']['final_depth_distribution']
    assert all(k not in json.dumps(payload) for k in ('evidence_completeness_at_stopping','trigger_states','race_id','scoring_outcome','provenance'))
    with pytest.raises(Rejected):context(current,frame,cutoff(frame['day']),tmp_path)


def test_history_aggregates_are_count_weighted_and_keep_all_domains(released_frame):
    _,frame=released_frame;one=statistics([frame]);two=statistics([frame,copy.deepcopy(frame)])
    assert two['overall']['race_count']==2 and two['overall']['gross']==2*one['overall']['gross']
    assert two['overall']['Top1']==one['overall']['Top1']
    for d in one['domains']:
        assert two['domains'][d]['native_acquisition_operations']==2*one['domains'][d]['native_acquisition_operations']
    assert two['stopping']['domains_per_horse']==one['stopping']['domains_per_horse']


def test_fitter_complete_round_eviction_preserves_anchors_and_audit(released_frame,tmp_path):
    current,frame=released_frame;payload,_=context(current,frame,release(frame['day']),tmp_path)
    messages=[{'role':'system','content':'ESSENTIAL CANONICAL CONTRACT'},
        {'role':'user','content':json.dumps(payload)}]
    for i in range(5):
        messages.extend([{'role':'assistant','tool_calls':[{'id':str(i),'function':{'name':'validate','arguments':'{}'}}]},
            {'role':'tool','tool_call_id':str(i),'content':json.dumps({'complete_result':'x'*75000})}])
    before=copy.deepcopy(messages);req,receipt=fit(SETTINGS,messages,toolset())
    assert messages==before and len(packed(req))<=200000 and receipt['headroom']>=40000
    obs=json.loads(req['messages'][1]['content'])
    for k in ('current_policy','current_day_frame','cumulative_historical_summary','latest_policy_change'):
        assert obs[k]==payload[k]
    assert req['messages'][0]==messages[0] and req['tools']==toolset()
    assert req['messages'][-2:]==messages[-2:]
    assert all(len(m['content'])==len(messages[-1]['content']) for m in req['messages'] if m['role']=='tool')
    assert all(e['after_bytes']<e['before_bytes'] and e['evicted_digest'] for e in receipt['evictions'])
    assert (req,receipt)==fit(SETTINGS,messages,toolset())


def test_fitter_partial_tool_round_not_removed(released_frame,tmp_path):
    current,frame=released_frame;payload,_=context(current,frame,release(frame['day']),tmp_path)
    msgs=[{'role':'system','content':'CONTRACT'},{'role':'user','content':json.dumps(payload)},
        {'role':'assistant','tool_calls':[{'id':'a'},{'id':'b'}]},
        {'role':'tool','tool_call_id':'a','content':'x'*190000}]
    with pytest.raises(ContextLimit):fit(SETTINGS,msgs,toolset())
    assert msgs[-1]['content']=='x'*190000


def test_fitter_never_drops_oversized_current_policy(released_frame,tmp_path):
    current,frame=released_frame;payload,_=context(current,frame,release(frame['day']),tmp_path)
    payload['current_policy']['source']='x'*210000
    with pytest.raises(ContextLimit):fit(SETTINGS,[{'role':'system','content':'CONTRACT'},
        {'role':'user','content':json.dumps(payload)}],toolset())
