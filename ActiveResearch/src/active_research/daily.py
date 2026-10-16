"""A2 daily coordinator; existing agent, services, scoring and replay stay intact."""
import copy
import json
import sys
import time
import traceback
from pathlib import Path

from jrdb_lh.common import Rejected, cutoff, release, digest, file_hash
from jrdb_lh.config import Config
from jrdb_lh.runner import Run
from jrdb_lh.audit import replay
from jrdb_lh.ledger import summary
from active_research.runner import PROJECT, ActiveService, check_config, settle
from active_research.llm.runner import wall_limit, write_json, result_report, scan_no_credential
from active_research.adaptive.runner import predict, verify_semantics, MAX_TURNS, WALL_SECONDS, SafetyLimit
from active_research.adaptive.model import AdaptiveAgent, load_settings, credential, model_cost, PROMPT
from active_research.daily_session import DailyReflectionSession, DailyReflectionFailed, load_budget, verify_session
from active_research.adaptive.domains import neutral, validate_policy, reflection_tool, parse_reflection, trajectory, allocation


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


def lifecycle(days, memory, predict_one, release_day, reflect_day, emit):
    """No result callback is reachable until every selected race of the day sealed."""
    validate_schedule(days)
    for batch in days:
        frozen = copy.deepcopy(memory)
        emit('day_start', day=batch['day'], memory=digest(frozen))
        sealed = []
        for race in batch['races']:
            prediction = predict_one(batch['day'], race, copy.deepcopy(frozen))
            sealed.append(prediction)
            emit('sealed', day=batch['day'], race_id=race, memory=digest(frozen))
        emit('release_gate', day=batch['day'], clock=release(batch['day']), races=batch['races'])
        outcomes = release_day(batch['day'], sealed)
        candidate = reflect_day(batch['day'], sealed, outcomes, copy.deepcopy(frozen))
        validate_policy(candidate, batch['races'][-1])
        memory = copy.deepcopy(candidate)
        emit('memory_commit', day=batch['day'], memory=digest(memory))
    return memory


def daily_reflect(day, rows, memory, out, settings, budget, experiment_id):
    session = DailyReflectionSession(out, experiment_id, day, settings, budget)
    record = session.run(rows, memory)
    payload = json.loads((session.path/'A2-input.json').read_text())
    verify_session(session.state, payload)
    write_json(out / ('A2-daily-reflection-input-' + day + '.json'), payload)
    write_json(out / ('A2-daily-reflection-' + day + '.json'), record)
    return record['new_policy']


def execute(out):
    manifest = json.loads((out/'A2-manifest.json').read_text())
    config = Config.load(out/'A2-config.json')
    check_config(config)  # Each isolated race retains the original one-race service contract.
    settings = load_settings(out/'A2-provider.json')
    if 'A2-reflection-budget.json' not in manifest['files']:
        raise Rejected('Daily session budget must be explicitly frozen in the manifest')
    budget = load_budget(out/'A2-reflection-budget.json')
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
        if kind == 'memory_commit':
            completed_days.append(data['day'])

    def prediction(day, race, memory):
        nonlocal attempted
        attempted += 1
        ident = 'A2-daily-' + str(attempted).zfill(3)
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
                 A2_daily={'manifest': digest(manifest), 'memory_before': digest(memory)})
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
            s['reflection_result_cost'] = actual['_cost']
            row = {'race_id': race, 'release_clock': s['release_ledger'][race],
                   'final_prediction': s['submissions'][race]['prediction'],
                   'actual_result': {k:v for k,v in actual.items() if not k.startswith('_')},
                   'prediction_score': s['scores'][race]['score_revenue_yen'],
                   'prediction_phase_cost': s['prediction_phase_cost'],
                   'ledger': summary(s,config), 'model_cost_so_far': model_cost(s),
                   'domain_trajectory': trajectory(s), 'allocation': allocation(s),
                   'stopping_mode': s['submission_mode']}
            rows.append(row)
            s['phase'] = 'awaiting_daily_reflection'
            run.persist()
            write_json(out/(run.state['run_id']+'-released-result.json'), row)
            emit('result_released', day=day, race_id=race, clock=release(day))
            suspend(run)
        return rows

    def reflection(day, sealed, outcomes, memory):
        emit('reflection_start', day=day, clock=release(day), races=[x['race_id'] for x in outcomes])
        # Finish and audit races before creating an independent day session.
        for i, run in enumerate(sealed):
            run = activate(run)
            sealed[i] = run
            run.state['phase'] = 'completed'
            run.persist()
            finish(run)
        updated = daily_reflect(day, outcomes, memory, out, settings, budget, digest(manifest))
        write_json(out/'A2-policy.json', updated)
        return updated

    try:
        lifecycle(manifest['schedule'], neutral(), prediction, results, reflection, emit)
    except BaseException as exc:
        errors.append({'type': type(exc).__name__, 'failure_code': 'daily_reflection_failed' if isinstance(exc, DailyReflectionFailed) else 'execution_failed', 'stage': events[-1]['kind'] if events else 'startup',
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
        from active_research.daily_report import build_report
        build_report(out)


if __name__ == '__main__':
    main()
