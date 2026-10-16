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
from .policy_builder import Registry,construct,matched_pair,revise,write
from .policy_contract import validate_source,source_hash
from .predictor import Predictor,predict
from .frame import extract,make_frame
from .audit import verify_semantics
from .specification import validate_manifest
from .service import PolicyService

def coordinate(schedule,initial,arm,predict_one,release_day,make_feedback,update,emit):
    validate_schedule(schedule);active=copy.deepcopy(initial);history=[]
    for batch in schedule:
        policy=copy.deepcopy(active);day=batch['day'];emit('day_start',day=day,policy_hash=policy['policy_hash'])
        sealed=[]
        for race in batch['races']:
            sealed.append(predict_one(day,race,copy.deepcopy(policy)))
            emit('sealed',day=day,race_id=race,policy_hash=policy['policy_hash'])
        clock=release(day);emit('release_gate',day=day,clock=clock,races=batch['races'])
        rows=release_day(day,sealed,policy)
        frame=make_feedback(rows,history,day,policy,clock,batch['races'])
        if frame['day']!=day or frame['release_clock']!=clock:raise Rejected('Frame publication boundary')
        emit('frame_commit',day=day,frame_digest=frame['frame_digest'])
        history.extend(copy.deepcopy(rows))
        if arm=='adaptive_research_program':active=update(copy.deepcopy(policy),frame,clock)
        elif arm!='frozen_research_program':raise Rejected('Unknown arm')
        emit('day_complete',day=day,next_policy_hash=active['policy_hash'])
    return active,history

def verify_frozen(out,m):
    validate_manifest(m)
    for name,h in m['protected'].items():
        if file_hash(PROJECT/name)!=h:raise IntegrityFailure('Protected source changed: '+name)
    for name,h in m['files'].items():
        if file_hash(out/name)!=h:raise IntegrityFailure('Frozen input changed: '+name)

def execute(out):
    out=Path(out).resolve();m=json.loads((out/'manifest.json').read_text());verify_frozen(out,m)
    if not out.is_relative_to(PROJECT) or not (PROJECT/'.experiment-host').is_file():raise Rejected('Experiment host only')
    if len(m['schedule'])!=1 or m['schedule'][0]['day']!='2024-01-06' or validate_schedule(m['schedule'])!=24:raise Rejected('Only authorized 24-race Day1 real smoke')
    if m['scientific_arm']!='adaptive_research_program':raise Rejected('Only Adaptive Day1 smoke authorized')
    gate=json.loads((out/'offline-gate.json').read_text())
    if not gate.get('passed') or gate['source_hashes']!={k:v for k,v in m['protected'].items() if k.startswith('active_research/policy_amortized/')}:
        raise Rejected('Missing matching offline gate')
    config=Config.load(out/'config.json');check_config(config)
    if not Path(config.managed_root).resolve().is_relative_to(out):raise Rejected('New storage required')
    settings=load_settings(out/'provider.json')
    marker=out/'started.json'
    if marker.exists():raise Rejected('No automatic restart/resume')
    credential(settings)
    with marker.open('x') as f:json.dump({'manifest':digest(m),'maximum_real_races':24},f)
    start=time.monotonic();events=[];reports=[];errors=[];sessions=[];states=[];completed=[]
    registry=Registry(out/'policies');initial=None;candidate=None
    def emit(kind,**data):
        events.append({'sequence':len(events),'kind':kind,**data});write(out/'events.json',events)
        if kind=='day_complete':completed.append(data['day'])
    def reopen(run):
        run=Run(config,run.state['run_id'],run.agent)
        service=PolicyService(run.source,config,run.state,run.persist);service.executor=run.executor
        run.service=service;run.executor.service=service;return run
    def one(day,race,policy):
        ident='PAR-'+str(len(states)+1).zfill(3)
        if (Path(config.managed_root)/ident).exists():raise IntegrityFailure('Existing race storage')
        agent=Predictor(settings,policy);run=Run(config,ident,agent);s=run.state;states.append(s)
        try:
            bound=run.source.con.execute('SELECT day FROM races WHERE id=?',(run.source.raw_race(race),)).fetchone()
            if not bound or bound['day']!=day:raise IntegrityFailure('Race/day source binding')
            s.update(schedule=[{'day':day,'races':[race]}],clock=cutoff(day),initial_clock=cutoff(day))
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
        write(out/'day2-candidate.json',{'policy_hash':candidate['policy_hash'],'policy_version':candidate['policy_version'],
            'differs_from_initial':candidate['policy_hash']!=initial['policy_hash'],'validation':validate_source(candidate['source']),
            'day2_executed':False,'retained_previous':candidate['policy_hash']==current['policy_hash']})
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
            'elapsed_seconds':time.monotonic()-start,'day2_races_executed':0,'full_experiment_started':False})
        from .report import build_report
        build_report(out,states,sessions)
    if errors:raise Rejected('Policy smoke stopped; see execution-status.json')
