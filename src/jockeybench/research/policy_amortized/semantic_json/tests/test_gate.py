import copy
import gzip
import json
import sys
import pytest
if sys.platform!='darwin':pytest.skip('Native runtime imports; run on Mini',allow_module_level=True)
from jockeybench.canonical import digest
from jockeybench.research.aligned.integrity import IntegrityFailure
from jockeybench.research.policy_amortized.semantic_json import gate, lifecycle, supervisor


@pytest.fixture
def metrics():
    rows=[{'race_id':'r'+str(i),'sealed':True,'delivery':['direct_predictor'],'input_tokens':100,
        'output_tokens':100,'latency_seconds':10,'request_bytes':200000,'context_limit':False,'metrics_complete':True} for i in range(5)]
    return rows,{'race_ids':[r['race_id'] for r in rows],'rows':copy.deepcopy(rows)}


def test_gate_thresholds_inclusive_and_outcome_blind(metrics):
    rows,ref=metrics
    for r in rows:r.update(output_tokens=175,latency_seconds=20,request_bytes=234999)
    result=gate.evaluate(rows,ref)
    assert result['passed'] and not result['outcomes_inspected'] and result['release_count']==0
    rows[0]['output_tokens']=176
    assert not gate.evaluate(rows,ref)['passed']


@pytest.mark.parametrize('mutation',[{'request_bytes':235000},{'context_limit':True},{'sealed':False},
    {'metrics_complete':False},{'delivery':['no_delivery']},{'race_id':'wrong'}])
def test_any_hard_gate_failure_stops(metrics,mutation):
    rows,ref=metrics;rows[0].update(mutation)
    assert not gate.evaluate(rows,ref)['passed']


def test_output_median_and_latency_independently_fail(metrics):
    rows,ref=metrics
    for r in rows[:3]:r['output_tokens']=176
    for r in rows[3:]:r['output_tokens']=1
    result=gate.evaluate(rows,ref)
    assert result['criteria']['aggregate_output'] and not result['criteria']['median_output']
    for r in rows:r.update(output_tokens=100,latency_seconds=20.001)
    assert not gate.evaluate(rows,ref)['criteria']['median_latency']


def test_session_global_attempt_index_is_not_retry():
    attempts=[{'request_id':str(i),'attempt_index':i} for i in range(4)]
    assert gate.retries(attempts)==0
    assert gate.retries(attempts+[{'request_id':'3','attempt_index':4}])==1


def test_gate_refuses_early_outcomes_before_semantic_audit(tmp_path,monkeypatch):
    monkeypatch.setattr(lifecycle,'verify_frozen',lambda *a:None)
    monkeypatch.setattr(gate,'verify_semantics',lambda *a:pytest.fail('must not inspect released state'))
    for i in range(5):
        p=tmp_path/'runs'/('PAR-'+str(i+1).zfill(3));p.mkdir(parents=True)
        state={'release_ledger':{'r':'clock'},'scores':{'sentinel':'never inspect'}}
        state['checkpoint_digest']=digest(state)
        (p/'checkpoint.json.gz').write_bytes(gzip.compress(json.dumps(state).encode()))
    with pytest.raises(IntegrityFailure,match='leakage'):gate.run_gate(tmp_path,{})


def test_same_coordinator_324_order_and_no_early_release():
    assert lifecycle.coordinate is lifecycle.original.coordinate
    dates=['2024-01-06','2024-01-07','2024-01-08','2024-01-13','2024-01-14','2024-01-20','2024-01-21','2024-01-27','2024-01-28','2024-02-03']
    sizes=[24]+[33]*8+[36]
    schedule=[{'day':d,'races':['r'+str(i)+'-'+str(j) for j in range(n)]} for i,(d,n) in enumerate(zip(dates,sizes))]
    lifecycle.check_schedule(schedule);sealed=[];released=[];updates=[]
    def one(day,race,p):sealed.append((day,race));return race
    def release_day(day,races,p):
        assert [r for d,r in sealed if d==day]==races
        released.extend(races);return [{'day':day,'race':r} for r in races]
    def frame(rows,history,day,p,clock,races):return {'day':day,'release_clock':clock,'frame_digest':'ok'}
    def update(p,f,c):updates.append(f['day']);return {'policy_hash':str(len(updates))}
    final,history=lifecycle.coordinate(schedule,{'policy_hash':'P0'},'adaptive_research_program',one,release_day,frame,update,lambda *a,**k:None)
    assert len(sealed)==len(released)==len(history)==324 and len(updates)==10 and final['policy_hash']=='10'


def test_failed_gate_never_releases_or_updates():
    schedule=[{'day':'2024-01-06','races':['r'+str(i) for i in range(24)]}]
    seals=[]
    def one(day,race,policy):
        if len(seals)==5:raise gate.GateFailed('engineering failure')
        seals.append(race);return race
    def forbidden(*a,**k):pytest.fail('gate failure must not release/frame/update')
    with pytest.raises(gate.GateFailed):
        lifecycle.coordinate(schedule,{'policy_hash':'P0'},'adaptive_research_program',one,forbidden,forbidden,forbidden,lambda *a,**k:None)
    assert len(seals)==5


@pytest.mark.parametrize('passed',[True,False])
def test_barrier_same_process_and_single_decision(tmp_path,monkeypatch,passed):
    (tmp_path/'supervisor-start.json').write_text(json.dumps({'supervisor_pid':123}))
    monkeypatch.setattr(gate.os,'kill',lambda pid,sig:None)
    def supervisor_decides(seconds):
        pending=json.loads((tmp_path/'five-race-gate-pending.json').read_text())
        assert pending['sealed']==5 and pending['before_race']==6
        (tmp_path/'five-race-gate.json').write_text(json.dumps({'passed':passed}))
    monkeypatch.setattr(gate.time,'sleep',supervisor_decides)
    if passed:gate.barrier(tmp_path,{})
    else:
        with pytest.raises(gate.GateFailed):gate.barrier(tmp_path,{})
    with pytest.raises(IntegrityFailure,match='reentry'):gate.barrier(tmp_path,{})


def test_monitor_day_release_and_scope(tmp_path,monkeypatch):
    monkeypatch.setattr(supervisor,'verify_frozen',lambda *a:None)
    m={'schedule':[{'day':'2024-01-06','races':['r0','r1']}]}
    def save(i,release=False):
        d=tmp_path/'runs'/('PAR-'+str(i+1).zfill(3));d.mkdir(parents=True,exist_ok=True)
        s={'trajectory_bound':True,'schedule':[{'day':'2024-01-06','races':['r'+str(i)]}],
           'submissions':{'r'+str(i):{}},'release_ledger':{'r'+str(i):'2024-01-06T20:30:00+09:00'} if release else {}}
        s['active_initial']={'card':{'race_id':'r'+str(i),'runners':[{'number':'01'}]}}
        s['active_trace']=[]
        s['evidence_package']={'schema':'evidence-package-v1','race_id':'r'+str(i),'entrants':['01'],'observations':[]}
        s['evidence_package_digest']=digest(s['evidence_package'])
        from jockeybench.clock import release as clock
        if release:s['release_ledger']['r'+str(i)]=clock('2024-01-06')
        s['checkpoint_digest']=digest(s)
        (d/'checkpoint.json.gz').write_bytes(gzip.compress(json.dumps(s).encode()))
    save(0)
    assert supervisor.inspect_snapshot(tmp_path,m)['sealed']==1
    save(0,True)
    with pytest.raises(IntegrityFailure,match='Incomplete day'):supervisor.inspect_snapshot(tmp_path,m)
    save(1);(tmp_path/'five-race-gate.json').write_text('{"passed":true}')
    assert supervisor.inspect_snapshot(tmp_path,m)['released']==1
    save(2)
    with pytest.raises(IntegrityFailure,match='scope'):supervisor.inspect_snapshot(tmp_path,m)
