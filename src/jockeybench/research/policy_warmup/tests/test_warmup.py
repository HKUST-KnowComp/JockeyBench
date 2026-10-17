import copy,json
from pathlib import Path
from unittest.mock import Mock
import pytest
from jockeybench.research.adaptive.test_adaptive import source
from jockeybench.research.policy_amortized.policy_contract import validate_source,PolicyInvalid
from jockeybench.research.policy_amortized.policy_builder import Registry,matched_pair
from jockeybench.research.policy_amortized import lifecycle as v1
from jockeybench.research.policy_warmup import archive,builder,lifecycle

CODE='def collect_evidence(sdk, context):\n    return sdk.package()\n'
ROOT=Path(__file__).resolve().parents[3]

@pytest.fixture
def profile(tmp_path):
    value=json.loads((ROOT/'active_research_outputs/2026-10-02-Policy-Amortized-v2-Warmup-Offline/warmup-profile.json').read_text())
    value['source']=str(tmp_path/'nonexistent-fixture.sqlite')
    Path(value['source']).write_text('synthetic isolation canary; not a database')
    value['source_manifest']={'sha256':'in-memory-fixture','receipt_digest':'fixture'}
    return value

@pytest.fixture
def mod(profile):return archive.load(profile)

@pytest.fixture
def support(source,mod):
    source.__class__=archive.support_types(mod)
    archive.historical_views(source.con)
    return source

@pytest.mark.parametrize('raw',['raw2','raw4','raw6','raw7'])
def test_01_formal_cards_and_results_denied(support,mod,raw):
    rid=support.token('race',raw)
    with pytest.raises(mod('common').Rejected):support.card(rid,archive.CLOCK,archive.ORIGIN)
    with pytest.raises(mod('common').Rejected):support.objective_result(rid,archive.CLOCK,archive.ORIGIN,archive.CLOCK)

def test_03_fixed_clock(support,mod):
    rid=support.token('race','raw0')
    assert mod('common').cutoff(archive.HORIZON)==archive.CLOCK
    with pytest.raises(mod('common').Rejected):support.card(rid,archive.CLOCK+1,archive.ORIGIN)

def test_04_universe_full_available_not_sampled(support):
    value,_=support.history_page(archive.CLOCK,archive.ORIGIN,{},'',8)
    assert len(value['examples'])==2
    assert {x['card']['race_id'] for x in value['examples']}=={support.token('race','raw0'),support.token('race','raw1')}
    assert all(x['labels'] for x in value['examples'])

def test_05_released_and_unreleased_historical_labels(support):
    rid=support.token('race','raw0')
    assert support.objective_result(rid,archive.CLOCK,archive.ORIGIN,archive.CLOCK)['records'][0]['values']['finish']==1
    support.con.execute('PRAGMA query_only=OFF')
    support.con.execute('UPDATE main.races SET review_at=? WHERE id=?',(archive.CLOCK+1,'raw0'))
    support.con.execute('PRAGMA query_only=ON')
    assert support.objective_result(rid,archive.CLOCK,archive.ORIGIN,archive.CLOCK) is None
    value,_=support.history_page(archive.CLOCK,archive.ORIGIN,{},'',8)
    assert len(value['examples'])==1

@pytest.mark.parametrize('tool',['results','submit','compute','llm','history','save_strategy'])
def test_06_07_08_runtime_stays_acquisition_only(tool):
    with pytest.raises(PolicyInvalid):validate_source(CODE.replace('return sdk.package()',f'sdk.call("{tool}")\n    return sdk.package()'))

def test_09_10_identical_matched_initial(tmp_path):
    v=Registry(tmp_path/'policies').save(CODE)
    assert validate_source(CODE)==validate_source(CODE)
    pair=matched_pair(v,tmp_path/'pair');assert len(set(pair.values()))==1

def test_13_14_15_16_no_alternate_runtime_implementation():
    assert lifecycle.v1 is v1
    assert lifecycle.construct.__module__.endswith('policy_warmup.lifecycle')
    # The bound execution changes only initial construction and version verification.
    assert v1.revise.__module__=='jockeybench.research.policy_amortized.policy_builder'
    assert v1.predict.__module__=='jockeybench.research.policy_amortized.predictor'
    assert v1.make_frame.__module__=='jockeybench.research.policy_amortized.frame'

def test_20_schedule_anchor_does_not_query_formal_data(support):
    support.con.set_trace_callback(lambda sql:pytest.fail('Schedule must not query the database: '+sql))
    assert support.schedule()==[{'day':'2024-01-06','races':[]}]
    support.con.set_trace_callback(None)

def test_broad_archived_index_scan_is_historical_only(support,mod):
    assert [r[0] for r in support.con.execute('SELECT DISTINCT day FROM races ORDER BY day')]==['2023-12-01','2023-12-02']
    assert support.con.execute('SELECT COUNT(*) FROM entries').fetchone()[0]==10
    assert support.con.execute('PRAGMA query_only').fetchone()[0]==1
    index=mod('connections').ConnectionsIndex(support)
    assert all(event[1]<'2024-01-06' for events in index.rows.values() for event in events)

def prepare_build(monkeypatch,support,profile):
    monkeypatch.setattr(builder,'support_types',lambda mod:lambda *args:support)
    support.projection_version='objective-v4'
    profile=copy.deepcopy(profile);profile['config']['projection']='objective-v4'
    return profile

def caller(actions):
    def call(service,settings,body,phase):
        index=len(service.state.get('llm_trajectory',[]));name,args=actions(index,service)
        msg={'role':'assistant','content':'','tool_calls':[{'id':str(index),'type':'function','function':{'name':name,'arguments':json.dumps(args)}}]}
        service.state.setdefault('llm_trajectory',[]).append({'phase':phase,'request':copy.deepcopy(body),'logical_request_elapsed_seconds':1})
        service.state.setdefault('model_outputs',[]).append({'request_id':str(index),'cost_usd':.001,'prompt_tokens':10,'completion_tokens':5,'message':msg})
        service.state.setdefault('provider_attempts',[]).append({'status':'complete','request_id':str(index)})
        return msg
    return call

def test_02_08_11_12_native_warmup_compute_accounting_and_no_artifact_access(monkeypatch,support,profile,tmp_path):
    profile=prepare_build(monkeypatch,support,profile)
    forbidden=tmp_path/'cold-start-Frame.json';forbidden.write_text('DO_NOT_READ_COLD_START')
    steps=[('inspect_contract',{}),('results',{'race_id':support.token('race','raw0')}),
           ('compute',{'code':'result = sum(range(100))'}),
           ('compute',{'code':f'result = open({str(forbidden)!r}).read()'}),
           ('validate_research_policy',{'source':CODE}),('save_research_policy',{'source':CODE}),('finish',{})]
    p=builder.build(tmp_path/'out',profile,caller(lambda i,s:steps[i]))
    assert p['parent_policy_hash'] is None
    cost=json.loads((tmp_path/'out/warmup-accounting.json').read_text())
    assert cost['logical_requests']==7 and cost['provider_attempts']==7
    assert cost['api_cost_usd_estimate']==pytest.approx(.007)
    assert cost['weighted_data_tool_cost']>0 and cost['weighted_compute_cost']>0 and cost['compute_settled']
    assert cost['warmup_weighted_cost_per_324']==cost['weighted_ledger_cost']/324
    import gzip
    with gzip.open(tmp_path/'out/warmup-runs/historical-support/checkpoint.json.gz','rt') as f:state=json.load(f)
    assert any(x['tool']=='compute' and x['status']=='rejected' for x in state['audit'])
    assert 'DO_NOT_READ_COLD_START' not in json.dumps(state)
    assert 'Research-Program Experience Frame' not in json.dumps(state['llm_trajectory'])
    assert p['model_usage']['logical_requests']==6
    assert p['model_usage']==p['model_usage_at_save']
    assert p['model_usage_scope']=='through_save_request'
    assert cost['builder_session_usage']['logical_requests']==7
    assert cost['builder_session_usage']['finish_received']
    session=json.loads((tmp_path/'out/warmup-session.json').read_text())
    assert session['builder_session_usage']==cost['builder_session_usage']
    assert state['usage']['model_calls']==7

def test_bounded_repair_only_after_normal_35(monkeypatch,support,profile,tmp_path):
    profile=prepare_build(monkeypatch,support,profile)
    actions=lambda i,s:('inspect_contract',{}) if i<35 else [('validate_research_policy',{'source':CODE}),('save_research_policy',{'source':CODE}),('finish',{})][i-35]
    builder.build(tmp_path/'out',profile,caller(actions))
    session=json.loads((tmp_path/'out/warmup-session.json').read_text())
    assert len(session['llm_trajectory'])==38
    assert 'repair reserve' in session['llm_trajectory'][35]['request']['messages'][-1]['content']

def test_no_manual_initial_fallback(monkeypatch,support,profile,tmp_path):
    profile=prepare_build(monkeypatch,support,profile)
    with pytest.raises(PolicyInvalid,match='Warmup failed'):
        builder.build(tmp_path/'out',profile,caller(lambda i,s:('inspect_contract',{})))
    assert not list((tmp_path/'out/policies').glob('*.py'))
    assert json.loads((tmp_path/'out/warmup-accounting.json').read_text())['logical_requests']==40

def test_hard_context_limit_no_pruning(mod):
    from jockeybench.research.policy_amortized.transport import request
    from jockeybench.research.aligned.model import ContextLimit
    service=Mock();service.state={}
    with pytest.raises(ContextLimit):request(service,{}, {'messages':['x'*262145]},'warmup-1')
    assert service.state['request_sizes'][0]['sent'] is False

def test_archive_fingerprint_rejects_wrong_clock(profile):
    profile['clock']+=1
    with pytest.raises(ValueError):archive.load(profile)
