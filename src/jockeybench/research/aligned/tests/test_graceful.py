"""A–N: actual services/transport over tiny memory fixtures, zero real API."""
import copy
import json
from dataclasses import replace
from unittest.mock import patch
import pytest
from jockeybench.clock import cutoff, release
from jockeybench.canonical import code_hash, digest
from jockeybench.errors import Rejected
from jockeybench.protocol.metering import PROGRAM_BUDGET_VERSION
from jockeybench.research.adaptive.test_adaptive import source,config,service,active,run
from jockeybench.research.adaptive.domains import tools
from jockeybench.research.test_daily_session import Response
from jockeybench.research.experience.frame import neutral, make_frame
from jockeybench.research.experience.model import FIELD
from jockeybench.research.runner import settle
from jockeybench.research.aligned import runner
from jockeybench.research.aligned.model import AlignedAgent, ContextLimit
from jockeybench.research.aligned.scoring import checkpoint_components, VERSION
from jockeybench.research.aligned.frame import extract
from jockeybench.research.aligned.integrity import IntegrityFailure
from jockeybench.research.aligned.tests.test_aligned import SETTINGS,reply

@pytest.fixture(autouse=True)
def offline_only():
    with patch('socket.socket.connect',side_effect=AssertionError('NO NETWORK')), \
         patch('jockeybench.agents.provider.require_credential',side_effect=AssertionError('NO CREDENTIALS')), \
         patch('jockeybench.agents.provider.time.sleep'):
        yield

def bind(run):
    run.config=replace(run.config,tariff=replace(run.config.tariff,multiplier=.5))
    run.service.config=run.config
    run.source.projection_version=run.config.projection
    run.state['binding'].update(config=run.config.public(),code=code_hash(),
        budget_version=PROGRAM_BUDGET_VERSION,source=run.source.manifest())

def nonuniform(numbers):
    return {'ranking':numbers,'probabilities':{n:(.6 if i==0 else .4/(len(numbers)-1)) for i,n in enumerate(numbers)}}

def execute_fixture(run,mode='direct',repair='nonuniform'):
    bind(run);requests=[];repair_snapshots=[]
    def http(req,timeout):
        body=json.loads(req.data);ob=json.loads(body['messages'][1]['content'])
        requests.append((copy.deepcopy(body),timeout,req.get_header('Idempotency-key')))
        numbers=[r['number'] for r in ob['basic_race_card']['runners']]
        finalization=len(body['tools'])==1
        if finalization:
            repair_snapshots.append(copy.deepcopy([run.state['active_trace'],run.state.get('semantic_provenance'),run.state['ledger']]))
            if repair=='fail':raise OSError('provider unavailable')
            if repair=='retry' and len(repair_snapshots)<3:raise OSError('retry finalization')
            if repair=='invalid':return Response(reply('get_training',{'horse_number':'01'}))
            pred=nonuniform(numbers) if repair in ('nonuniform','retry') else {'ranking':numbers,'probabilities':{n:1/len(numbers) for n in numbers}}
            return Response(reply('submit',pred))
        turn=ob['model_turn']
        if mode=='turn_limit':return Response(reply('get_training',{'horse_number':'03'}))
        if turn==1:return Response(reply('get_recent_history',{'horse_number':'01'}))
        if turn==2:return Response(reply('get_training',{'horse_number':'03'}))
        if mode=='provider':raise OSError('research request exhausted')
        if mode=='runtime':raise RuntimeError('ordinary execution failure')
        if mode=='cost':return Response(reply('get_track_context',{},cost=.749))
        if mode=='uniform':pred={'ranking':numbers,'probabilities':{n:1/len(numbers) for n in numbers}}
        else:pred=nonuniform(numbers)
        return Response(reply('submit',pred))
    with patch('urllib.request.urlopen',side_effect=http):
        got=runner.predict(run,AlignedAgent(SETTINGS),neutral())
    return got,requests,repair_snapshots

def released(run):
    rid=run.state['schedule'][0]['races'][0]
    from jockeybench.ledger import summary
    run.state['prediction_phase_cost']=summary(run.state,run.config)
    run.state.pop('strategy_job',None);run.state.pop('strategy_job_usage',None)
    settle(run,rid,'2024-01-06')
    run.state['scores'][rid]['format_fallback']=run.state['submissions'][rid]['format_fallback']
    return checkpoint_components(run.state)

def test_A_direct_success(run):
    (rid,day),requests,_=execute_fixture(run)
    d=run.state['aligned_delivery'][rid]
    assert d['delivery']=='direct_predictor' and d['bonus_eligible']
    assert not run.state.get('finalization_repair_requests') and len(requests)==3
    assert not run.state['submissions'][rid]['format_fallback']

def test_B_I_repaired_same_evidence_no_tool_or_fee_rerun(run):
    (rid,day),requests,snapshots=execute_fixture(run,'provider','retry')
    d=run.state['aligned_delivery'][rid]
    assert d['delivery']=='repaired_predictor' and d['bonus_eligible']
    assert run.state['finalization_repair_requests']==1 and run.state['query_count']==2
    normal=requests[2][0];repair=requests[-1][0]
    a=json.loads(normal['messages'][1]['content']);b=json.loads(repair['messages'][1]['content'])
    for key in ('basic_race_card','evidence',FIELD,'trajectory','ledger'):assert a[key]==b[key],key
    assert snapshots[0]==snapshots[1]==snapshots[2]
    assert requests[-1][0]==requests[-2][0]==requests[-3][0]
    assert requests[-1][2]==requests[-2][2]==requests[-3][2]
    assert repair['tools']==[tools([r['number'] for r in b['basic_race_card']['runners']])[-1]]

def test_C_uniform_repair_not_predictor(run):
    (rid,_),_,_=execute_fixture(run,'provider','uniform')
    d=run.state['aligned_delivery'][rid]
    assert d['delivery']=='uniform_submit' and not d['bonus_eligible']
    assert not run.state['submissions'][rid]['format_fallback']
    assert released(run)['races'][0]['winner_bonus']==0

@pytest.mark.parametrize('repair',['fail','invalid'])
def test_D_E_G_fallback_lucky_hit_retains_fees(run,repair):
    (rid,_),requests,_=execute_fixture(run,'provider',repair)
    d=run.state['aligned_delivery'][rid];pred=run.state['submissions'][rid]['prediction']
    assert d['delivery']=='execution_fallback' and d['generated_by']=='framework' and not d['bonus_eligible']
    assert pred['ranking']==sorted(pred['ranking']) and all(p==1/len(pred['ranking']) for p in pred['probabilities'].values())
    assert run.state['query_count']==2 and run.state['submissions'][rid]['format_fallback']
    if repair=='invalid':assert run.state['finalization_failure']=='invalid_terminal_action'
    evaluated=released(run);row=evaluated['races'][0]
    assert row['top1_hit'] is True and row['winner_bonus']==0
    assert abs(row['log_reward'])<1e-10 and evaluated['cost']['weighted_research_cost']>0
    assert evaluated['common_Net']<0
    frame_row=extract(run.state,'2024-01-06',rid,release('2024-01-06'))
    assert frame_row['fallback'] and frame_row['validity']=='fallback' and frame_row['queries']==2
    assert frame_row['B_contribution']==0 and frame_row['common_net']<0
    assert frame_row['common_reward']==pytest.approx(evaluated['aligned_reward']) and frame_row['common_reward']<0
    before=len(run.state['llm_trajectory'])
    frame=make_frame([frame_row],[],'2024-01-06',[rid],release('2024-01-06'))
    assert frame['current_day']['overall']['fallback_count']==1
    assert len(run.state['llm_trajectory'])==before  # no synthetic response persisted

def test_F_repaired_bonus_eligible_and_separate(run):
    (rid,_),_,_=execute_fixture(run,'runtime','nonuniform')
    evaluated=released(run);row=evaluated['races'][0]
    assert row['delivery']=='repaired_predictor' and row['top1_hit'] and row['bonus_eligible']
    assert row['winner_bonus']==pytest.approx(100*(1-1/5))

def test_H_twenty_attempts_two_logical_requests_never_second_repair(run):
    (_, _),requests,_=execute_fixture(run,'provider','fail')
    s=run.state
    assert len(s['provider_attempts'])==22  # two successful research tools + 10 + 10
    assert len([t for t in s['llm_trajectory'] if t['phase']=='finalization-1'])==1
    assert len([r for r in requests if len(r[0]['tools'])==1])==10
    assert all(r[1]==3600 and r[0]['max_tokens'] is None for r in requests)

@pytest.mark.parametrize('repair',['nonuniform','fail'])
def test_J_forty_research_turns_then_one_finalization(run,repair):
    (rid,_),requests,_=execute_fixture(run,'turn_limit',repair)
    s=run.state
    assert s['model_turns']==40 and s['query_count']==40
    assert len([t for t in s['llm_trajectory'] if t['phase'].startswith('prediction-')])==40
    assert not any(t['phase']=='prediction-41' for t in s['llm_trajectory'])
    assert len([t for t in s['llm_trajectory'] if t['phase']=='finalization-1'])==1
    assert s['aligned_delivery'][rid]['delivery']==('repaired_predictor' if repair=='nonuniform' else 'execution_fallback')

def test_cost_safety_stop_falls_back_without_raising_ceiling(run):
    (rid,_),requests,_=execute_fixture(run,'cost','nonuniform')
    assert run.state['graceful_trigger']=='cost_safety_stop'
    assert run.state['aligned_delivery'][rid]['delivery']=='execution_fallback'
    assert run.state['funding_stop']['threshold_usd']==.75

@pytest.mark.parametrize('tamper',['race','clock','ledger','provenance','digest'])
def test_L_integrity_failure_never_repairs_or_fallback(run,tamper):
    bind(run)
    def http(req,timeout):
        ob=json.loads(json.loads(req.data)['messages'][1]['content'])
        if ob['model_turn']==1:return Response(reply('get_recent_history',{'horse_number':'01'}))
        if tamper=='race':run.state['active_initial']['card']['race_id']='foreign'
        if tamper=='clock':run.state['clock']=release('2024-01-06')
        if tamper=='ledger':run.state['ledger']['cost_micro']+=1
        if tamper=='provenance':run.state['semantic_provenance'][0]['race_id']='foreign'
        if tamper=='digest':run.state['checkpoint_digest']='corrupt'
        raise RuntimeError('injected')
    with patch('urllib.request.urlopen',side_effect=http),patch.object(AlignedAgent,'finalize') as final:
        with pytest.raises(IntegrityFailure):runner.predict(run,AlignedAgent(SETTINGS),neutral())
    assert not final.called and not run.state['submissions'] and not run.state.get('aligned_delivery')

def test_N_scorer_is_separate_old_uniform_bonus_unchanged(run):
    (_, _),_,_=execute_fixture(run,'uniform')
    evaluated=released(run)
    from jockeybench.research.common_scoring import checkpoint_components as old, VERSION as OLD_VERSION
    legacy=old(run.state)
    assert OLD_VERSION=='common-score-yen-v2-b100-ungated-v1' and VERSION=='common-aligned-delivery-v1'
    assert legacy['races'][0]['common_B_contribution']==80
    assert evaluated['races'][0]['common_B_contribution']==0

def test_invalid_terminal_actions_close_then_one_valid_repair(run):
    bind(run)
    def http(req,timeout):
        body=json.loads(req.data);ob=json.loads(body['messages'][1]['content'])
        numbers=[r['number'] for r in ob['basic_race_card']['runners']]
        if len(body['tools'])==1:return Response(reply('submit',nonuniform(numbers)))
        return Response(reply('bogus',{}))
    with patch('urllib.request.urlopen',side_effect=http):
        rid,day=runner.predict(run,AlignedAgent(SETTINGS),neutral())
    assert run.state['model_turns']==40 and len(run.state['protocol_errors'])==40
    assert run.state['aligned_delivery'][rid]['delivery']=='repaired_predictor'
    assert run.state['finalization_repair_requests']==1 and run.state['query_count']==0

def test_delivery_provenance_and_versioned_score_tampering_rejected(run):
    (rid,_),_,_=execute_fixture(run,'provider','invalid')
    from jockeybench.research.aligned.audit import verify_semantics
    assert verify_semantics(run.state)['aligned_execution_constraints']=='PASS'
    saved=copy.deepcopy(run.state['aligned_delivery'][rid])
    run.state['aligned_delivery'][rid]['bonus_eligible']=True
    with pytest.raises(Rejected,match='bonus provenance'):verify_semantics(run.state)
    run.state['aligned_delivery'][rid]=saved
    evaluated=released(run)
    run.state['aligned_scores']=evaluated
    run.state['aligned_scores']['races'][0]['winner_bonus']=80
    with pytest.raises(Rejected,match='score integrity'):verify_semantics(run.state)

@pytest.mark.parametrize('fits',[True,False])
def test_K_real_size_guard_same_evidence_submit_only_or_skip(run,fits):
    # Synthetic trusted-backend stress response. It is charged, audited,
    # projected and replayed through the actual service; never injected into
    # knowledge/provenance directly.
    original_card=run.source.card
    def large_card(*args,**kwargs):
        value=original_card(*args,**kwargs)
        value['facts']['course_layout']='x'* (254000 if fits else 263000)
        return value
    bind(run);sizes=[];observations=[]
    from jockeybench.research.aligned import model
    original_guard=model.guard_body
    def guard(body):
        sizes.append((len(body['tools']),len(__import__('jockeybench.canonical',fromlist=['packed']).packed(body))))
        observations.append(json.loads(body['messages'][1]['content']))
        return original_guard(body)
    def http(req,timeout):
        body=json.loads(req.data);ob=json.loads(body['messages'][1]['content'])
        if len(body['tools'])!=1:return Response(reply('get_track_context',{}))
        numbers=[r['number'] for r in ob['basic_race_card']['runners']]
        return Response(reply('submit',nonuniform(numbers)))
    with patch.object(run.source,'card',side_effect=large_card),patch('urllib.request.urlopen',side_effect=http) as http_mock,patch.object(model,'guard_body',side_effect=guard):
        rid,day=runner.predict(run,AlignedAgent(SETTINGS),neutral())
    assert run.state['graceful_trigger']=='context_limit'
    normal=next(o for (n,size),o in zip(sizes,observations) if n==5 and size>262144)
    final=next(o for (n,size),o in zip(sizes,observations) if n==1)
    for key in ('basic_race_card','evidence',FIELD,'trajectory'):assert normal[key]==final[key]
    final_size=next(size for n,size in sizes if n==1)
    assert (final_size<=262144)==fits
    assert run.state['aligned_delivery'][rid]['delivery']==('repaired_predictor' if fits else 'execution_fallback')
    assert http_mock.call_count==(2 if fits else 1)

@pytest.mark.parametrize('arm',['guided_experience','frozen_neutral_history'])
@pytest.mark.parametrize('hard_failure',[False,True])
def test_M_actual_coordinator_mixed_day_then_next_day(run,arm,request,hard_failure):
    from pathlib import Path
    from dataclasses import asdict
    from types import SimpleNamespace
    from contextlib import ExitStack
    import tempfile
    from jockeybench.research.runner import PROJECT,ActiveService
    from jockeybench.research.aligned.prepare import templates
    from jockeybench.audit.replay import replay as replay_economic
    # Extend only the tiny in-memory fixture to have three races on day one.
    run.source.con.execute("INSERT INTO races SELECT 'raw9',day,pre_at,off_at,review_at,context FROM races WHERE id='raw2'")
    run.source.con.execute("INSERT INTO entries SELECT 'raw9',number,horse_id,jockey_id,trainer_id,pre,history,settlement,review_at FROM entries WHERE race_id='raw2'")
    run.source.con.execute('INSERT INTO expanded_race_ids VALUES(?,?)',('raw9',run.source.token('race','raw9')))
    bind(run);initial=copy.deepcopy(run.state);states={}
    ids=[run.source.token('race',r) for r in ('raw2','raw3','raw9','raw4')]
    schedule=[{'day':'2024-01-06','races':ids[:3]},{'day':'2024-01-07','races':[ids[3]]}]
    modes=dict(zip(ids,('direct','repaired','fallback','direct')))
    directory=tempfile.TemporaryDirectory(prefix='A2-graceful-offline-',dir=PROJECT/'active_research_outputs')
    request.addfinalizer(directory.cleanup);out=Path(directory.name)
    cfg=replace(run.config,smoke_days=1,races_per_day=1,managed_root=str(out/'A2-runs'))
    source=run.source
    class MemoryRun:
        def __init__(self,config,ident,agent):
            self.config=config;self.agent=agent;self.source=source;self.executor=SimpleNamespace()
            self.state=states.setdefault(ident,copy.deepcopy(initial));self.state['run_id']=ident
            self.state['binding']['config']=config.public()
            self.storage=SimpleNamespace()
        def persist(self):pass
        def close(self):pass
        def preflight(self):pass
    seen=[]
    def http(req,timeout):
        body=json.loads(req.data);ob=json.loads(body['messages'][1]['content']);rid=ob['basic_race_card']['race_id']
        numbers=[r['number'] for r in ob['basic_race_card']['runners']]
        seen.append((rid,ob[FIELD]))
        repair=len(body['tools'])==1
        if hard_failure and rid==ids[1]:
            failed=next(s for s in states.values() if s['schedule'][0]['races'][0]==rid)
            failed['active_initial']['card']['race_id']='foreign'
            raise RuntimeError('injected identity corruption')
        if modes[rid]=='fallback':raise OSError('offline provider failure')
        if modes[rid]=='repaired' and not repair:raise OSError('offline research failure')
        return Response(reply('submit',nonuniform(numbers)))
    (out/'A2-config.json').write_text(json.dumps(asdict(cfg)))
    (out/'A2-provider.json').write_text(json.dumps(SETTINGS))
    manifest,_=templates(arm);manifest.update(schedule=schedule,maximum_real_races=4,template_only=False)
    (out/'A2-manifest.json').write_text(json.dumps(manifest))
    with ExitStack() as stack:
        stack.enter_context(patch('urllib.request.urlopen',side_effect=http))
        stack.enter_context(patch.object(runner,'Run',MemoryRun))
        stack.enter_context(patch.object(runner,'credential',lambda *a:None))
        stack.enter_context(patch.object(runner,'replay',lambda cfg,s:replay_economic(cfg,s,source=source)))
        stack.enter_context(patch.object(runner,'result_report',lambda run,*a:{'run_id':run.state['run_id']}))
        stack.enter_context(patch.object(runner,'scan_no_credential',lambda *a:{}))
        runner.execute(out)
    status=json.loads((out/'A2-execution-status.json').read_text())
    if hard_failure:
        assert status['attempted']==2 and not status['completed_days']
        assert status['errors'][0]['failure_code']=='hard_integrity_failure'
        assert not list(out.glob('A2-experience-frame-*.json'))
        assert not any(e['kind']=='release_gate' for e in json.loads((out/'A2-events.json').read_text()))
        report=json.loads((out/'A2-aligned-report.json').read_text())
        assert report['hard_failure_count']==1 and report['delivery_counts']['no_delivery']==1
        assert report['resources']['sealed']==1 and report['resources']['released']==0
        return
    assert not status['errors'],status['errors']
    assert status['attempted']==4 and status['completed_days']==['2024-01-06','2024-01-07']
    frame=json.loads((out/'A2-experience-frame-2024-01-06.json').read_text())
    assert frame['current_day']['overall']['race_count']==3
    assert frame['current_day']['overall']['fallback_count']==1
    next_frame=next(f for rid,f in seen if rid==ids[3])
    assert next_frame==(frame if arm=='guided_experience' else neutral())
    events=json.loads((out/'A2-events.json').read_text())
    for batch in schedule:
        es=[e for e in events if e.get('day')==batch['day']]
        gate=next(e for e in es if e['kind']=='release_gate')
        assert gate['clock']==release(batch['day'])
        assert all(e['sequence']<gate['sequence'] for e in es if e['kind']=='sealed')
        assert all(e['sequence']>gate['sequence'] for e in es if e['kind']=='result_released')
    report=json.loads((out/'A2-aligned-report.json').read_text())
    assert report['delivery_counts']['direct_predictor']==2
    assert report['delivery_counts']['repaired_predictor']==1
    assert report['delivery_counts']['execution_fallback']==1
    assert report['resources']['released']==4 and report['hard_failure_count']==0
