"""Daily freeze/seal/release/frame/revise coordinator and the authorized Day1 runner."""
import copy
import json
import time
from pathlib import Path
from jockeybench.errors import Rejected
from jockeybench.clock import cutoff, release
from jockeybench.canonical import digest, file_hash
from jockeybench.config import Config
from .source_binding import VerifiedRun as Run
from .native_replay import replay
from jockeybench.ledger import summary
from jockeybench.research.runner import PROJECT,ActiveService,check_config,settle
from jockeybench.research.adaptive.model import credential
from jockeybench.research.experience.runner import validate_schedule
from jockeybench.research.aligned.model import load_settings
from jockeybench.research.aligned.scoring import checkpoint_components
from jockeybench.research.aligned.integrity import IntegrityFailure
from .policy_builder import Registry,construct,matched_pair,revise,write
from .policy_contract import validate_source,source_hash
from .runtime import Predictor,predict
from .frame import extract,make_frame
from .runtime import verify_semantics
from jockeybench.research.policy_amortized.specification import validate_manifest
from jockeybench.research.policy_amortized.service import PolicyService


import os
from jockeybench.research.policy_amortized.semantic_json.view import VERSION
from jockeybench.research.policy_amortized import lifecycle as original
from jockeybench.research.policy_amortized.prepare import freeze as base_freeze
from jockeybench.research.policy_amortized.specification import PROFILE
from .warmup import build
from . import FRAME
from .gate import barrier, GateFailed
from . import PROTOCOL,CONTRACT_VERSION
coordinate = original.coordinate


def check_schedule(schedule):
    if (validate_schedule(schedule) != 324 or len(schedule) != 10 or
        schedule[0]['day'] != '2024-01-06' or schedule[-1]['day'] != '2024-02-03' or
        len(schedule[0]['races']) != 24):
        raise IntegrityFailure('Only the authorized 10-day / 324-race trajectory')


def freeze(out, schedule):
    out=Path(out);check_schedule(schedule)
    m=base_freeze(out,'adaptive_research_program',schedule)
    m.update(experiment_protocol=PROTOCOL,evidence_view_protocol=VERSION,
        research_program_contract=CONTRACT_VERSION, marginal_accounting='native-delta-owned-cache-v1',
        experience_frame_protocol=FRAME,smoke_only=False,engineering_gate_after_races=5,
        warmup_reexecuted=True,reused_initial_policy_hash=None,
        implementation_revision='source-epoch-policy-cpu-v1',
        updater_context_revision='released-finegrained-updater-context-v1',
        day1_updater_lifecycle_required=True)
    for name in ('warmup-profile.json','v3-offline-gate.json','baseline-bindings.json'):
        m['files'][name]=file_hash(out/name)
    write(out/'manifest.json',m);return m

def verify_frozen(out,m,require_approval=True):
    out=Path(out)
    if (out/'model-identity-hard-stop.json').exists():raise IntegrityFailure('V3 durable model identity hard stop')
    if m.get('implementation_revision')!='source-epoch-policy-cpu-v1':raise IntegrityFailure('Wrong timing implementation')
    frozen_control=m.get('scientific_arm')=='frozen_research_program'
    if (m.get('updater_context_revision')!='released-finegrained-updater-context-v1'
            or m.get('day1_updater_lifecycle_required') is not (not frozen_control)):
        raise IntegrityFailure('Wrong updater context/Day1 checkpoint implementation')
    if m.get('research_program_contract')!=CONTRACT_VERSION or m.get('marginal_accounting')!='native-delta-owned-cache-v1':raise IntegrityFailure('Wrong marginal contract')
    if m.get('experiment_protocol')!=PROTOCOL or m.get('experience_frame_protocol')!=FRAME or m.get('evidence_view_protocol')!=VERSION:
        raise IntegrityFailure('Wrong v3 protocol')
    if json.loads((out/'manifest.json').read_text())!=m:raise IntegrityFailure('Manifest mutation')
    check_schedule(m['schedule'])
    if m.get('maximum_real_races')!=324 or m.get('engineering_gate_after_races')!=5 or m.get('warmup_reexecuted') is not (not frozen_control) or (not frozen_control and m.get('reused_initial_policy_hash') is not None):
        raise IntegrityFailure('V3 scope/freshness')
    if frozen_control:
        from .frozen_inputs import verify_matched_inputs
        verify_matched_inputs(out,m)
    original.verify_frozen(out,{**m,'experiment_protocol':PROFILE['experiment_protocol'],
        'research_program_contract':PROFILE['research_program_contract'],
        'experience_frame_protocol':PROFILE['experience_frame_protocol']})
    g=json.loads((out/'v3-offline-gate.json').read_text())
    if not g.get('passed') or g['source_hashes']!=m['protected']:raise IntegrityFailure('Missing v3 offline acceptance')
    baseline=json.loads((out/'baseline-bindings.json').read_text())
    if digest(m['schedule'])!=baseline['schedule_digest']:raise IntegrityFailure('V2 exact universe/order changed')
    settings=json.loads((out/'provider.json').read_text())
    if settings.get('model')!='deepseek-ai/DeepSeek-V4-Flash-0731':raise IntegrityFailure('DeepSeek only; GLM is not authorized')
    if settings!=baseline['provider']:raise IntegrityFailure('V2 provider config changed')
    config=json.loads((out/'config.json').read_text())
    if {k:v for k,v in config.items() if k!='managed_root'}!=baseline['config_without_managed_root']:raise IntegrityFailure('V2 config changed')
    profile=json.loads((out/'warmup-profile.json').read_text())
    if profile!=baseline['warmup_profile']:raise IntegrityFailure('Warmup profile changed')
    from jockeybench.research.policy_warmup.archive import load
    load(profile)
    from .policy_contract import source_hash
    approval=out/'paid-approval.json'
    if require_approval and (not approval.exists() or json.loads(approval.read_text()).get('manifest_digest')!=digest(m)):raise IntegrityFailure('Explicit prelaunch approval missing')

def construct(out,settings,registry):
    manifest=json.loads((Path(out)/'manifest.json').read_text())
    if manifest.get('scientific_arm')=='frozen_research_program':
        from .frozen_inputs import load_initial
        return load_initial(Path(out),manifest),None
    profile=json.loads((Path(out)/'warmup-profile.json').read_text())
    if (Path(out)/'initial-policy.json').exists():raise IntegrityFailure('Fresh v3 warmup required')
    policy=build(out,profile)
    return policy,json.loads((Path(out)/'warmup-session.json').read_text())


def execute(out):
    out=Path(out).resolve();m=json.loads((out/'manifest.json').read_text());verify_frozen(out,m)
    if not out.is_relative_to(PROJECT) or not (PROJECT/'.experiment-host').is_file():raise Rejected('Experiment host only')
    check_schedule(m['schedule'])
    if m['scientific_arm'] not in ('adaptive_research_program','frozen_research_program'):raise Rejected('Unknown trajectory arm')
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
        write(out/('frame-'+day+'.json'),f)
        if m['scientific_arm']=='frozen_research_program':
            write(out/('next-policy-'+day+'.json'),{'policy_hash':policy['policy_hash'],'policy_version':policy['policy_version'],
                'differs_from_initial':False,'validation':policy['validation_result'],
                'created_after_day':day,'retained_previous':True,'decision':'protocol_frozen_no_updater'})
        return f
    def update(current,frame,clock):
        nonlocal candidate
        verify_frozen(out,m)
        candidate,session=revise(m['scientific_arm'],current,frame,clock,out,settings,registry)
        verify_frozen(out,m)
        if session:sessions.append(session)
        from .updater_context import lifecycle_checkpoint
        checkpoint=lifecycle_checkpoint(session or {})
        write(out/('updater-engineering-checkpoint-'+frame['day']+'.json'),checkpoint)
        if frame['day']==m['schedule'][0]['day']:
            write(out/'day1-updater-engineering-checkpoint.json',checkpoint)
            if not checkpoint['passed']:
                raise IntegrityFailure('Day1 updater lifecycle failed; stop before Day2, no restart')
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
