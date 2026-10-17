"""Trajectory reports and paired comparisons over booked results; never reads source data."""

import math
import random

from .errors import Rejected
from .ledger import MICRO, REVENUE_CATEGORIES, ledger, reconcile
from .protocol.delivery import report_delivery, unrepaired_failures
from .scoring import revenue_components, summarize

EXPENSE_CATEGORIES = ('exploration_data', 'execution_data', 'calls', 'compute')


def quantile(values, q):
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * q
    lo = int(position)
    return ordered[lo] + (ordered[min(lo + 1, len(ordered) - 1)] - ordered[lo]) * (position - lo)


def distribution(values, scale):
    return {
        'n': len(values),
        'quantiles': {str(q): quantile(values, q) for q in (0, 0.05, 0.25, 0.5, 0.75, 0.95, 1)},
        'share_below_minus_S': sum(v < -scale for v in values) / len(values) if values else None,
    }


def bootstrap(days, field, seed=0, samples=2000):
    """Percentile day-block bootstrap of a per-race mean (ratio of sums)."""
    days = [d for d in days if d['n'] > 0]
    n = sum(d['n'] for d in days)
    mean = sum(d[field] for d in days) / n if n else None
    result = {
        'mean_per_race': mean,
        'clusters': len(days),
        'seed': seed,
        'samples': samples,
        'method': 'percentile day-block bootstrap; resample whole days, ratio of sums',
        'ci95_per_race': None,
        'ci95_total': None,
    }
    if len(days) < 2:
        return result
    rng = random.Random(seed)
    draws = []
    for _ in range(samples):
        blocks = rng.choices(days, k=len(days))
        draws.append(sum(d[field] for d in blocks) / sum(d['n'] for d in blocks))
    result['ci95_per_race'] = [quantile(draws, 0.025), quantile(draws, 0.975)]
    result['ci95_total'] = [x * n for x in result['ci95_per_race']]
    return result


def interval(days):
    """Day-cluster sandwich standard error of the per-race mean net score."""
    n = sum(d['n'] for d in days)
    mean = sum(d['net_A_yen'] for d in days) / n if n else None
    if len(days) < 2 or not n:
        return {'mean': mean, 'se': None, 'ci95': None, 'clusters': len(days)}
    g = len(days)
    se = math.sqrt(g / (g - 1) * sum((d['net_A_yen'] - mean * d['n']) ** 2 for d in days)) / n
    return {
        'mean': mean,
        'se': se,
        'ci95': [mean - 1.96 * se, mean + 1.96 * se],
        'clusters': g,
        'method': 'day-cluster sandwich, asymptotic normal interval; few clusters are diagnostic only',
    }


def _mean(values):
    values = list(values)
    return sum(values) / len(values) if values else None


def _days(state, n):
    """Per-day revenue and cost; warmup cost is spread over all scored races."""
    entries = ledger(state)['entries']
    initial_cost = sum(e['amount_micro'] for e in entries if e['phase'] == 'warmup' and e['category'] not in REVENUE_CATEGORIES) / MICRO
    days = []
    for index, batch in enumerate(state['schedule']):
        scored = [state['scores'][r] for r in batch['races'] if r in state['scores']]
        if not scored:
            continue
        count = sum(r['status'] == 'ok' for r in scored)
        day_cost = (
            sum(
                e['amount_micro']
                for e in entries
                if e['phase'] != 'warmup' and e['category'] not in REVENUE_CATEGORIES and e['index'] == index
            )
            / MICRO
        )
        day_cost += initial_cost * count / n if n else 0
        day_revenue = sum(r.get('score_revenue_yen') or 0 for r in scored)
        days.append(
            {
                'day': batch['day'],
                'n': count,
                'cost_yen': day_cost,
                **{k: sum(revenue_components(r)[k] or 0 for r in scored) for k in ('revenue_log_yen', 'revenue_hit_yen')},
                'revenue_yen': day_revenue,
                'net_A_yen': day_revenue - day_cost,
            }
        )
    return days


def _calibration(rows):
    buckets = []
    for bucket in range(10):
        values = [r['smoothed_calibration'][bucket] for r in rows if r.get('smoothed_calibration')]
        count = sum(v['n'] for v in values)
        buckets.append(
            {
                'lower': bucket / 10,
                'upper': (bucket + 1) / 10,
                'n': count,
                'mean_probability': sum(v['sum_probability'] for v in values) / count if count else None,
                'observed_fraction': sum(v['sum_target'] for v in values) / count if count else None,
            }
        )
    return buckets


def report(run):
    state, config = run.state, run.config
    delivery = report_delivery(state)
    check = reconcile(state)
    entries = ledger(state)['entries']
    rows = list(state['scores'].values())
    ok_rows = [r for r in rows if r['status'] == 'ok']
    n = len(ok_rows)
    expenses = dict.fromkeys(EXPENSE_CATEGORIES, 0.0)
    for entry in entries:
        if entry['category'] not in REVENUE_CATEGORIES:
            expenses[entry['category']] = expenses.get(entry['category'], 0) + entry['amount_micro'] / MICRO
    costs, revenue = check['cost_micro'] / MICRO, check['revenue_micro'] / MICRO
    revenue_log = sum(e['amount_micro'] for e in entries if e['category'] == 'revenue_log') / MICRO
    revenue_hit = sum(e['amount_micro'] for e in entries if e['category'] == 'revenue_hit') / MICRO
    outputs, attempts = state.get('model_outputs', []), state.get('provider_attempts', [])
    unknown_cost = sum(o.get('cost_usd') is None for o in outputs) + max(0, len(attempts) - len(outputs))
    usd = sum(o.get('cost_usd') or 0 for o in outputs)
    unknown_cpu = sum(j['status'] != 'settled' for j in state.get('compute_jobs', []))
    days = _days(state, n)
    stats = summarize(rows)
    calibration = _calibration(rows)
    market = [r['private_gain_over_market'] for r in rows if r.get('private_gain_over_market') is not None]
    fallback_races = sum(s.get('format_fallback', False) for s in state['submissions'].values())
    day_cost = {d['day']: d['cost_yen'] / d['n'] for d in days if d['n']}
    result = {
        'run_id': state['run_id'],
        'protocol': config.protocol,
        'binding': state['binding'],
        'completed': state['completed'],
        'arm': config.arm,
        'agent': run.agent.identity,
        **delivery,
        'schedule': state['schedule'],
        'sealed_races': len(state['submissions']),
        'settled_races': len(rows),
        'planned_races': sum(len(b['races']) for b in state['schedule']),
        'scored_races': n,
        'tariff': state['binding']['config']['tariff'],
        'currency': 'score_yen_not_real_profit',
        'revenue_yen': revenue,
        'cost_yen': costs,
        'cost_breakdown': expenses,
        'revenue_log_yen': revenue_log,
        'revenue_hit_yen': revenue_hit,
        'log_only_net_yen': revenue_log - costs,
        'decomposition': {
            'gross_log_score': revenue_log,
            'gross_hit_score': revenue_hit,
            'evidence_fees': expenses['exploration_data'] + expenses['execution_data'],
            'call_fees': expenses['calls'],
            'cpu_fees': expenses['compute'],
            'exploration_data': expenses['exploration_data'],
            'execution_data': expenses['execution_data'],
        },
        'net_A_yen': revenue - costs,
        'net_A_per_scored_race': (revenue - costs) / n if n else None,
        'score_roi': (revenue - costs) / (100 * n) if n else None,
        'score_roi_basis': 'Scoring currency per nominal 100 units, not betting ROI',
        'net_B_yen': revenue - costs - usd * config.tariff.usd_to_yen if not unknown_cost else None,
        'llm_cost_usd_observed': usd,
        'llm_cost_unknown_attempts': unknown_cost,
        'llm_cost_basis': sorted({o.get('cost_basis', 'unavailable') for o in outputs}),
        'llm_requests': len(attempts),
        'model_responses': len(outputs),
        'prediction_model_requests': sum(a['phase'] == 'predict' for a in attempts),
        'scores': stats,
        'days': days,
        'day_cluster_interval': interval(days),
        'mean_smoothed_log_gain': sum(r.get('smoothed_log_gain', 0) for r in rows) / n if n else None,
        'mean_smoothed_brier': sum(r.get('smoothed_brier', 0) for r in rows) / n if n else None,
        'smoothed_calibration': calibration,
        'smoothed_ece': (
            sum(b['n'] * abs(b['mean_probability'] - b['observed_fraction']) for b in calibration if b['n'])
            / sum(b['n'] for b in calibration)
        )
        if any(b['n'] for b in calibration)
        else None,
        'gross_per_race_distribution': distribution([r['score_revenue_yen'] for r in ok_rows], config.tariff.score_scale),
        'log_per_race_distribution': distribution([revenue_components(r)['revenue_log_yen'] for r in ok_rows], config.tariff.score_scale),
        'hit_per_race_distribution': distribution([revenue_components(r)['revenue_hit_yen'] for r in ok_rows], config.tariff.score_scale),
        'day_block_intervals': {
            name: bootstrap(days, field, config.seed)
            for name, field in [
                ('net', 'net_A_yen'),
                ('gross_log', 'revenue_log_yen'),
                ('gross_hit', 'revenue_hit_yen'),
                ('gross', 'revenue_yen'),
            ]
        },
        'race_scores': {
            r: {
                'day': b['day'],
                'gross': state['scores'][r]['score_revenue_yen'],
                **revenue_components(state['scores'][r]),
                'net': state['scores'][r]['score_revenue_yen'] - day_cost[b['day']],
            }
            for b in state['schedule']
            for r in b['races']
            if state['scores'].get(r, {}).get('status') == 'ok'
        },
        'paired_cost_allocation': 'Warmup spread over all scored races; each day cost spread over that day’s scored races.',
        'purchased_evidence_cells': sum(e.get('units', 0) for e in entries if e['category'] in {'exploration_data', 'execution_data'}),
        'private_no_takeout_roi_after_protocol_cost': (
            (sum(r.get('nominal_net_yen', 0) for r in rows) - costs) / stats['at_risk_yen'] if stats['at_risk_yen'] else None
        ),
        'private_mean_gain_over_market': _mean(market),
        'limit_events': state.get('limit_events', []),
        'strategy_failures': state.get('strategy_failures', []),
        'provider_failures': state.get('provider_failures', 0),
        'recovered_mutations': state.get('recovered_mutations', []),
        'funding_stop': state.get('funding_stop'),
        'clean_trajectory': bool(state['completed'])
        and not (
            any(not e.get('recoverable') for e in state.get('limit_events', []))
            or unrepaired_failures(state)
            or unknown_cpu
            or state.get('recovered_mutations')
            or state.get('funding_stop')
            or delivery['delivery_totals']['silent_uniform']
            or delivery['delivery_totals']['no_strategy']
            or fallback_races
        ),
        'uniform_fallback_races': fallback_races,
        'initial_strategy': state['initial_strategy'],
        'validated_strategy_versions': len(state['valid_strategies']),
        'common_initial_from': state.get('common_initial_from'),
        'cpu_unknown_jobs': unknown_cpu,
        'cpu_policy': 'wait4 child CPU; bounded interruptions retain reservation; unbounded interruptions remain unknown, never measured as zero',
        'provisional_accounting': bool(unknown_cpu),
        'ledger_reconciliation': check,
        'isolation': state.get('isolation'),
        'usage': state['usage_history'],
        'current_usage': state['usage'],
        'source_unchanged': state.get('source_verified_after') == state['binding']['source']
        if state.get('source_verified_after')
        else None,
        'claim': 'Engineering/development replay. Smoothed scoring currency, not real profit or proof of learning. Historical 2025 is diagnostic only.',
    }
    run.storage.write('report.json.gz', result)
    return result


def compare(left, right):
    """Paired day-cluster comparison of two completed reports on common scored races."""
    if left.get('protocol') != 'economic-v1' or right.get('protocol') != 'economic-v1':
        raise Rejected('Economic comparison cannot mix protocols')
    if left.get('provisional_accounting') or right.get('provisional_accounting'):
        raise Rejected('Unresolved CPU accounting cannot enter a formal paired comparison')
    for key in ('source', 'code'):
        if left['binding'][key] != right['binding'][key]:
            raise Rejected('Comparisons require identical source and code')
    if not left['completed'] or not right['completed'] or left['tariff'] != right['tariff']:
        raise Rejected('Paired comparison requires completed runs and matching tariffs')
    a, b = left['race_scores'], right['race_scores']
    groups = {}
    for race in sorted(a.keys() & b.keys()):
        if a[race]['day'] != b[race]['day']:
            raise Rejected('Common race day differs')
        day = groups.setdefault(
            a[race]['day'], {'day': a[race]['day'], 'n': 0, 'net_A_yen': 0, 'revenue_yen': 0, 'revenue_log_yen': 0, 'revenue_hit_yen': 0}
        )
        day['n'] += 1
        day['net_A_yen'] += a[race]['net'] - b[race]['net']
        day['revenue_yen'] += a[race]['gross'] - b[race]['gross']
        day['revenue_log_yen'] += a[race]['revenue_log_yen'] - b[race]['revenue_log_yen']
        day['revenue_hit_yen'] += a[race]['revenue_hit_yen'] - b[race]['revenue_hit_yen']
    differences = list(groups.values())
    if not differences:
        raise Rejected('No common scored races')
    fields = {'net': 'net_A_yen', 'gross': 'revenue_yen', 'gross_log': 'revenue_log_yen', 'gross_hit': 'revenue_hit_yen'}

    def intervals(days):
        return {key: bootstrap(days, field) for key, field in fields.items()}

    reference = right['net_A_yen']
    return {
        'left': left['run_id'],
        'right': right['run_id'],
        'paired_difference': interval(differences),
        'revenues': {
            r['run_id']: {k: r[k] for k in ('revenue_yen', 'revenue_log_yen', 'revenue_hit_yen', 'log_only_net_yen')} for r in (left, right)
        },
        'ranking': [r['run_id'] for r in sorted([left, right], key=lambda r: (-r['net_A_yen'], -r['revenue_yen'], r['run_id']))],
        'day_block_intervals': {r['run_id']: intervals(r['days']) for r in (left, right)},
        'paired_day_block_intervals': intervals(differences),
        'common_scored_races': sum(d['n'] for d in differences),
        'efficiency_ratio': left['net_A_yen'] / reference if reference > 0 else None,
        'efficiency_note': 'Interpret as R2-relative efficiency only when right is the SDK R2 reference and net A is positive.',
        'common_initial_strategy': left['initial_strategy'] == right['initial_strategy'],
        'trajectory_variance': 'Requires independent trajectory clusters; one paired run does not estimate it.',
    }
