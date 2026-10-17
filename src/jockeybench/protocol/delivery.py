"""How a race's sealed prediction was delivered, independent of its accuracy.

Delivery categories: ``predictor`` (the current validated program),
``silent_uniform`` (it ran but every runner got the same probability),
``explicit_uniform`` (the agent chose uniform), ``no_strategy`` and
``execution_fallback`` (an older valid program or uniform after a failure).
"""

from collections import Counter

SDK_CONVENTION = (
    'sdk.call(tool_name, **arguments) returns the same JSON as the direct tool. '
    'sdk.history(...) is a paging generator. There are no per-tool methods such as sdk.card. '
    'SDK data calls inside predict are the only way a strategy sees the race data; context supplies only identifiers, runner numbers and seed. '
)
SAVE_WARNING = 'Save your strategy and NOTES.md now; a session that ends without save_strategy keeps the previous strategy.'
DELIVERIES = ('predictor', 'silent_uniform', 'explicit_uniform', 'no_strategy', 'execution_fallback')


def session_memory(service):
    """What a new session sees of the previous ones: saved files, notes and the last validation."""
    value = {'current_strategy_files': {}, 'truncated': [], 'notes': None, 'last_validation': service.state.get('last_validation', [])}
    if service.executor is not None:
        value.update(service.executor.session_files())
    return value


def behaviour(prediction, numbers, evidence_cells=0, sdk_calls=0, sdk_misuse=None, cpu_seconds=0, charged_cells=0):
    probabilities = (prediction or {}).get('probabilities')
    equal = bool(probabilities) and len(set(probabilities.values())) == 1
    ranking = (prediction or {}).get('ranking')
    criteria = ['probabilities_exactly_uniform'] if equal else []
    return {
        'evidence_cells': evidence_cells,
        'charged_cells': charged_cells,
        'sdk_calls': sdk_calls,
        'probabilities_exactly_uniform': equal,
        'ranking_is_number_order': ranking == sorted(numbers, key=int),
        'ranking_is_context_order': ranking == numbers,
        'uninformative_output': bool(criteria),
        'uninformative_criteria': criteria,
        'sdk_misuse': sdk_misuse or [],
        'cpu_seconds': cpu_seconds,
    }


def silent_uniform(observations):
    return bool(observations) and all(o['uninformative_output'] for o in observations)


def report_delivery(state):
    submissions = list(state['submissions'].values())
    totals = {name: sum(s.get('delivery') == name for s in submissions) for name in DELIVERIES}
    counts = Counter()
    for s in submissions:
        counts.update(
            {
                name: bool(s.get('behaviour', {}).get(key))
                for name, key in [
                    ('uniform_output_races', 'probabilities_exactly_uniform'),
                    ('number_order_ranking_races', 'ranking_is_number_order'),
                ]
            }
        )
    return {
        'delivery_totals': totals,
        'predictor_delivered': totals['predictor'] > 0,
        'nondeterministic_validation_races': sum(
            o.get('nondeterministic_output', False)
            for trial in state.get('strategy_trials', {}).values()
            for o in trial.get('observations', [])
        ),
        **{k: counts[k] for k in ('uniform_output_races', 'number_order_ranking_races')},
        'zero_evidence_prediction_races': sum(s.get('behaviour', {}).get('evidence_cells', 0) == 0 for s in submissions),
        'sdk_misuse_events': sum(m['count'] for j in state.get('compute_jobs', []) for m in j.get('sdk_misuse', [])),
        'sessions_ended_without_save': sum(s.get('ended_without_save', False) for s in state.get('sessions', {}).values()),
        'recoverable_limit_counts': dict(Counter(e['cap'] for e in state.get('limit_events', []) if e.get('recoverable'))),
        'race_delivery': {rid: {'delivery': s.get('delivery'), **s.get('behaviour', {})} for rid, s in state['submissions'].items()},
    }


def unrepaired_failures(state):
    return [f for f in state.get('strategy_failures', []) if not (f['stage'] == 'validation' and f.get('repaired'))]
