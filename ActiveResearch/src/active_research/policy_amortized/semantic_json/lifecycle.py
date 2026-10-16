"""Daily freeze/seal/release/frame/revise coordinator and the authorized Day1 runner."""
import copy
import json
import time
from pathlib import Path
from jrdb_lh.common import Rejected,cutoff,release,digest,file_hash
from jrdb_lh.config import Config
from jrdb_lh.runner import Run
from jrdb_lh.audit import replay
from jrdb_lh.ledger import summary
from active_research.runner import PROJECT,ActiveService,check_config,settle
from active_research.adaptive.model import credential
from active_research.experience.runner import validate_schedule
from active_research.aligned.model import load_settings
from active_research.aligned.scoring import checkpoint_components
from active_research.aligned.integrity import IntegrityFailure
from ..policy_builder import Registry,construct,matched_pair,revise,write
from ..policy_contract import validate_source,source_hash
from .runtime import Predictor,predict
from ..frame import extract,make_frame
from .runtime import verify_semantics
from ..specification import validate_manifest
from ..service import PolicyService


import os
from .view import VERSION
from .. import lifecycle as original
from ..prepare import freeze as base_freeze
from ..specification import PROFILE
from ..readable_evidence.lifecycle import Registry, construct, P0_HASH
from .gate import barrier, GateFailed
PROTOCOL = 'Policy-Amortized-Research-v2.1-Warmup-SemanticJSON-Light-v1'
coordinate = original.coordinate


def check_schedule(schedule):
    if (validate_schedule(schedule) != 324 or len(schedule) != 10 or
        schedule[0]['day'] != '2024-01-06' or schedule[-1]['day'] != '2024-02-03' or
        len(schedule[0]['races']) != 24):
        raise IntegrityFailure('Only the authorized 10-day / 324-race trajectory')


def freeze(out, schedule):
    out = Path(out);check_schedule(schedule)
    m = base_freeze(out, 'adaptive_research_program', schedule)
    m.update(experiment_protocol=PROTOCOL,evidence_view_protocol=VERSION,smoke_only=False,
             reused_initial_policy_hash=P0_HASH,engineering_gate_after_races=5)
    for name in ('frozen-warmed-p0.json','semantic-offline-gate.json','comparison-source.json','five-race-reference.json'):
        m['files'][name] = file_hash(out/name)
    write(out/'manifest.json',m)
    return m


def verify_frozen(out,m):
    if m.get('experiment_protocol') != PROTOCOL or m.get('evidence_view_protocol') != VERSION:
        raise IntegrityFailure('Wrong SemanticJSON formal protocol')
    if json.loads((out/'manifest.json').read_text())!=m:raise IntegrityFailure('Manifest mutation')
    check_schedule(m['schedule'])
    if m.get('maximum_real_races') != 324 or m.get('engineering_gate_after_races') != 5 or m.get('smoke_only') is not False:
        raise IntegrityFailure('Wrong trajectory scope/gate')
    original.verify_frozen(out,{**m,'experiment_protocol':PROFILE['experiment_protocol']})
    gate=json.loads((out/'semantic-offline-gate.json').read_text())
    if not gate.get('passed') or gate.get('semantic_pass') != 24 or gate.get('max_semantic_request_bytes',262144)>=235000:
        raise IntegrityFailure('Missing offline acceptance')
    if gate['source_hashes']!=m['protected']:raise IntegrityFailure('Acceptance source mismatch')


def execute(out):
    out=Path(out).resolve();m=json.loads((out/'manifest.json').read_text());verify_frozen(out,m)
    if not out.is_relative_to(PROJECT) or not (PROJECT/'.experiment-host').is_file():raise Rejected('Experiment host only')
    check_schedule(m['schedule'])
    if m['scientific_arm']!='adaptive_research_program':raise Rejected('Only Adaptive trajectory authorized')
    gate=json.loads((out/'offline-gate.json').read_text())
    if not gate.get('passed') or gate['source_hashes']!={k:v for k,v in m['protected'].items() if k.startswith('active_research/policy_amortized/')}:
        raise Rejected('Missing matching offline gate')
    config=Config.load(out/'config.json');check_config(config)
    if not Path(config.managed_root).resolve().is_relative_to(out):raise Rejected('New storage required')
    settings=load_settings(out/'provider.json')
    marker=out/'started.json'
    if marker.exists():raise Rejected('No automatic restart/resume')
    credential(settings)
    with marker.open('x') as f:json.dump({'manifest':digest(m),'maximum_real_races':324},f)
    start=time.monotonic();events=[];reports=[];errors=[];sessions=[];states=[];completed=[]
    registry=Registry(out/'policies');initial=None;candidate=None
    def emit(kind,**data):
        events.append({'sequence':len(events),'kind':kind,**data});write(out/'events.json',events)
        if kind=='day_complete':
            completed.append(data['day'])
            from .report import daily
            daily(out,data['day'])
    def reopen(run):
        run=Run(config,run.state['run_id'],run.agent)
        service=PolicyService(run.source,config,run.state,run.persist);service.executor=run.executor
        run.service=service;run.executor.service=service;return run
    def one(day,race,policy):
        verify_frozen(out,m)
        os.kill(json.loads((out/'supervisor-start.json').read_text())['supervisor_pid'],0)
        if len(states)==5:barrier(out,m)
        expected=[(b['day'],r) for b in m['schedule'] for r in b['races']]
        if len(states)>=324 or (day,race)!=expected[len(states)]:raise IntegrityFailure('Trajectory scope/order')
        ident='PAR-'+str(len(states)+1).zfill(3)
        if (Path(config.managed_root)/ident).exists():raise IntegrityFailure('Existing race storage')
        agent=Predictor(settings,policy);run=Run(config,ident,agent);s=run.state;states.append(s)
        try:
            bound=run.source.con.execute('SELECT day FROM races WHERE id=?',(run.source.raw_race(race),)).fetchone()
            if not bound or bound['day']!=day:raise IntegrityFailure('Race/day source binding')
            s.update(schedule=[{'day':day,'races':[race]}],clock=cutoff(day),initial_clock=cutoff(day),trajectory_bound=True)
            service=PolicyService(run.source,config,s,run.persist);service.executor=run.executor
            run.service=service;run.executor.service=service;run.persist();run.preflight()
            got=predict(run,agent)
            if got!=(race,day) or s['scores'] or s['release_ledger']:raise IntegrityFailure('Seal/release binding')
            s['prediction_phase_cost']=summary(s,config)
            s.pop('strategy_job',None);s.pop('strategy_job_usage',None);s['phase']='awaiting_daily_release';run.persist()
            write(out/'progress.json',{'attempted':len(states),'sealed':sum(bool(x['submissions']) for x in states),
                'delivery':[list(x.get('aligned_delivery',{}).values()) for x in states]})
            return run
        except IntegrityFailure:
            if not s['submissions']:
                s.setdefault('aligned_delivery',{})[race]={'delivery':'no_delivery','generated_by':None,'reason':'hard_integrity_failure','bonus_eligible':False,'submit_phase':None}
                run.persist()
            raise
        finally:run.close()
    def released(day,sealed,policy):
        verify_frozen(out,m)
        decision=json.loads((out/'five-race-gate.json').read_text())
        if not decision.get('passed'):raise IntegrityFailure('Release before engineering gate pass')
        expected=next(b['races'] for b in m['schedule'] if b['day']==day)
        if [r.state['schedule'][0]['races'][0] for r in sealed]!=expected:raise IntegrityFailure('Incomplete day release')
        if any(not r.state['submissions'] or r.state['release_ledger'] for r in sealed):raise IntegrityFailure('Day release barrier')
        rows=[]
        for saved in sealed:
            run=reopen(saved);s=run.state;race=s['schedule'][0]['races'][0]
            states[states.index(saved.state)]=s
            try:
                settle(run,race,day)
                s['scores'][race]['format_fallback']=s['submissions'][race]['format_fallback']
                s['phase']='reflect';result=run.service.call('results',{'race_id':race})
                s['experience_result_cost']=result['_cost'];s['aligned_scores']=checkpoint_components(s)
                s['phase']='completed';run.persist()
                try:core=replay(config,s);semantic=verify_semantics(s)
                except Exception as exc:raise IntegrityFailure('Post-release audit failed: '+str(exc)) from exc
                row=extract(s,policy,release(day));rows.append(row)
                reports.append({'run_id':s['run_id'],'row':row,'core_audit':core,'semantic_audit':semantic})
                write(out/'race-reports.json',reports);emit('result_released',day=day,race_id=race,clock=release(day))
            finally:run.close()
        return rows
    def feedback(rows,history,day,policy,clock,races):
        f=make_frame(rows,history,day,policy,clock,races)
        if f!=make_frame(copy.deepcopy(rows),copy.deepcopy(history),day,policy,clock,races):raise IntegrityFailure('Nondeterministic Frame')
        write(out/('frame-'+day+'.json'),f);return f
    def update(current,frame,clock):
        nonlocal candidate
        candidate,session=revise(m['scientific_arm'],current,frame,clock,out,settings,registry)
        if session:sessions.append(session)
        write(out/('next-policy-'+frame['day']+'.json'),{'policy_hash':candidate['policy_hash'],'policy_version':candidate['policy_version'],
            'differs_from_initial':candidate['policy_hash']!=initial['policy_hash'],'validation':validate_source(candidate['source']),
            'created_after_day':frame['day'],'retained_previous':candidate['policy_hash']==current['policy_hash']})
        return candidate
    try:
        initial,session=construct(out,settings,registry);sessions.append(session)
        hashes=matched_pair(initial,out/'matched-initial')
        write(out/'initial-policy.json',initial)
        write(out/'race-freeze.json',{'manifest':digest(m),'initial_policy_hash':initial['policy_hash'],'matched_hashes':hashes})
        coordinate(m['schedule'],initial,m['scientific_arm'],one,released,feedback,update,emit)
        verify_frozen(out,m)
    except Exception as exc:
        import traceback
        errors.append({'type':type(exc).__name__,'message':str(exc),'hard_integrity_failure':isinstance(exc,IntegrityFailure),
                       'trace':traceback.format_exc()})
    finally:
        write(out/'execution-status.json',{'finished':True,'attempted':len(states),'completed_days':completed,'errors':errors,
            'elapsed_seconds':time.monotonic()-start,'maximum_real_races':324,'engineering_gate_failed':any(e['type']=='GateFailed' for e in errors)})
        # Reporting is supervisor-owned; never run outcome reports at the five-race gate.
    if errors:raise Rejected('Formal trajectory stopped; see execution-status.json')
