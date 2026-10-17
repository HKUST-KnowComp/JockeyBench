"""Independent aligned lifecycle scaffold; all research and daily semantics reused."""
import copy
import json
import time
import traceback
from contextlib import nullcontext
from pathlib import Path
from jockeybench.errors import Rejected
from jockeybench.clock import cutoff, release
from jockeybench.canonical import digest, file_hash
from jockeybench.config import Config
from jockeybench.runner import Run
from jockeybench.audit.replay import replay
from jockeybench.ledger import summary
from jockeybench.research.runner import PROJECT, ActiveService, check_config, settle
from jockeybench.research.llm.runner import write_json, result_report, scan_no_credential
from jockeybench.research.adaptive.runner import predict as original_predict
from jockeybench.research.adaptive.model import credential, model_cost
from jockeybench.research.experience.runner import lifecycle, validate_schedule
from jockeybench.research.experience.frame import neutral, make_frame, PROTOCOL, SCHEMA
from .frame import extract
from .scoring import checkpoint_components
from .integrity import IntegrityFailure
from .delivery import predict as graceful_predict
from .model import AlignedAgent, load_settings
from .audit import verify_semantics, classify_failure
from .specification import validate_manifest
from .report import resources, build_report

def research_predict(run, agent, memory):
    started = time.monotonic()
    try:
        return original_predict(run, agent, memory)
    finally:
        run.state['aligned_race_elapsed_seconds'] = time.monotonic()-started
        request_seconds = sum(t.get('logical_request_elapsed_seconds',0)
                              for t in run.state.get('llm_trajectory',[]))
        run.state['aligned_tool_and_local_seconds'] = max(0,
            run.state['aligned_race_elapsed_seconds']-request_seconds)
        run.persist()

def predict(run,agent,memory):
    started=time.monotonic()
    try:
        return graceful_predict(run,agent,memory,research_predict)
    finally:
        run.state['aligned_research_elapsed_seconds']=run.state.get('aligned_race_elapsed_seconds',0)
        run.state['aligned_race_elapsed_seconds']=time.monotonic()-started
        request_seconds=sum(t.get('logical_request_elapsed_seconds',0) for t in run.state.get('llm_trajectory',[]))
        run.state['aligned_tool_and_local_seconds']=max(0,run.state['aligned_race_elapsed_seconds']-request_seconds)
        run.persist()

def attach_delivery_report(state,report):
    report.update(scoring_profile='common-aligned-delivery-v1',
        delivery=copy.deepcopy(state.get('aligned_delivery',{})),
        graceful_trigger=state.get('graceful_trigger'),finalization_failure=state.get('finalization_failure'))
    for key in ('prediction_score','net_score','net_B_after_model_estimate'):
        if key in report:report['legacy_'+key]=report.pop(key)
    return report

def execute(out):
    manifest = json.loads((out/'A2-manifest.json').read_text())
    validate_manifest(manifest)
    config = Config.load(out/'A2-config.json')
    check_config(config)  # Each isolated race retains the original one-race service contract.
    if not Path(config.managed_root).resolve().is_relative_to(out.resolve()):
        raise Rejected('Aligned race storage must belong to this new output directory')
    settings = load_settings(out/'A2-provider.json')
    if manifest.get('protocol')!=PROTOCOL or manifest.get('frame_schema')!=SCHEMA:
        raise Rejected('New experience protocol manifest required')
    if 'A2-reflection-budget.json' in manifest['files']:
        raise Rejected('No reflection provider budget in experience protocol')
    count = validate_schedule(manifest['schedule'])
    if count != manifest['maximum_real_races']:
        raise Rejected('Plan and authorization count differ')
    for name, expected in manifest['protected'].items():
        if file_hash(PROJECT/name) != expected:
            raise Rejected('Frozen implementation changed')
    for name, expected in manifest['files'].items():
        if file_hash(out/name) != expected:
            raise Rejected('Frozen configuration changed')
    if config.tariff.multiplier != .5 or config.tariff.hit_bonus_yen != 0:
        raise Rejected('Expected multiplier .5 and B=0')
    if (out/'A2-started.json').exists():
        raise Rejected('Already attempted; never restart or rerun')
    credential(settings)
    write_json(out/'A2-started.json', {'manifest': digest(manifest), 'maximum_real_races': count})
    runs = []
    events = []
    errors = []
    completed_days = []
    history = []
    reports = []
    attempted = 0

    def suspend(run):
        if not getattr(run, '_A2_closed', False):
            run.persist()
            run.close()
            run._A2_closed = True

    def activate(run):
        if not getattr(run, '_A2_closed', False):
            return run
        reopened = Run(config, run.state['run_id'], run.agent)
        service = ActiveService(reopened.source, config, reopened.state, reopened.persist)
        service.executor = reopened.executor
        reopened.service = service
        reopened.executor.service = service
        runs[runs.index(run)] = reopened
        return reopened

    def finish(run, failure=None):
        s = run.state
        audit = replay(config, s)
        semantic = verify_semantics(s)
        r = result_report(run, audit, failure)
        attach_delivery_report(s,r)
        r.update(delivery=copy.deepcopy(s.get('aligned_delivery',{})),
                 aligned_scoring=checkpoint_components(s) if s.get('aligned_delivery') and s['scores'] else None)
        r.update(aligned_resources=resources([s]), completed=bool(s['completed']), settled=bool(s['scores']),
                 semantic_audit=semantic, audit_error=None,
                 memory_before_digest=digest(s.get('memory_before')),
                 memory_after_digest=digest(s['daily_memory_after']) if s.get('daily_memory_after') else None,
                 network_retries=max(0,len(s.get('provider_attempts',[]))-len(s.get('llm_trajectory',[]))))
        reports.append(r)
        write_json(out/'A2-results.json', reports)
        suspend(run)
        runs.remove(run)

    def emit(kind, **data):
        if manifest['scientific_arm']=='frozen_neutral_history' and kind in ('day_start','sealed'):
            data['memory']=digest(neutral())
        events.append({'sequence': len(events), 'kind': kind, **data})
        write_json(out/'A2-events.json', events)
        if kind == 'experience_commit':
            completed_days.append(data['day'])

    def prediction(day, race, memory):
        nonlocal attempted
        if manifest['scientific_arm'] == 'frozen_neutral_history':
            memory = neutral()
        attempted += 1
        ident = 'A2-aligned-' + str(attempted).zfill(3)
        if (Path(config.managed_root)/ident).exists():
            raise Rejected('Existing race; no overwrite')
        agent = AlignedAgent(settings)
        run = Run(config, ident, agent)
        runs.append(run)
        bound = run.source.con.execute('SELECT day FROM races WHERE id=?', (run.source.raw_race(race),)).fetchone()
        if not bound or bound['day'] != day:
            raise Rejected('Race/day binding differs from source')
        s = run.state
        s['schedule'] = [{'day': day, 'races': [race]}]
        s.update(clock=cutoff(day), initial_clock=cutoff(day),
                 A2_experience={'manifest': digest(manifest), 'memory_before': digest(memory)})
        service = ActiveService(run.source, config, s, run.persist)
        service.executor = run.executor
        run.service = service
        run.executor.service = service
        run.persist()
        emit('prediction_start', day=day, race_id=race, clock=cutoff(day), memory=digest(memory))
        with nullcontext():
            run.preflight()
            got = predict(run, agent, memory)
        if got != (race, day) or s['scores'] or s['release_ledger']:
            raise Rejected('Invalid seal or premature release')
        s['prediction_phase_cost'] = summary(s, config)
        s['prediction_model_cost'] = model_cost(s)
        s.pop('strategy_job', None)
        s.pop('strategy_job_usage', None)
        s['phase'] = 'awaiting_daily_release'
        run.persist()
        suspend(run)
        return run

    def results(day, sealed):
        if any(not r.state['submissions'] or r.state['release_ledger'] for r in sealed):
            raise Rejected('Day release barrier failed')
        rows = []
        for i, run in enumerate(sealed):
            run = activate(run)
            sealed[i] = run
            s = run.state
            race = s['schedule'][0]['races'][0]
            settle(run, race, day)
            s['scores'][race]['format_fallback']=s['submissions'][race]['format_fallback']
            s['aligned_scores']=checkpoint_components(s)
            run.persist()
            # As before: paid result access occurs only after original settlement/release.
            s['phase'] = 'reflect'
            actual = run.service.call('results', {'race_id': race})
            s['experience_result_cost']=actual['_cost']
            s['aligned_scores']=checkpoint_components(s)
            s['phase']='awaiting_experience_generation'
            run.persist()
            row=extract(s,day,race,release(day))
            rows.append(row)
            write_json(out/(s['run_id']+'-experience-record.json'),row)
            emit('result_released', day=day, race_id=race, clock=release(day))
            suspend(run)
        return rows

    def experience(day, sealed, outcomes, memory):
        for i, run in enumerate(sealed):
            run=activate(run)
            sealed[i]=run
            run.state['phase']='completed'
            run.persist()
            finish(run)
        races=[b['races'] for b in manifest['schedule'] if b['day']==day][0]
        updated=make_frame(outcomes,history,day,races,release(day))
        history.extend(copy.deepcopy(outcomes))
        write_json(out/('A2-experience-frame-'+day+'.json'),updated)
        write_json(out/'A2-experience-frame.json',updated)
        write_json(out/'A2-experience-records.json',history)
        return updated

    try:
        lifecycle(manifest['schedule'], neutral(), prediction, results, experience, emit)
    except BaseException as exc:
        failure_code = 'hard_integrity_failure' if isinstance(exc,IntegrityFailure) else classify_failure(exc, runs[-1].state if runs else {})
        if failure_code=='hard_integrity_failure' and runs:
            failed=runs[-1].state
            failed['delivery_failure_kind']='hard_integrity_failure'
            rid=failed['schedule'][0]['races'][0]
            if rid not in failed['submissions']:
                failed.setdefault('aligned_delivery',{})[rid]={'delivery':'no_delivery',
                    'generated_by':None,'reason':'hard_integrity_failure','bonus_eligible':False,'submit_phase':None}
        errors.append({'type': type(exc).__name__, 'failure_code': failure_code, 'stage': events[-1]['kind'] if events else 'startup',
                       'frames': [{'file': Path(f.filename).name, 'line': f.lineno, 'function': f.name}
                                  for f in traceback.extract_tb(exc.__traceback__)]})
        emit('failed', error_type=type(exc).__name__)
    finally:
        for run in list(runs):
            try:
                run = activate(run)
                s = run.state
                audit = semantic = None
                audit_error = None
                try:
                    audit = replay(config, s)
                    semantic = verify_semantics(s)
                except Exception as exc:
                    audit_error = type(exc).__name__
                r = result_report(run, audit, errors[0]['type'] if errors else audit_error)
                attach_delivery_report(s,r)
                r.update(delivery=copy.deepcopy(s.get('aligned_delivery',{})),
                    aligned_scoring=checkpoint_components(s) if s.get('aligned_delivery') and s['scores'] and audit else None)
                r.update(aligned_resources=resources([s]), completed=bool(s['completed']),
                         settled=bool(s['scores']), semantic_audit=semantic, audit_error=audit_error,
                         memory_before_digest=digest(s.get('memory_before')),
                         memory_after_digest=digest(s['daily_memory_after']) if s.get('daily_memory_after') else None,
                         network_retries=max(0,len(s.get('provider_attempts',[]))-len(s.get('llm_trajectory',[]))))
                reports.append(r)
            except Exception as exc:
                reports.append({'run_id':run.state['run_id'], 'completed':False, 'report_error':type(exc).__name__,
                    'delivery':copy.deepcopy(run.state.get('aligned_delivery',{})),
                    'aligned_resources':resources([run.state]),'aligned_scoring':None})
            finally:
                suspend(run)
        write_json(out/'A2-results.json', reports)
        write_json(out/'A2-execution-status.json', {'finished':True,'attempted':attempted,
                   'completed_days':completed_days,'errors':errors,'full_experiment_started':bool(manifest.get('formal',False)),
                   'total_attempts':attempted})
        write_json(out/'A2-credential-scan.json',scan_no_credential(out))
        build_report(out)
