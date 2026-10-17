import copy
import json
import math
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import pytest
from jockeybench.agents import provider
from jockeybench.errors import Pending, Rejected, ProviderCostLimit
from jockeybench.canonical import packed
from jockeybench.clock import cutoff, release
from jockeybench.agents.compatible import fit_context
from jockeybench.research.adaptive.test_adaptive import source, config, service, active, run, message
from jockeybench.research.adaptive.domains import tools, fetch, trajectory
from jockeybench.research.adaptive.runner import predict as original_predict, SafetyLimit
from jockeybench.research.experience.model import ExperienceAgent, PROMPT as ORIGINAL_PROMPT, FIELD
from jockeybench.research.experience.frame import neutral, extract, make_frame
from jockeybench.research.experience.test_frame import record
from jockeybench.research.runner import ActiveService, settle
from jockeybench.research.test_daily_session import Response
from jockeybench.research.aligned import runner
from jockeybench.research.aligned.model import AlignedAgent, PROMPT, OLD_CONSTRAINT, NEW_CONSTRAINT, guard_body, ContextLimit
from jockeybench.research.aligned.audit import verify_semantics, classify_failure
from jockeybench.research.aligned.specification import PROFILE, execution_hash
from jockeybench.research.aligned.prepare import templates
from jockeybench.research.aligned.report import resources

@pytest.fixture(autouse=True)
def forbid_real_network_and_credentials():
    with patch('socket.socket.connect',side_effect=AssertionError('REAL NETWORK FORBIDDEN')), \
         patch('jockeybench.agents.provider.require_credential',side_effect=AssertionError('CREDENTIAL ACCESS FORBIDDEN')):
        yield

SETTINGS = {'model':'deepseek-ai/DeepSeek-V4-Flash-0731',
 'base_url':'https://api.deepinfra.com/v1/openai','api_key_env':'DEEPINFRA_API_KEY',
 'timeout_seconds':3600,'output_token_field':'max_tokens','temperature':0,'seed':0,
 'input_usd_per_million':.06,'output_usd_per_million':.18,'retry_attempts':10,
 'retry_base_seconds':2,'retry_max_seconds':300,'reservation_output_tokens':4096,
 'max_estimated_cost_usd':.75,'reasoning_effort':'high'}

def svc():
    return SimpleNamespace(state={'run_id':'fixture','phase':'predict','index':0,
        'usage':{},'clock':cutoff('2024-01-06'),'memory_before':neutral()},persist=lambda:None)

def obs():
    return {'mode':'prediction','research_policy':neutral(),'evidence':{},
            'model_turn':1,'safety_remaining_wall_seconds':math.inf}

def reply(name='get_track_context',args=None,cost=.001):
    native=message(name,args or {})
    native['tool_calls'][0].update(id='offline-native',type='function')
    return {'model':SETTINGS['model'],'choices':[{'finish_reason':'tool_calls',
        'message':{'role':'assistant','content':'',**native}}],
        'usage':{'prompt_tokens':10,'completion_tokens':10,'estimated_cost':cost}}

def script(req,timeout):
    body=json.loads(req.data);ob=json.loads(body['messages'][1]['content'])
    numbers=[r['number'] for r in ob['basic_race_card']['runners']]
    actions=[('get_recent_history',{'horse_number':'01'}),
             ('get_training',{'horse_number':'03'}),
             ('get_condition_stats',{'horse_number':'01'}),('get_track_context',{}),
             ('submit',{'ranking':numbers,'probabilities':{n:1/len(numbers) for n in numbers}})]
    name,args=actions[ob['model_turn']-1]
    return Response(reply(name,args))

@pytest.mark.parametrize('history',['neutral','generated'])
def test_02_03_semantics_and_prompt_equivalence(run,history):
    from dataclasses import replace
    run.config=replace(run.config,tariff=replace(run.config.tariff,multiplier=.5))
    run.service.config=run.config
    run.state['binding']['config']=run.config.public()
    initial=copy.deepcopy(run.state)
    twin=SimpleNamespace(state=initial,source=run.source,config=run.config,persist=lambda:None)
    twin.service=ActiveService(run.source,run.config,initial,twin.persist)
    legacy_settings=dict(SETTINGS,timeout_seconds=180,retry_attempts=2,retry_base_seconds=1,retry_max_seconds=1)
    memory=neutral() if history=='neutral' else make_frame(
        [record('previous','2024-01-05',reward=2,seq=['track_context'])],[],
        '2024-01-05',['previous'],release('2024-01-05'))
    with patch('urllib.request.urlopen',side_effect=script),patch('time.monotonic',return_value=100):
        original_predict(run,ExperienceAgent(legacy_settings,1000),memory)
        runner.research_predict(twin,AlignedAgent(SETTINGS),memory)
    keys=['active_initial','active_trace','semantic_provenance','submissions','ledger',
          'memory_before','query_count','repeated_actions','protocol_errors']
    for key in keys: assert run.state[key]==twin.state[key],key
    assert trajectory(run.state)==trajectory(twin.state)
    for old,new in zip(run.state['llm_trajectory'],twin.state['llm_trajectory']):
        a=old['request'];b=new['request']
        assert a['messages'][0]['content'].replace(OLD_CONSTRAINT,NEW_CONSTRAINT)==b['messages'][0]['content']
        oa=json.loads(a['messages'][1]['content']);ob=json.loads(b['messages'][1]['content'])
        assert ob['safety_remaining_wall_seconds'] is None
        oa['safety_remaining_wall_seconds']=None
        assert oa==ob
        assert a['tools']==b['tools'] and a['parallel_tool_calls']==b['parallel_tool_calls']==False
    assert verify_semantics(twin.state)['aligned_execution_constraints']=='PASS'
    for candidate in (run,twin):
        from jockeybench.ledger import summary
        candidate.state['prediction_phase_cost']=summary(candidate.state,candidate.config)
        candidate.state.pop('strategy_job');candidate.state.pop('strategy_job_usage')
        rid=candidate.state['schedule'][0]['races'][0]
        settle(candidate,rid,'2024-01-06')
    assert run.state['scores']==twin.state['scores']
    rid=run.state['schedule'][0]['races'][0]
    a=extract(run.state,'2024-01-06',rid,release('2024-01-06'))
    b=extract(twin.state,'2024-01-06',rid,release('2024-01-06'))
    assert a==b
    assert make_frame([a],[],'2024-01-06',[rid],release('2024-01-06'))==make_frame([b],[],'2024-01-06',[rid],release('2024-01-06'))

def test_04_05_uncapped_output_fixed_timeout():
    sent=[]
    def http(req,timeout):sent.append((json.loads(req.data),timeout));return Response(reply())
    agent=AlignedAgent(SETTINGS)
    with patch('urllib.request.urlopen',side_effect=http):
        for clock in (0,1900,10000):
            with patch('time.monotonic',return_value=clock):agent.call(svc(),obs(),tools(['01']),'prediction-1')
    assert all(body['max_tokens'] is None and timeout==3600 for body,timeout in sent)

def test_06_transport_retries_same_bytes_identity_no_tool_rerun(run):
    captured=[];failures=[True,True,False]
    def http(req,timeout):
        captured.append((req.data,req.get_header('Idempotency-key'),timeout))
        if failures and failures.pop(0):raise OSError('offline failure')
        return script(req,timeout)
    with patch('urllib.request.urlopen',side_effect=http),patch('jockeybench.agents.provider.time.sleep') as sleep:
        runner.research_predict(run,AlignedAgent(SETTINGS),neutral())
    assert captured[0]==captured[1]==captured[2]
    assert sleep.call_args_list[0].args==(2,) and sleep.call_args_list[1].args==(4,)
    assert run.state['query_count']==4
    assert len(run.state['semantic_provenance'])==4
    assert len(run.state['llm_trajectory'])==5 and len(run.state['provider_attempts'])==7

def test_07_exhaustion_no_outer_recovery(run):
    with patch('urllib.request.urlopen',side_effect=OSError('offline')) as http,patch('jockeybench.agents.provider.time.sleep') as sleep:
        with pytest.raises(Pending):runner.research_predict(run,AlignedAgent(SETTINGS),neutral())
    assert http.call_count==10
    assert [c.args[0] for c in sleep.call_args_list]==[2,4,8,16,32,64,128,256,300]
    assert run.state['query_count']==0 and len(run.state['llm_trajectory'])==1
    assert not run.state['submissions']

def test_08_no_1800_second_deadline(run):
    clocks=iter(range(0,1000000,2000))
    with patch('time.monotonic',side_effect=lambda:next(clocks)),patch('urllib.request.urlopen',side_effect=script):
        runner.research_predict(run,AlignedAgent(SETTINGS),neutral())
    assert run.state['submissions'] and run.state['aligned_race_elapsed_seconds']>1800
    assert all(json.loads(t['request']['messages'][1]['content'])['safety_remaining_wall_seconds'] is None
               for t in run.state['llm_trajectory'])

@pytest.mark.parametrize('size',[262143,262144,262145])
def test_09_exact_body_boundary_matches_modeling(size):
    body={'messages':[{'role':'user','content':''}]}
    body['messages'][0]['content']='a'*(size-len(packed(body)))
    assert len(packed(body))==size
    original=copy.deepcopy(body)
    if size<=262144:
        assert guard_body(body)==size
        fit_context(body,{}, {},262144)
    else:
        with pytest.raises(ContextLimit):guard_body(body)
        with pytest.raises(Exception):fit_context(body,{}, {},262144)
    assert body==original

def test_context_limit_prevents_network_and_preserves_evidence():
    ob=obs();ob['evidence']={'complete':'x'*262144};old=copy.deepcopy(ob)
    with patch('urllib.request.urlopen') as http:
        with pytest.raises(ContextLimit):AlignedAgent(SETTINGS).call(svc(),ob,tools(['01']),'prediction-1')
    assert not http.called and ob==old

def test_10_11_independent_race_budget_cumulative_over_three():
    states=[]
    with patch('urllib.request.urlopen',return_value=Response(reply(cost=.7))):
        agent=AlignedAgent(SETTINGS)
        for i in range(5):
            s=svc();s.state['run_id']=f'race-{i}'
            agent.call(s,obs(),tools(['01']),'prediction-1')
            exposure,unknown=provider.cost_exposure(s.state)
            assert exposure==.7 and not unknown and not s.state.get('funding_stop')
            states.append(s.state)
    assert sum(provider.cost_exposure(s)[0] for s in states)>3
    assert resources(states)['total_estimated_API_cost_usd']==3.5
    assert len(states)==5 and agent.settings['max_estimated_cost_usd']==.75

def test_per_race_funding_still_stops_before_next_attempt():
    s=svc()
    with patch('urllib.request.urlopen',return_value=Response(reply(cost=.749))) as http:
        AlignedAgent(SETTINGS).call(s,obs(),tools(['01']),'prediction-1')
        with pytest.raises(ProviderCostLimit):AlignedAgent(SETTINGS).call(s,obs(),tools(['01']),'prediction-2')
    assert http.call_count==1 and s.state['funding_stop']['threshold_usd']==.75

def test_12_arm_profiles_and_provider_settings_identical():
    a,pa=templates('guided_experience');b,pb=templates('frozen_neutral_history')
    assert pa==pb and a['execution_profile_hash']==b['execution_profile_hash']==execution_hash()
    a.pop('scientific_arm');b.pop('scientific_arm');assert a==b
    assert runner.lifecycle.__module__=='jockeybench.research.experience.runner'
    assert runner.original_predict is original_predict

@pytest.mark.parametrize('exception,expected',[(ContextLimit('context_limit'),'context_limit'),
    (Pending('offline'),'provider_failure'),(ProviderCostLimit('offline'),'cost_safety_stop'),
    (SafetyLimit('max_model_turns'),'turn_limit')])
def test_failure_categories(exception,expected):assert classify_failure(exception,{})==expected

def test_40_turns_invalid_actions_fail_stop(run):
    with patch('urllib.request.urlopen',return_value=Response(reply('bogus'))):
        with pytest.raises(SafetyLimit,match='max_model_turns'):
            runner.research_predict(run,AlignedAgent(SETTINGS),neutral())
    assert run.state['model_turns']==40 and not run.state['submissions']
    assert len(run.state['protocol_errors'])==40
    assert classify_failure(SafetyLimit('max_model_turns'),run.state)=='invalid_action'

def test_unknown_usage_is_not_silently_priced_at_zero():
    s=svc();result=reply();result.pop('usage')
    with patch('urllib.request.urlopen',return_value=Response(result)):
        AlignedAgent(SETTINGS).call(s,obs(),tools(['01']),'prediction-1')
    assert s.state['model_outputs'][0]['cost_usd'] is None
    assert provider.cost_exposure(s.state)[0]>0
    assert resources([s.state])['unknown_usage_outputs']==1

@pytest.mark.parametrize('arm',['guided_experience','frozen_neutral_history'])
@pytest.mark.parametrize('failure',[False,True])
def test_actual_aligned_coordinator_daily_release_and_arm_input(tmp_path,arm,request,failure):
    from contextlib import ExitStack
    from dataclasses import asdict
    from jockeybench.config import Config, Tariff
    from jockeybench.research.runner import PROJECT
    import tempfile
    scratch=tempfile.TemporaryDirectory(prefix='A2-aligned-offline-',dir=PROJECT/'active_research_outputs')
    request.addfinalizer(scratch.cleanup)
    tmp_path=Path(scratch.name)
    from jockeybench.research.experience.frame import PROTOCOL, SCHEMA
    schedule=[{'day':'2024-01-06','races':['a','b']},{'day':'2024-01-07','races':['c']}]
    days={r:b['day'] for b in schedule for r in b['races']}
    states={};received=[]
    class FakeRun:
        def __init__(self,config,ident,agent):
            self.config=config;self.agent=agent
            self.state=states.setdefault(ident,{'run_id':ident,'phase':'predict','index':0,
                'completed':False,'scores':{},'submissions':{},'release_ledger':{},
                'usage':{},'model_outputs':[],'provider_attempts':[],'active_trace':[],
                'query_count':0,'binding':{}})
            self.source=SimpleNamespace(raw_race=lambda r:r,con=SimpleNamespace(
                execute=lambda q,args:SimpleNamespace(fetchone=lambda:{'day':days[args[0]]})))
            self.executor=SimpleNamespace()
        def persist(self):pass
        def close(self):pass
        def preflight(self):pass
    class FakeService:
        def __init__(self,source,config,state,persist):self.state=state;self.persist=persist
        def call(self,name,args):
            assert name=='results' and self.state['release_ledger'][args['race_id']]==self.state['clock']
            return {'_cost':{'total_micro':0}}
    def pred(run,agent,memory):
        s=run.state;s['memory_before']=copy.deepcopy(memory)
        rid=s['schedule'][0]['races'][0];day=s['schedule'][0]['day']
        ob=obs();ob.update(research_policy=memory,basic_race_card={'race_id':rid})
        agent.call(run.service,ob,tools(['01']),'prediction-1')
        received.append((day,rid,json.loads(s['llm_trajectory'][0]['request']['messages'][1]['content'])[FIELD]))
        s['submissions'][rid]={'prediction':{'ranking':['01'],'probabilities':{'01':1}},'format_fallback':False}
        s['aligned_race_elapsed_seconds']=2000
        return rid,day
    def settled(run,rid,day):
        run.state.update(completed=True,clock=release(day))
        run.state['scores'][rid]={};run.state['release_ledger'][rid]=release(day)
    def extracted(s,day,rid,clock):
        assert clock==release(day) and s['completed']
        return record(rid,day,reward=1,seq=['track_context'])
    cfg=Config(protocol='economic-v1',arm='A',projection='objective-v5',
        smoke_days=1,races_per_day=1,tariff=Tariff(version='score-yen-v2',multiplier=.5),
        source=str(PROJECT/'unused-synthetic.sqlite'),python=str(PROJECT/'unused-python'),
        managed_root=str(tmp_path/'A2-runs'))
    (tmp_path/'A2-config.json').write_text(json.dumps(asdict(cfg)))
    (tmp_path/'A2-provider.json').write_text(json.dumps(SETTINGS))
    m,_=templates(arm);m.update(schedule=schedule,maximum_real_races=3,template_only=False)
    (tmp_path/'A2-manifest.json').write_text(json.dumps(m))
    with ExitStack() as stack:
        http=stack.enter_context(patch('urllib.request.urlopen',side_effect=OSError('offline') if failure else None,
                                      return_value=Response(reply())))
        sleep=stack.enter_context(patch('jockeybench.agents.provider.time.sleep'))
        for name,value in {'Run':FakeRun,'ActiveService':FakeService,'predict':pred,
            'settle':settled,'extract':extracted,'credential':lambda *a:None,
            'checkpoint_components':lambda *a:{'races':[],'cost':{'weighted_research_cost':0}},
            'summary':lambda *a:{},'replay':lambda *a:{'offline':True},
            'verify_semantics':lambda *a:{'offline':True},
            'result_report':lambda r,*a:{'run_id':r.state['run_id']},
            'scan_no_credential':lambda *a:{'offline':True}}.items():
            stack.enter_context(patch.object(runner,name,value))
        runner.execute(tmp_path)
    status=json.loads((tmp_path/'A2-execution-status.json').read_text())
    if failure:
        assert http.call_count==10 and sleep.call_count==9
        assert status['attempted']==1 and not status['completed_days']
        assert status['errors'][0]['failure_code']=='provider_failure'
        assert not received and not list(tmp_path.glob('A2-experience-frame-*.json'))
        report=json.loads((tmp_path/'A2-aligned-report.json').read_text())
        assert report['failure_counts']['provider_failure']==1
        assert report['resources']['attempted']==1 and report['resources']['sealed']==0
        return
    assert status['attempted']==3 and status['completed_days']==['2024-01-06','2024-01-07'] and not status['errors']
    assert received[0][2]==received[1][2]==neutral()
    generated=json.loads((tmp_path/'A2-experience-frame-2024-01-06.json').read_text())
    assert generated!=neutral()
    assert received[2][2]==(generated if arm=='guided_experience' else neutral())
    events=json.loads((tmp_path/'A2-events.json').read_text())
    for batch in schedule:
        es=[e for e in events if e.get('day')==batch['day']]
        gate=next(e['sequence'] for e in es if e['kind']=='release_gate')
        assert all(e['sequence']<gate for e in es if e['kind']=='sealed')
        assert all(e['sequence']>gate for e in es if e['kind']=='result_released')
    report=json.loads((tmp_path/'A2-aligned-report.json').read_text())
    assert report['resources']['attempted']==report['resources']['released']==3
    assert report['trajectory_provider_budget_match'] is False

