"""A2 experience-guided coordinator; no LLM reflection in this protocol."""
import copy
import json
import sys
import time
import traceback
from pathlib import Path

from jockeybench.errors import Rejected
from jockeybench.clock import cutoff, release
from jockeybench.canonical import digest, file_hash
from jockeybench.config import Config
from jockeybench.runner import Run
from jockeybench.audit.replay import replay
from jockeybench.ledger import summary
from jockeybench.research.runner import PROJECT, ActiveService, check_config, settle
from jockeybench.research.llm.runner import wall_limit, write_json, result_report, scan_no_credential
from jockeybench.research.adaptive.runner import predict, MAX_TURNS, WALL_SECONDS, SafetyLimit
from .model import ExperienceAgent as AdaptiveAgent, verify_semantics, PROMPT
from jockeybench.research.adaptive.model import load_settings, credential, model_cost
from .frame import neutral, make_frame, extract, PROTOCOL, SCHEMA



def validate_schedule(days):
    seen = set()
    for i, batch in enumerate(days):
        if not batch['races'] or (i and release(days[i-1]['day']) > cutoff(batch['day'])):
            raise Rejected('Empty day or invalid day chronology')
        for race in batch['races']:
            if race in seen:
                raise Rejected('Duplicate race')
            seen.add(race)
    if not seen:
        raise Rejected('Empty schedule')
    return len(seen)


def lifecycle(days, memory, predict_one, release_day, build_experience, emit):
    validate_schedule(days)
    for batch in days:
        frozen = copy.deepcopy(memory)
        emit('day_start', day=batch['day'], memory=digest(frozen))
        sealed=[]
        for race in batch['races']:
            sealed.append(predict_one(batch['day'], race, copy.deepcopy(frozen)))
            emit('sealed', day=batch['day'], race_id=race, memory=digest(frozen))
        emit('release_gate', day=batch['day'], clock=release(batch['day']), races=batch['races'])
        outcomes=release_day(batch['day'],sealed)
        memory=build_experience(batch['day'],sealed,outcomes,copy.deepcopy(frozen))
        if memory.get('protocol')!=PROTOCOL or memory.get('through_day')!=batch['day']:
            raise Rejected('Experience publication boundary mismatch')
        emit('experience_generated',day=batch['day'],memory=digest(memory))
        emit('experience_commit',day=batch['day'],memory=digest(memory))
    return memory


def execute(out):
    manifest = json.loads((out/'A2-manifest.json').read_text())
    config = Config.load(out/'A2-config.json')
    check_config(config)  # Each isolated race retains the original one-race service contract.
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
        r.update(completed=bool(s['completed']), settled=bool(s['scores']),
                 semantic_audit=semantic, audit_error=None,
                 memory_before_digest=digest(s.get('memory_before')),
                 memory_after_digest=digest(s['daily_memory_after']) if s.get('daily_memory_after') else None,
                 network_retries=max(0,len(s.get('provider_attempts',[]))-len(s.get('llm_trajectory',[]))))
        reports.append(r)
        write_json(out/'A2-results.json', reports)
        suspend(run)
        runs.remove(run)

    def emit(kind, **data):
        events.append({'sequence': len(events), 'kind': kind, **data})
        write_json(out/'A2-events.json', events)
        if kind == 'experience_commit':
            completed_days.append(data['day'])

    def prediction(day, race, memory):
        nonlocal attempted
        attempted += 1
        ident = 'A2-experience-' + str(attempted).zfill(3)
        if (Path(config.managed_root)/ident).exists():
            raise Rejected('Existing race; no overwrite')
        agent = AdaptiveAgent(settings, time.monotonic()+WALL_SECONDS)
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
        with wall_limit(WALL_SECONDS):
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
            # As before: paid result access occurs only after original settlement/release.
            s['phase'] = 'reflect'
            actual = run.service.call('results', {'race_id': race})
            s['experience_result_cost']=actual['_cost']
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
        errors.append({'type': type(exc).__name__, 'failure_code': 'experience_execution_failed', 'stage': events[-1]['kind'] if events else 'startup',
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
                r.update(completed=bool(s['completed']),
                         settled=bool(s['scores']), semantic_audit=semantic, audit_error=audit_error,
                         memory_before_digest=digest(s.get('memory_before')),
                         memory_after_digest=digest(s['daily_memory_after']) if s.get('daily_memory_after') else None,
                         network_retries=max(0,len(s.get('provider_attempts',[]))-len(s.get('llm_trajectory',[]))))
                reports.append(r)
            except Exception as exc:
                reports.append({'run_id':run.state['run_id'], 'completed':False, 'report_error':type(exc).__name__})
            finally:
                suspend(run)
        write_json(out/'A2-results.json', reports)
        write_json(out/'A2-execution-status.json', {'finished':True,'attempted':attempted,
                   'completed_days':completed_days,'errors':errors,'full_experiment_started':bool(manifest.get('formal',False)),
                   'total_attempts':attempted})
        write_json(out/'A2-credential-scan.json',scan_no_credential(out))


def main():
    out = Path(sys.argv[1]).resolve()
    if not out.is_relative_to(PROJECT):
        raise Rejected('Copy-only output')
    try:
        execute(out)
    finally:
        from .report import build_report
        build_report(out)


if __name__ == '__main__':
    main()
