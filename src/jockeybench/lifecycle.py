"""The program-first trajectory: warmup, then per day predict -> settle -> reflect.

Model sessions (initial exploration, daily reflection, one repair per phase)
may change the strategy files; the framework alone executes ``predict`` and
seals predictions. Every step checkpoints, so an interrupted run resumes
without re-executing or re-billing completed work. Session limits end a
session, never erase sealed races.
"""

import copy

from .clock import cutoff, release
from .errors import BudgetExceeded, Rejected
from .ledger import book, micro, summary
from .protocol.delivery import SDK_CONVENTION, behaviour, silent_uniform
from .scoring import revenue_components, score, uniform, validate
from .storage import RUN_STOPS

SESSION_ENDING = ('Provider call budget', 'Provider turn budget', 'Provider context byte limit')
INITIAL_KEYS = (
    'ledger',
    'initial_clock',
    'validated_strategy',
    'valid_strategies',
    'accepted_workspace',
    'compute_jobs',
    'economic_sequence',
    'model_outputs',
    'provider_attempts',
    'provider_failures',
    'usage_history',
    'audit',
    'read_digests',
    'explicit_uniform',
    'strategy_trials',
    'last_validation',
    'sessions',
    'limit_events',
    'strategy_failures',
)


def usage():
    return {
        'calls': 0,
        'bytes': 0,
        'seconds': 0,
        'evidence_cells': 0,
        'model_calls': 0,
        'strategy_calls': 0,
        'strategy_evidence_cells': 0,
        'strategy_bytes': 0,
    }


def session(run, action, ident):
    """Run one model session; files persist only if it called save_strategy."""
    state = run.state
    record = state.setdefault('sessions', {}).setdefault(
        ident,
        {
            'phase': state['phase'],
            'index': state['index'],
            'start_usage': copy.deepcopy(state['usage']),
            'start_saves': state.get('session_saves', 0),
            'workspace': run.executor.snapshot(),
            'candidate_uniform': state.get('candidate_uniform', False),
        },
    )
    state.setdefault('accepted_workspace', state.get('validated_strategy') or record['workspace'])
    state['active_session'] = ident
    run.persist()
    try:
        action()
    except BudgetExceeded as exc:
        if not str(exc).startswith(SESSION_ENDING):
            raise
        if state.get('pending'):
            state.setdefault('abandoned_provider_requests', []).append({**state['pending'], 'reason': str(exc)})
            state['pending'] = None
        model_context = state.get('model_context')
        if model_context:
            model_context.update(done=True, final='', budget_stopped=True)
        state.setdefault('limit_events', []).append({'phase': state['phase'], 'index': state['index'], 'reason': str(exc)})
    saved = state.get('session_saves', 0) - record['start_saves']
    caps = run.service.phase_caps()
    caps = {'calls': caps['tool_calls'], 'bytes': caps['bytes'], 'evidence_cells': caps['evidence_cells']}
    spent = {k: state['usage'].get(k, 0) - record['start_usage'].get(k, 0) for k in caps}
    record.update(status='ended', saves=saved, spent=spent, ended_without_save=not saved and any(spent[k] >= 0.8 * caps[k] for k in caps))
    if not saved:
        run.executor.restore(record['workspace'])
        state['candidate_uniform'] = record['candidate_uniform']
    state.pop('active_session', None)
    run.persist()


def context(run, race):
    """The only argument a program receives besides the SDK."""
    return {
        'race_id': race,
        'seed': run.config.seed,
        'runner_numbers': [r['number'] for r in run.source.card(race, run.state['clock'])['runners']],
    }


def job(run, race):
    """Execute the current workspace's ``predict`` for one race and record its behaviour."""
    state = run.state
    state['strategy_job'] = race
    state['strategy_job_usage'] = {'calls': 0, 'evidence_cells': 0}
    audit_start = len(state['audit'])
    cpu_start = len(state.get('compute_jobs', []))
    prediction, numbers = None, []
    run.persist()
    try:
        ctx = context(run, race)
        numbers = ctx['runner_numbers']
        prediction = validate(run.executor.predict(ctx), numbers)
        return prediction
    finally:
        reads = [r for r in state['audit'][audit_start:] if r.get('from_sdk')]
        jobs = state.get('compute_jobs', [])[cpu_start:]
        cpu = sum(j.get('cpu_seconds', 0) for j in jobs) if all(j.get('status') == 'settled' for j in jobs) else None
        state['last_job_behaviour'] = {
            'race_id': race,
            **behaviour(
                prediction,
                numbers,
                evidence_cells=sum(r.get('evidence_cells', 0) for r in reads),
                sdk_calls=len(reads),
                sdk_misuse=[m for j in jobs for m in j.get('sdk_misuse', [])],
                cpu_seconds=cpu,
                charged_cells=sum(r.get('charged_cells', 0) for r in reads),
            ),
        }
        state.pop('strategy_job', None)
        state.pop('strategy_job_usage', None)
        run.persist()


def _silent_uniform_notice(record):
    if all(o['evidence_cells'] == 0 for o in record['observations']):
        diagnostic = 'On every validation race the program bought zero evidence. '
    else:
        diagnostic = 'Data arrived, but every runner received the same score. Check field paths and null handling against describe. '
    return {
        'classification': 'silent_uniform',
        'interface_valid': True,
        'protocol': 'economic-v1',
        'message': 'Every validation race returned uninformative output under this protocol. '
        + diagnostic
        + 'See last_validation for per-race evidence_cells, charged_cells, sdk_calls, sdk_misuse and uninformative_criteria. '
        + SDK_CONVENTION
        + 'Use save_strategy(mode="uniform") to explicitly choose uniform. You may also retain this valid program.',
        'last_validation': copy.deepcopy(record['observations']),
    }


def validate_candidate(run):
    """Accept the workspace as the new strategy if it passes interface checks on up to three support races.

    Validation never ranks candidates by accuracy. Uninformative or
    nondeterministic output keeps the program valid but offers one repair.
    """
    state = run.state
    old = state.get('validated_strategy')
    if state.pop('candidate_uniform', False):
        state.update(
            validation_needs_repair=False, validated_strategy=None, explicit_uniform=True, accepted_workspace=run.executor.snapshot()
        )
        run.persist()
        return True
    if not (run.executor.work / 'strategy.py').exists():
        state['accepted_workspace'] = run.executor.snapshot()
        return True
    candidate = run.executor.snapshot()
    if candidate == old or candidate == state.get('accepted_workspace'):
        return True
    accepted_workspace = state.get('accepted_workspace') or old
    if accepted_workspace and run.executor.program_digest(candidate) == run.executor.program_digest(accepted_workspace):
        state['accepted_workspace'] = candidate
        run.persist()
        return True  # NOTES.md changes carry memory, without a new program trial/version.
    state['validation_needs_repair'] = False
    record = state.setdefault('strategy_trials', {}).setdefault(candidate, {'status': 'pending', 'races': [], 'observations': []})
    state['last_validation'] = record['observations']
    if record['status'] == 'failed':
        state['validation_needs_repair'] = True
        state['validation_notice'] = {
            'stage': 'validation',
            'index': state['index'],
            'strategy': candidate,
            'error': record['error'],
            'details': record.get('details', {}),
        }
        if accepted_workspace:
            run.executor.restore(accepted_workspace)
        return False
    # Framework-selected support cases check the interface, never rank candidate accuracy.
    page, _ = run.source.history_page(state['initial_clock'], run.config.start, '', 3, scope='support')
    races = [s['card']['race_id'] for s in page['examples']]
    try:
        if not races:
            raise Rejected('No eligible initial support cases for strategy validation')
        for race in races:
            if race in record['races']:
                continue
            if record.get('running'):
                raise Rejected('Interrupted validation retained; candidate was not accepted')
            record['running'] = race
            run.persist()
            first = job(run, race)
            observation = copy.deepcopy(state['last_job_behaviour'])
            second = job(run, race)
            observation['nondeterministic_output'] = first != second
            observation['repeat_observation'] = copy.deepcopy(state['last_job_behaviour'])
            record['observations'].append(observation)
            record['races'].append(race)
            record.pop('running', None)
            run.persist()
        record['status'] = 'valid'
        record['delivery'] = 'silent_uniform' if silent_uniform(record['observations']) else 'predictor'
        state.update(explicit_uniform=False, validated_strategy=candidate, accepted_workspace=candidate)
        if candidate not in state['valid_strategies']:
            state['valid_strategies'].append(candidate)
        if record['delivery'] == 'silent_uniform':
            state['validation_needs_repair'] = True
            state['validation_notice'] = _silent_uniform_notice(record)
        if any(o.get('nondeterministic_output') for o in record['observations']):
            state['validation_needs_repair'] = True
            state['validation_notice'] = {
                'classification': 'nondeterministic_output',
                'interface_valid': True,
                'nondeterministic_output': True,
                'last_validation': copy.deepcopy(record['observations']),
                'message': "Validation repeats differed. Use context['seed'] for all randomness. This program remains valid if kept.",
            }
        run.persist()
        return True
    except Rejected as exc:
        if str(exc) in RUN_STOPS:
            raise
        record.update(status='failed', error=str(exc), details=getattr(exc, 'details', {}))
        if record.get('running') and state.get('last_job_behaviour', {}).get('race_id') == record['running']:
            record['observations'].append(copy.deepcopy(state['last_job_behaviour']))
        state.setdefault('strategy_failures', []).append(
            {
                'stage': 'validation',
                'phase': state['phase'],
                'index': state['index'],
                'strategy': candidate,
                'error': str(exc),
                'details': getattr(exc, 'details', {}),
            }
        )
        state['validation_needs_repair'] = True
        state['validation_notice'] = copy.deepcopy(state['strategy_failures'][-1])
        if accepted_workspace:
            run.executor.restore(accepted_workspace)
        run.persist()
        return False


def repair_candidate(run, accepted, key):
    """At most one repair session per phase, after a rejected or flagged candidate."""
    state = run.state
    request_key = key + '_request'
    if (
        hasattr(run.agent, 'repair_strategy')
        and not state.get(key)
        and (request_key in state or not accepted or state.get('validation_needs_repair'))
    ):
        if request_key not in state:
            state[request_key] = copy.deepcopy(state['validation_notice'])
            run.persist()
        # A pending provider request resumes this same repair conversation; it
        # is only marked done after the session actually finishes.
        previous_valid = state.get('validated_strategy')
        state['repair_session'] = True
        try:
            session(run, lambda: run.agent.repair_strategy(run.service, state[request_key]), key)
        finally:
            state.pop('repair_session', None)
        state[key] = True
        run.persist()
        repaired = validate_candidate(run)
        changed = state.get('explicit_uniform') or state.get('validated_strategy') != previous_valid
        if repaired and changed and state.get('session_saves', 0) > state['sessions'][key]['start_saves']:
            for failure in state.get('strategy_failures', []):
                if failure['stage'] == 'validation' and failure.get('phase') == state['phase'] and failure['index'] == state['index']:
                    failure['repaired'] = True
    state['validation_needs_repair'] = False
    run.persist()


def _merge_attempt_behaviour(observed, attempts):
    for attempt in attempts:
        for key in ('evidence_cells', 'charged_cells', 'sdk_calls'):
            observed[key] += attempt.get('behaviour', {}).get(key, 0)
        observed['sdk_misuse'].extend(attempt.get('behaviour', {}).get('sdk_misuse', []))


def predict_race(run, race):
    """Current strategy, else the previous valid one once, else uniform. Never re-executes a crashed attempt."""
    state = run.state
    job_state = state.setdefault('prediction_jobs', {}).setdefault(race, {'attempts': [], 'prediction': None})
    if job_state['prediction'] is not None:
        return job_state['prediction']
    ctx = context(run, race)
    current = state.get('validated_strategy')
    # Choosing uniform is a real stop action, not a reason to run a previous model.
    versions = ([current] + [v for v in reversed(state['valid_strategies']) if v != current][:1]) if current else []
    for version in versions:
        prior = next((a for a in job_state['attempts'] if a['strategy'] == version), None)
        if prior:
            if prior['status'] == 'started':
                prior.update(status='interrupted', error='Interrupted attempt retained without re-execution')
                state.setdefault('strategy_failures', []).append(
                    {'stage': 'prediction', 'index': state['index'], 'race_id': race, 'strategy': version, 'error': prior['error']}
                )
                run.persist()
            continue  # A crashed attempt is not executed or billed a second time.
        attempt = {
            'strategy': version,
            'status': 'started',
            'audit_start': len(state['audit']),
            'session_usage': copy.deepcopy(state['usage']),
        }
        job_state['attempts'].append(attempt)
        run.executor.restore((state.get('accepted_workspace') or version) if version == current else version)
        run.persist()
        try:
            prediction = job(run, race)
            attempt['status'] = 'ok'
            observed = copy.deepcopy(state['last_job_behaviour'])
            attempt['behaviour'] = observed
            delivery = (
                'execution_fallback' if version != current else ('silent_uniform' if observed['uninformative_output'] else 'predictor')
            )
            _merge_attempt_behaviour(observed, job_state['attempts'][:-1])
            job_state.update(prediction=prediction, selected_strategy=version, delivery=delivery, behaviour=observed)
            run.persist()
            return prediction
        except Rejected as exc:
            if str(exc) in RUN_STOPS:
                raise
            attempt.update(
                status='failed',
                error=str(exc),
                details=getattr(exc, 'details', {}),
                behaviour=copy.deepcopy(state.get('last_job_behaviour', {})),
            )
            state.setdefault('strategy_failures', []).append(
                {
                    'stage': 'prediction',
                    'index': state['index'],
                    'race_id': race,
                    'strategy': version,
                    'error': str(exc),
                    'details': getattr(exc, 'details', {}),
                }
            )
            run.persist()
    prediction = uniform(race, ctx['runner_numbers'], run.config.seed)
    observed = behaviour(prediction, ctx['runner_numbers'])
    _merge_attempt_behaviour(observed, job_state['attempts'])
    job_state.update(
        prediction=prediction,
        selected_strategy=None,
        fallback=bool(versions),
        behaviour=observed,
        delivery='execution_fallback' if versions else ('explicit_uniform' if state.get('explicit_uniform') else 'no_strategy'),
    )
    run.persist()
    return prediction


def public_day(run, batch):
    """The feedback a reflection session receives: that day's revenues, hits and the ledger."""
    scores = [run.state['scores'][r] for r in batch['races']]
    valid = [s for s in scores if s['status'] == 'ok']
    return {
        'ledger': summary(run.state, run.config),
        'items': [
            {
                'race_id': rid,
                'status': run.state['scores'][rid]['status'],
                'revenue_yen': run.state['scores'][rid]['score_revenue_yen'],
                **revenue_components(run.state['scores'][rid]),
            }
            for rid in batch['races']
        ],
        'hits': {
            'top1_hits': sum(s['top1'] for s in valid),
            'top1_n': len(valid),
            'top3_hits': sum(s['top3_hits'] for s in valid),
            'top3_n': sum(s['top3_n'] for s in valid),
        },
    }


def settle_day(run, batch):
    """Score and book every race of the day; outcomes become visible in one checkpoint."""
    state = run.state
    for race in batch['races']:
        sealed = state['submissions'][race]
        value = score(sealed['prediction'], run.source.settlements(race), run.config.tariff, delivery=sealed.get('delivery'))
        state['feedback'][race] = {'status': value['status'], 'revenue_yen': value['score_revenue_yen'], **revenue_components(value)}
        for category in ('revenue_log', 'revenue_hit'):
            book(state, category + ':' + race, category, micro(value[category + '_yen'] or 0), race_id=race)
        value['format_fallback'] = sealed['format_fallback']
        state['scores'][race] = value
        state['release_ledger'][race] = release(batch['day'])
    state.update(phase='reflect', clock=release(batch['day']), cursors={})
    run.persist()


def run_trajectory(run, stop_after=None, warmup_only=False):
    state = run.state
    if state['completed']:
        return run.report()
    run.preflight()
    # Recover only uncertain compute reservations, never silently erase expenditure.
    for compute in state.get('compute_jobs', []):
        if compute['status'] == 'reserved':
            compute['status'] = 'interrupted_cpu_unknown'
    state.pop('strategy_job', None)
    state.pop('strategy_job_usage', None)
    if state['phase'] == 'warmup':
        if not state.get('warmup_session_done'):
            session(run, lambda: run.agent.warmup(run.service), 'warmup')
            state['warmup_session_done'] = True
            run.persist()
        repair_candidate(run, validate_candidate(run), 'warmup_repair_done')
        state['initial_strategy'] = run.executor.snapshot()
        state['usage_history'].append({'phase': 'warmup', **state['usage']})
        state['initial_economic'] = {k: copy.deepcopy(state[k]) for k in INITIAL_KEYS if k in state}
        state.update(phase='predict', usage=usage(), cursors={})
        state.pop('model_context', None)
        run.persist()
    if warmup_only:
        return {'phase': state['phase'], 'initial_strategy': state['initial_strategy'], 'validated_strategy': state['validated_strategy']}
    count = 0
    while state['index'] < len(state['schedule']):
        run.source.verify()
        batch = state['schedule'][state['index']]
        if state['phase'] == 'predict':
            for race in batch['races']:
                if race in state['submissions']:
                    continue
                prediction = predict_race(run, race)
                job_state = state['prediction_jobs'][race]
                state['submissions'][race] = {
                    'prediction': prediction,
                    'strategy': job_state.get('selected_strategy'),
                    'clock': state['clock'],
                    'delivery': job_state['delivery'],
                    'behaviour': job_state['behaviour'],
                    'format_fallback': job_state.get('fallback', False),
                }
                run.persist()
                count += 1
                if stop_after is not None and count >= stop_after:
                    return {'paused_after_submissions': count, 'phase': 'predict'}
            settle_day(run, batch)
        if state['phase'] == 'reflect':
            if run.config.arm != 'S':
                # Finish and checkpoint the provider session before trialling candidate code.
                done_key = 'reflection_done_' + str(state['index'])
                if not state.get(done_key):
                    if state.get('accepted_workspace') or state.get('validated_strategy'):
                        run.executor.restore(state.get('accepted_workspace') or state['validated_strategy'])
                    session(run, lambda day=batch: run.agent.reflect(run.service, public_day(run, day)), done_key)
                    state[done_key] = True
                    run.persist()
                repair_candidate(run, validate_candidate(run), 'reflection_repair_done_' + str(state['index']))
            state['usage_history'].append(
                {'phase': 'day', 'day': batch['day'], 'strategy_digest': state['validated_strategy'], **state['usage']}
            )
            state['index'] += 1
            state.pop('model_context', None)
            state.update(phase='predict', usage=usage(), cursors={})
            if state['index'] < len(state['schedule']):
                next_clock = cutoff(state['schedule'][state['index']]['day'])
                if next_clock < state['clock']:
                    raise Rejected('Decision clock must not move backwards')
                state['clock'] = next_clock
            run.persist()
    state.update(completed=True, phase='completed')
    state['source_verified_after'] = run.source.manifest()
    if state['source_verified_after'] != state['binding']['source']:
        raise Rejected('Source or receipt changed')
    run.persist()
    return run.report()
